"""Anthropic Claude による解析。APIキーは環境変数 ANTHROPIC_API_KEY から読む。"""
from __future__ import annotations

import base64
import io
import json

from PIL import Image

from .base import AIError, AIProvider, AIRequest, AIResponse
from .prompts import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_user_text


#: キャッシュ（既定の 5 分）。Batch の中ではキャッシュに当たるかは保証されないので、外れたときの割増が小さい
#: 5 分（書き込み 1.25 倍）にする。1 時間（書き込み 2 倍）は当たる割合が高いと実測で分かってから検討する。内容には影響しない
CACHE = {"type": "ephemeral"}
#: Batch の中で同じ先頭部分（指示文・基準画像・資料全体の理解など）を持つ依頼がこの件数以上あるときは、公式の推奨どおり
#: 1件だけを先に送ってキャッシュを書き（1 時間）、終わってから残りを送る（2段階）。送る内容は同じで、料金だけが変わる。
#: 件数が少ないと 1 時間の書き込み（2 倍）の割増が読み出しの節約を上回るので、少ない場合は従来どおり 1 回で送る
#: （試算: docs/COSTS.md「キャッシュの先書き」）
PRIME_MIN_GROUP = 6
#: キャッシュされる最小の長さ（Opus 5.5 は 512 トークン）。これより短い先頭部分は先書きしても意味がない
CACHE_MIN_TOKENS = 512
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
PHASE_STRIDE = 1_000_000


def _cache_blocks(p: dict) -> list[dict]:
    blocks = [b for b in p.get("system") or [] if isinstance(b, dict)]
    for m in p.get("messages", []):
        blocks += [b for b in m.get("content", []) if isinstance(b, dict)]
    return [b for b in blocks if "cache_control" in b]


def prefix_key(p: dict) -> str | None:
    """キャッシュされる先頭部分（最後のキャッシュの区切りまで）の指紋。区切りが無ければ None。
    API のキャッシュは、モデル・出力の設定・指示文・区切りまでの内容が完全に同じときだけ使われる。"""
    import hashlib
    seq: list = [p.get("model"), p.get("output_config")]
    blocks = list(p.get("system") or [])
    for m in p.get("messages", []):
        blocks += list(m.get("content", []))
    last = max((i for i, b in enumerate(blocks) if isinstance(b, dict) and "cache_control" in b), default=None)
    if last is None:
        return None
    for b in blocks[:last + 1]:
        seq.append({k: v for k, v in b.items() if k != "cache_control"})
    return hashlib.sha256(json.dumps(seq, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prefix_tokens(p: dict, req: AIRequest | None = None) -> int:
    """キャッシュされる先頭部分の大まかなトークン数（文字数 + 画像は公式の式 ⌈幅/28⌉×⌈高さ/28⌉）。
    最小の長さ（512）を超えるかの判断にだけ使う。"""
    blocks = list(p.get("system") or [])
    for m in p.get("messages", []):
        blocks += list(m.get("content", []))
    last = max((i for i, b in enumerate(blocks) if isinstance(b, dict) and "cache_control" in b), default=-1)
    n = len(json.dumps((p.get("output_config") or {}).get("format", {}), ensure_ascii=False)) // 3
    imgs = iter(req.images) if req is not None else iter(())
    for b in blocks[:last + 1]:
        if b.get("type") == "text":  # 日本語は 1 文字 ≒ 1 トークン、英数字は 3〜4 文字 ≒ 1 トークン（少なめに数える）
            s = b.get("text", "")
            ascii_n = sum(1 for ch in s if ord(ch) < 128)
            n += (len(s) - ascii_n) + ascii_n // 4
        elif b.get("type") == "image":
            im = next(imgs, None)
            n += image_tokens_official(im.path) if im is not None else 1500
    return n


def image_tokens_official(path, max_edge: int = 2576) -> int:
    from .tokens import image_tokens
    return image_tokens(path, max_edge)


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
        k = int(getattr(req, "cache_prefix_chars", 0) or 0)
        lead = req.schema is not None and getattr(req, "prefix_before_images", False) and 0 < k < len(req.text)
        if lead:  # 候補の並べ方: 共通の先頭部分（指示文・資料全体の理解）→ 画像 → 残りの本文
            content.append({"type": "text", "text": req.text[:k], "cache_control": dict(CACHE)})
        for img in req.images:
            media, data = encode_image(img.path, self.max_image_edge, getattr(img, "lossless", False))
            content.append({"type": "text", "text": f"[画像: {img.label}]"})
            block = {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
            if getattr(img, "cache", False):
                block["cache_control"] = dict(CACHE)
            content.append(block)
        if lead:
            content.append({"type": "text", "text": req.text[k:]})
        elif req.schema is not None:  # 意味理解層: 本文は呼び出し側で組み立て済み
            if 0 < k < len(req.text):
                content.append({"type": "text", "text": req.text[:k], "cache_control": dict(CACHE)})
                content.append({"type": "text", "text": req.text[k:]})
            else:
                content.append({"type": "text", "text": req.text})
        else:
            content.append({"type": "text", "text": build_user_text(req.task, req.source_label, req.text)})
        return content

    def _params(self, req: AIRequest, ttl: str | None = None) -> dict:
        """Batch API 用の1件分の依頼。指示文はキャッシュ（同じ指示の2件目以降は入力が安くなる）。
        ttl="1h" は全てのキャッシュの区切りを 1 時間にする（内容は同じ）。"""
        p = self._params_5m(req)
        ttl = ttl or getattr(req, "cache_ttl", "") or None
        if ttl:
            for blk in _cache_blocks(p):
                blk["cache_control"] = {"type": "ephemeral", "ttl": ttl}
        return p

    def _params_5m(self, req: AIRequest) -> dict:
        return dict(
            model=self.model,
            max_tokens=req.max_tokens or self.max_tokens,
            system=[{"type": "text", "text": req.system or SYSTEM_PROMPT, "cache_control": dict(CACHE)}],
            messages=[{"role": "user", "content": self._content(req)}],
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": req.schema or OUTPUT_SCHEMA}},
        )

    def batch_plan(self, reqs: list[AIRequest]) -> tuple[list[list[int]], list[dict]]:
        """Batch で送る順序と各依頼の params を決める（API は呼ばない）。
        同じキャッシュの先頭部分を持つ依頼が PRIME_MIN_GROUP 件以上あれば、その先頭部分を 1 時間のキャッシュにし、
        各まとまりの 1 件目だけを先の段階で送る（公式の推奨: Batch では同時に処理されるのでキャッシュは保証されない。
        1 件で先に書いてから残りを送ると当たりやすい）。戻り値: 段階ごとの依頼の番号、各依頼の params。"""
        params = [self._params(r) for r in reqs]
        groups: dict[str, list[int]] = {}
        for i, p in enumerate(params):
            k = prefix_key(p)
            if k is not None and prefix_tokens(p, reqs[i]) >= CACHE_MIN_TOKENS:
                groups.setdefault(k, []).append(i)
        first: list[int] = []
        for idx in groups.values():
            if len(idx) >= PRIME_MIN_GROUP:
                for i in idx:
                    for blk in _cache_blocks(params[i]):
                        blk["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
                first.append(idx[0])
        if not first:
            return [list(range(len(reqs)))], params
        fs = set(first)
        return [sorted(first), [i for i in range(len(reqs)) if i not in fs]], params

    def analyze_batch(self, reqs: list[AIRequest], *, poll_seconds: int = 60, log=print,
                      known: dict | None = None, on_created=None) -> list:
        """Message Batches API でまとめて送る（料金は通常の 50%）。各依頼の AIResponse か例外を順に返す。
        結果は通常 1 時間以内、最長 24 時間。サーバー側のモデル切り替え（fallbacks）は Batch では使えない。
        キャッシュの先書き（batch_plan）をする場合は 2 段階で送る。known / on_created の番号は
        「段階 × PHASE_STRIDE + 段階内の位置」（1 段階のときは従来と同じ番号）。"""
        out: list = [AIError("未処理", retryable=True)] * len(reqs)
        phases, params = self.batch_plan(reqs)
        if len(phases) > 1:
            log(f"  キャッシュの先書き: 先に {len(phases[0])} 件、残り {len(phases[1])} 件")
        for ph, idx in enumerate(phases):
            self._run_batch(idx, params, out, ph * PHASE_STRIDE, poll_seconds, log, known, on_created)
        return out

    def _run_batch(self, idx: list[int], params: list[dict], out: list, offset: int, poll_seconds: int, log,
                   known: dict | None, on_created) -> None:
        import time
        chunk = 500  # 1回の送信を小さく保つ（上限 256MB）
        for pos in range(0, len(idx), chunk):
            part = idx[pos:pos + chunk]
            base = offset + pos
            if known and base in known:  # 前回の実行で送信済み（時間切れなどで中断）: 送り直さず結果を取りに行く
                batch = self.client.messages.batches.retrieve(known[base])
                log(f"  Batch {batch.id}: 送信済みの結果を待つ")
            else:
                batch = self.client.messages.batches.create(
                    requests=[{"custom_id": f"r{i}", "params": params[i]} for i in part])
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
        r.cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
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
