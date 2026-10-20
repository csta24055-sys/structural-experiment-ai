"""テスト用の決定的なAIプロバイダ（外部送信なし）。

ローカル抽出テキストの先頭行を document_text として返し、
さらに意図的に「根拠が原文に存在しない」推測項目を1件混ぜて、検証機構を試せるようにする。
テキストに "[[AI_FAIL]]" が含まれると一時エラーを発生させる（再試行テスト用）。
"""
from __future__ import annotations

from .base import AIError, AIProvider, AIRequest, AIResponse


class MockProvider(AIProvider):
    name = "mock"
    external = False
    model = "mock-1"

    def __init__(self, fail_times: int = 0):
        self.calls: list[AIRequest] = []
        self.fail_times = fail_times

    def analyze(self, req: AIRequest) -> AIResponse:
        self.calls.append(req)
        if "[[AI_FAIL]]" in req.text and self.fail_times > 0:
            self.fail_times -= 1
            raise AIError("mock: 一時的なエラー", retryable=True)
        lines = [ln.strip() for ln in req.text.splitlines() if len(ln.strip()) >= 6 and not ln.startswith("#")]
        items = []
        if lines:
            items.append({"category": "procedure", "statement": f"資料記載: {lines[0][:80]}",
                          "origin": "document_text" if req.task != "video_segment" else "screen",
                          "is_instructor_instruction": False, "evidence": lines[0][:80],
                          "confidence": "medium", "time_sec": req.t_start})
        items.append({"category": "formulas_graphs", "statement": "推測: 応力度は荷重÷断面積で求めると思われる",
                      "origin": "ai_inference", "is_instructor_instruction": True,  # 検証で false に矯正される
                      "evidence": "この文は原文に存在しない", "confidence": "low", "time_sec": None})
        moments = []
        if req.task == "video_segment" and "必ず" in req.text and req.t_start is not None:
            moments.append({"time_sec": req.t_start + 1, "reason": "mock: 強調表現"})
        return AIResponse(data={"items": items, "unreadable": [], "important_moments": moments},
                          model=self.model, input_tokens=len(req.text) // 2 + 100 * len(req.images),
                          output_tokens=200)
