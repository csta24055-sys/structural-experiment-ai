"""費用を下げる候補の品質比較（採用の判断）。"""
from __future__ import annotations

from structlab import pilot as PL


def _dirs(*items):
    return PL.semantic_items([{"class": c, "content": t} for c, t in items])


def test_candidate_adopted_only_when_as_good_as_baseline_rerun():
    base = _dirs(("prohibited", "連続コピーはしない"), ("mandatory", "軸ラベルを書く"), ("request", "ショートカットを覚えて"))
    repeat = _dirs(("prohibited", "連続コピーはしない"), ("mandatory", "軸ラベルを書くこと"))          # 揺れ: 要望を1件落とす
    same = _dirs(("prohibited", "連続コピーはしないこと"), ("mandatory", "軸ラベルを書く"))
    worse = _dirs(("mandatory", "軸ラベルを書く"), ("request", "ショートカットを覚えて"))              # 禁止事項を落とした
    assert PL.decide("semantic", base, repeat, same)["adopt"]
    r = PL.decide("semantic", base, repeat, worse)
    assert not r["adopt"] and r["critical_missing_candidate"] > r["critical_missing_repeat"]


def test_reading_items_catch_numbers_cells_formulas():
    ai = {"formulas": [{"plain": "δ = P / K", "latex": ""}], "operations": [{"target": "B3", "value": "=A3*2"}],
          "settings_shown": [{"value": "近似曲線: 線形"}], "text_blocks": [{"text": "最大荷重 97.2 kN"}]}
    it = PL.reading_items(ai)
    assert "97.2" in it["number"] and {"B3", "A3"} <= it["cell"] and it["formula"] and it["setting"]
    base = {k: set(v) for k, v in it.items()}
    cand = {k: set(v) for k, v in it.items()}
    cand["number"].discard("97.2")
    assert not PL.decide("reading", base, base, cand)["adopt"]       # 数値を1つでも落とせば不採用
