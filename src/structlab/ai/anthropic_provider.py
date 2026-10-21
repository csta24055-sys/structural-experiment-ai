"""Anthropic Claude による解析。APIキーは環境変数 ANTHROPIC_API_KEY から読む。"""
from __future__ import annotations

import base64
import io
import json

from PIL import Image

from .base import AIError, AIProvider, AIRequest, AIResponse
from .prompts import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_user_text


MAX_IMAGE_BYTES = 3_700_000  # API の上限（1枚 5MB、base64 で 4/3 倍になる）に収める


def encode_image(path, max_edge: int, lossless: bool = False) -> tuple[str, str]:
    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > max_edge:
            im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        if lossless:
            im.save(buf, format="WEBP", lossless=True, method=4)
            if buf.tell() <= MAX_IMAGE_BYTES:
                return "image/webp", base64.standard_b64encode(buf.getvalue()).decode("ascii")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=97, subsampling=0)  # 上限を超える場合だけ、ほぼ劣化のない JPEG
            return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode("ascii")
        im.save(buf, format="JPEG", quality=85)
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode("ascii")


STREAM_ABOVE = 16000


class AnthropicProvider(AIProvider):
    name = "anthropic"
    external = True

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", max_tokens: int = 8000,
                 use_fallbacks: bool = True, max_image_edge: int = 1568, client=None):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise AIError("anthropic ライブラリがありません: pip install anthropic", retryable=False) from e
        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_fallbacks = use_fallbacks
        self.max_image_edge = max_image_edge

    def _content(self, req: AIRequest) -> list[dict]:
        content: list[dict] = []
        for img in req.images:
            media, data = encode_image(img.path, self.max_image_edge, getattr(img, "lossless", False))
            content.append({"type": "text", "text": f"[画像: {img.label}]"})
            block = {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
            if getattr(img, "cache", False):
                block["cache_control"] = {"type": "ephemeral"}
            content.append(block)
        if req.schema is not None:  # 意味理解層: 本文は呼び出し側で組み立て済み
            content.append({"type": "text", "text": req.text})
        else:
            content.append({"type": "text", "text": build_user_text(req.task, req.source_label, req.text)})
        return content

    def _params(self, req: AIRequest) -> dict:
        """Batch API 用の1件分の依頼。指示文はキャッシュ（同じ指示の2件目以降は入力が安くなる）。"""
        return dict(
            model=self.model,
            max_tokens=req.max_tokens or self.max_tokens,
            system=[{"type": "text", "text": req.system or SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": self._content(req)}],
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": req.schema or OUTPUT_SCHEMA}},
        )

    def analyze_batch(self, reqs: list[AIRequest], *, poll_seconds: int = 60, log=print,
                      known: dict | None = None, on_created=None) -> list:
        """Message Batches API でまとめて送る（料金は通常の 50%）。各依頼の AIResponse か例外を順に返す。
        結果は通常 1 時間以内、最長 24 時間。サーバー側のモデル切り替え（fallbacks）は Batch では使えない。"""
        import time
        out: list = [AIError("未処理", retryable=True)] * len(reqs)
        chunk = 500  # 1回の送信を小さく保つ（上限 256MB）
        for base in range(0, len(reqs), chunk):
            part = reqs[base:base + chunk]
            if known and base in known:  # 前回の実行で送信済み（時間切れなどで中断）: 送り直さず結果を取りに行く
                batch = self.client.messages.batches.retrieve(known[base])
                log(f"  Batch {batch.id}: 送信済みの結果を待つ")
            else:
                batch = self.client.messages.batches.create(
                    requests=[{"custom_id": f"r{base + i}", "params": self._params(r)} for i, r in enumerate(part)])
                log(f"  Batch {batch.id}: {len(part)} 件")
                if on_created:
                    on_created(base, batch.id)
            while True:
                batch = self.client.messages.batches.retrieve(batch.id)
                if batch.processing_status == "ended":
                    break
                time.sleep(poll_seconds)
            for item in self.client.messages.batches.results(batch.id):
                i = int(item.custom_id[1:])
                r = item.result
                if r.type != "succeeded":
                    out[i] = AIError(f"Batch の結果: {r.type} {getattr(getattr(r, 'error', None), 'type', '')}",
                                     retryable=r.type != "errored")
                    continue
                try:
                    out[i] = self._response(r.message)
                except AIError as e:
                    out[i] = e
        return out

    def _response(self, resp) -> AIResponse:
        usage = getattr(resp, "usage", None)
        in_tok = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        out_tok = getattr(usage, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            raise AIError("AIが解析を拒否しました（refusal）", retryable=False)
        if resp.stop_reason == "max_tokens":
            raise AIError("出力が上限で途切れました（ai.max_output_tokens を増やしてください）", retryable=False)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise AIError(f"AI出力がJSONではありません: {text[:200]}", retryable=True) from e
        r = AIResponse(data=data, model=getattr(resp, "model", self.model), input_tokens=in_tok,
                       output_tokens=out_tok, raw_text=text)
        r.cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
        return r

    def count_input_tokens(self, req: AIRequest) -> int:
        """入力トークン数を数える（推論はしないので課金なし）。見積もりの実測に使う。"""
        p = self._params(req)
        return self.client.messages.count_tokens(model=p["model"], system=p["system"], messages=p["messages"],
                                                 output_config=p["output_config"]).input_tokens

    def analyze(self, req: AIRequest) -> AIResponse:
        a = self._anthropic
        kwargs = dict(
            model=self.model,
            max_tokens=req.max_tokens or self.max_tokens,
            system=req.system or SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._content(req)}],
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": req.schema or OUTPUT_SCHEMA}},
        )
        if self.use_fallbacks:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        api = self.client.beta.messages if self.use_fallbacks else self.client.messages
        try:
            if kwargs["max_tokens"] > STREAM_ABOVE:  # 長い出力は時間切れを避けるためストリーミングで受け取る
                with api.stream(**kwargs) as st:
                    resp = st.get_final_message()
            else:
                resp = api.create(**kwargs)
        except a.AuthenticationError as e:
            raise AIError(f"APIキーが無効です: {e}", retryable=False) from e
        except a.PermissionDeniedError as e:
            raise AIError(f"APIの権限がありません: {e}", retryable=False) from e
        except a.BadRequestError as e:
            raise AIError(f"リクエストが不正です: {e}", retryable=False) from e
        except a.RateLimitError as e:
            raise AIError(f"レート制限: {e}", retryable=True) from e
        except a.APIStatusError as e:
            raise AIError(f"APIエラー {e.status_code}: {e}", retryable=e.status_code >= 500) from e
        except a.APIConnectionError as e:
            raise AIError(f"接続エラー: {e}", retryable=True) from e

        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            raise AIError("AIが解析を拒否しました（refusal）", retryable=False)
        if resp.stop_reason == "max_tokens":
            raise AIError("出力が上限で途切れました（ai.max_output_tokens を増やしてください）", retryable=False)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise AIError(f"AI出力がJSONではありません: {text[:200]}", retryable=True) from e
        return AIResponse(data=data, model=getattr(resp, "model", self.model), input_tokens=in_tok,
                          output_tokens=out_tok, raw_text=text)
