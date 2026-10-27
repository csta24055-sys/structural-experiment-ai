"""AIプロバイダの生成と、AI出力の検証。"""
from __future__ import annotations

from typing import Any

from ..classify import verify_evidence
from ..models import CATEGORIES, Item
from .base import AIError, AIImage, AIProvider, AIRequest, AIResponse


def make_provider(cfg, allow_external_flag: bool = False) -> tuple[AIProvider | None, str]:
    """(provider or None, 理由)。外部送信は ai.allow_external か --allow-external-ai が必要。"""
    ai = cfg.data["ai"]
    p = ai["provider"]
    if p == "none":
        return None, "ai.provider=none（ローカル抽出のみ）"
    if p == "mock":
        from .mock_provider import MockProvider
        return MockProvider(), "mock"
    if p == "anthropic":
        if not (ai.get("allow_external") or allow_external_flag):
            return None, ("外部AIへの送信が許可されていません（ai.allow_external: true または "
                          "--allow-external-ai で明示的に許可してください）")
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(model=ai["model"], effort=ai["effort"], max_tokens=ai["max_output_tokens"],
                                 use_fallbacks=ai["use_fallbacks"], max_image_edge=ai["max_image_edge"]), "ok"
    if p == "claude_code_queue":  # 候補 G5（既定では使わない）: Claude Code（定額の契約）で解析する受け渡し口
        if not (ai.get("allow_external") or allow_external_flag):
            return None, "外部AIへの送信が許可されていません（Claude Code も Anthropic に送るので許可が必要）"
        from pathlib import Path
        from .queue_provider import QueueProvider
        return QueueProvider(Path(ai.get("queue_dir") or "work/queue"), model_label=f"claude-code:{ai['model']}",
                             max_edge=int(ai.get("max_image_edge", 2576))), "ok"
    raise AIError(f"未知のプロバイダ: {p}", retryable=False)


def make_escalation_provider(cfg, allow_external_flag: bool = False) -> AIProvider | None:
    """複雑な部分の読み直しに使う上位モデル（ai.escalation_model）。未設定・送信不可なら None。"""
    ai = cfg.data["ai"]
    model = ai.get("escalation_model")
    if not model or ai["provider"] != "anthropic" or not (ai.get("allow_external") or allow_external_flag):
        return None
    from .anthropic_provider import AnthropicProvider
    return AnthropicProvider(model=model, effort=ai["effort"], max_tokens=ai["max_output_tokens"],
                             use_fallbacks=ai["use_fallbacks"], max_image_edge=ai["max_image_edge"])


def items_from_response(data: dict[str, Any], corpus: str, *, analyzer: str, page: int | None,
                        t_start: float | None, t_end: float | None) -> tuple[list[Item], list[dict]]:
    """AI出力を検証して Item に変換する。AIの出力は無条件に信用しない:
    - スキーマ外の値は捨てる
    - 根拠（evidence）をローカル抽出テキストと照合し、結果を verification に記録
    - 推測（ai_inference）は先生の指示として扱わない
    - 根拠が原文に見つからない「資料記載」は推測扱いに格下げしない代わりに要確認として明示
    """
    items: list[Item] = []
    rejected: list[dict] = []
    for raw in (data or {}).get("items", []) or []:
        try:
            cat = raw.get("category")
            origin = raw.get("origin")
            stmt = str(raw.get("statement") or "").strip()
            if cat not in CATEGORIES or origin not in ("document_text", "speech", "screen", "ai_inference") \
                    or not stmt:
                rejected.append({"raw": raw, "reason": "schema"})
                continue
            ev = str(raw.get("evidence") or "").strip()
            ver = "not_applicable" if origin == "ai_inference" else verify_evidence(ev, corpus)
            instr = bool(raw.get("is_instructor_instruction")) and origin in ("document_text", "speech", "screen")
            if instr and ver == "evidence_not_found":
                instr = False  # 原文で確認できない指示は「先生の指示」として確定しない
            ts = raw.get("time_sec")
            ts = float(ts) if isinstance(ts, (int, float)) else None
            if ts is not None and t_start is not None and t_end is not None and not (t_start - 1 <= ts <= t_end + 1):
                ts = None  # 区間外の時刻は採用しない
            conf = raw.get("confidence") if raw.get("confidence") in ("high", "medium", "low") else "low"
            items.append(Item(
                category=cat, statement=stmt[:2000], origin=origin, evidence=ev[:2000], confidence=conf,
                is_instructor_instruction=instr, verification=ver, page=page,
                t_start=ts if ts is not None else t_start, t_end=ts if ts is not None else t_end,
                analyzer=analyzer,
            ))
        except Exception as e:  # noqa: BLE001
            rejected.append({"raw": raw, "reason": str(e)})
    for u in (data or {}).get("unreadable", []) or []:
        if str(u).strip():
            items.append(Item(category="unknowns_contradictions", statement=f"読み取り不能: {str(u)[:500]}",
                              origin="unreadable", confidence="low", verification="not_applicable",
                              page=page, t_start=t_start, t_end=t_end, analyzer=analyzer))
    return items, rejected


__all__ = ["AIError", "AIImage", "AIProvider", "AIRequest", "AIResponse", "make_provider",
           "items_from_response"]
