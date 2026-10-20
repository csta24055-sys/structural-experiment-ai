"""Anthropic Claude による解析。APIキーは環境変数 ANTHROPIC_API_KEY から読む。"""
from __future__ import annotations

import base64
import io
import json

from PIL import Image

from .base import AIError, AIProvider, AIRequest, AIResponse
from .prompts import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_user_text


def encode_image(path, max_edge: int) -> tuple[str, str]:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode("ascii")


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
            media, data = encode_image(img.path, self.max_image_edge)
            content.append({"type": "text", "text": f"[画像: {img.label}]"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
        if req.schema is not None:  # 意味理解層: 本文は呼び出し側で組み立て済み
            content.append({"type": "text", "text": req.text})
        else:
            content.append({"type": "text", "text": build_user_text(req.task, req.source_label, req.text)})
        return content

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
        try:
            if self.use_fallbacks:
                resp = self.client.beta.messages.create(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            else:
                resp = self.client.messages.create(**kwargs)
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
