"""試験対策: 手がかり・論点・判定・問題生成・分類の単体テスト（合成データ）。"""
from __future__ import annotations

import pytest

from structlab.exam import classify as C
from structlab.exam import cues as Q
from structlab.exam import questions as G
from structlab.exam import scoring as S
from structlab.exam.pipeline import load_config

CFG = load_config()
PAT = Q.compile_patterns(CFG)


def topic(tid="T1", title="残響時間", lectures=(3,), formulas=(), evidence=(), summary="", emphasis=False):
    return {"id": tid, "subject": "x", "title": title, "lectures": list(lectures), "formulas": list(formulas),
            "evidence": list(evidence), "summary": summary or title, "terms": [title], "texts": [summary or title],
            "key_sentences": [summary or title], "sources": [], "emphasis": emphasis}


def speech(*lines):
    return [{"kind": "speech", "start": float(i * 30), "end": float(i * 30 + 10), "text": t} for i, t in enumerate(lines)]


@pytest.mark.parametrize("line,typ,pol", [
    ("鏡像音源法は試験に出します。", "exam_explicit", "+"),
    ("この式は覚えてください", "memorize", "+"),
    ("残響時間の式は重要です", "important_formula", "+"),
    ("これは毎年よく出ます", "frequent", "+"),
    ("計算できるようにしておいてください", "calc_skill", "+"),
    ("この問題が解ければ大丈夫です", "sufficient", "+"),
    ("音の回折は試験には出しません", "exclude", "-"),
    ("ここは範囲外です", "exclude", "-"),
    ("前回の説明を訂正します", "correction", "0"),
    ("小テストで確認します", "quiz", "+"),
])
def test_cue_detection(line, typ, pol):
    cs = Q.find_cues(speech(line), "m", PAT, 3)
    assert [(c.type, c.polarity) for c in cs] == [(typ, pol)]


def test_negation_wins_over_positive_in_same_sentence():
    cs = Q.find_cues(speech("ここは試験に出ると思ったかもしれませんが試験には出しません"), "m", PAT, 3)
    assert [c.polarity for c in cs] == ["-"]


def test_unresolvable_target_is_left_for_review():
    cs = Q.find_cues(speech("この式は重要です"), "m", PAT, 3)
    Q.resolve_targets(cs, [topic(title="音の回折", summary="音は障害物の裏側に回り込む")])
    assert cs[0].target_topic is None and "要確認" in cs[0].target_note


def test_cue_resolves_to_topic_named_in_sentence():
    ts = [topic("T1", "残響時間"), topic("T2", "音の回折")]
    cs = Q.find_cues(speech("音の回折は試験には出しません"), "m", PAT, 3)
    Q.resolve_targets(cs, ts)
    assert cs[0].target_topic == "T2"


def judge(t, cues=(), info=None, unanalyzed=()):
    return S.score_topic(t, list(cues), info or {}, CFG, list(unanalyzed))


def cue_for(t, line, lecture=3, start=0.0):
    c = Q.find_cues([{"kind": "speech", "start": start, "end": start + 5, "text": line}], "m", PAT, lecture)[0]
    c.target_topic = t["id"]
    return c


def test_explicit_is_s_and_exclusion_is_c():
    t = topic()
    assert judge(t, [cue_for(t, "残響時間は試験に出します")])["grade"] == "S"
    j = judge(t, [cue_for(t, "残響時間は試験には出しません")])
    assert (j["grade"], j["sub"]) == ("C", "除外")


def test_later_lecture_overrides_earlier_exclusion():
    t = topic()
    early_ex = cue_for(t, "ここは試験には出しません", lecture=3)
    later_in = cue_for(t, "やはり残響時間は試験に出します", lecture=9)
    assert judge(t, [early_ex, later_in])["grade"] == "S"
    early_in = cue_for(t, "残響時間は試験に出します", lecture=3)
    later_ex = cue_for(t, "前回の話ですがここは試験には出しません", lecture=9)
    assert judge(t, [early_in, later_ex])["grade"] == "C"


def test_past_exam_years_make_a_and_scope_excludes():
    ev = [{"type": "past_exam", "year": 2023, "name": "a"}, {"type": "past_exam", "year": 2024, "name": "b"}]
    t = topic(evidence=ev)
    assert judge(t)["grade"] == "A"
    j = judge(t, info={"scope_lectures": [1, 2]})
    assert j["grade"] == "C" and j["sub"] == "除外"


def test_assignment_alone_is_not_decisive():
    t = topic(evidence=[{"type": "assignment", "name": "課題"}])
    assert judge(t)["grade"] == "B"


def test_unanalyzed_material_prevents_c():
    t = topic()
    assert judge(t)["grade"] == "C"
    j = judge(t, unanalyzed=[{"name": "第03回.mp4", "lecture": 3, "why": "再生のみ可"}])
    assert (j["grade"], j["sub"]) == ("B", "要確認") and any(u["type"] == "unanalyzed" for u in j["unknowns"])
    other = judge(t, unanalyzed=[{"name": "第09回.mp4", "lecture": 9, "why": "未取得"}])
    assert other["grade"] == "C"  # 別の講義回の未解析は、この論点の判定に影響しない


def test_no_probability_percentages_in_judgment():
    t = topic(evidence=[{"type": "past_exam", "year": 2024, "name": "a"}])
    assert "%" not in str(judge(t))


def test_manual_override_keeps_auto_grade():
    t = topic()
    j = S.apply_overrides(t, judge(t), {"残響時間": {"grade": "A", "reason": "先生がプリントで指示"}})
    assert j["grade"] == "A" and j["auto_grade"] == "C" and "手動修正" in j["reason"]


# ---------------- 問題 ----------------
def test_calc_question_is_verified_and_marks_system_values():
    t = topic(formulas=["T = 0.16V/A"], summary="T：残響時間 [s]、V：室容積 [m3]、A：等価吸音面積 [m2]")
    qs = G.calc_questions(t)
    assert qs and qs[0]["verified"] and "システムが設定" in qs[0]["note"]
    assert "残響時間" in qs[0]["symbols"][0] and qs[0]["answer"].endswith("s")
    v = G.calc_questions(t, variant=1)[0]
    assert v["prompt"] != qs[0]["prompt"] and v["verified"]


@pytest.mark.parametrize("f,ok", [("Z = bh²/6", True), ("σ = M/Z", True), ("I = bh^3/12", True),
                                  ("Z = bh2/6", False), ("音圧 = 大きい", False), ("a = b = c", False)])
def test_formula_parsing_refuses_ambiguous(f, ok):
    assert (G.parse_formula(f) is not None) == ok


def test_superscript_is_power():
    eq, lhs, _ = G.parse_formula("Z = bh²/6")
    import sympy as sp
    b, h = sp.symbols("b h")
    assert sp.simplify(eq.rhs - b * h ** 2 / 6) == 0


def test_question_set_and_mocks():
    ts = [topic("T1", "残響時間", formulas=["T = 0.16V/A"],
                summary="音を止めてから音圧レベルが60dB減衰するまでの時間を残響時間という。"),
          topic("T2", "鏡像音源法", summary="反射面に対して音源と対称な位置に鏡像音源を置く方法を鏡像音源法という。"),
          topic("T3", "吸音率", summary="吸音率は入射した音のエネルギーのうち反射しなかった割合である。")]
    j = {t["id"]: {"grade": g, "score": s, "past_exam_years": []} for t, g, s in zip(ts, "SAA", (20, 10, 8))}
    ex = G.mock_exams(ts, j, {})
    assert [e["no"] for e in ex] == [1, 2, 3]
    for e in ex:
        assert e["questions"] and sum(q["points"] for q in e["questions"]) == 100
        assert "仮設定" in e["provisional"]
    assert any(q["type"] == "calc" for q in ex[2]["questions"])


# ---------------- 分類 ----------------
def mat(mid, name, folder="", role="slides"):
    return {"id": mid, "name": name, "folder": folder, "role": role, "source": f"local:{folder}/{name}"}


def test_classify_by_catalog_folder_content_and_unknown():
    cat = [{"name": "環境工学Ⅳ", "aliases": ["環境工学4"], "keywords": ["残響時間", "吸音率", "音圧レベル"]},
           {"name": "鉄筋コンクリート構造Ⅱ", "keywords": ["曲げ耐力", "中立軸", "引張鉄筋"]}]
    mats = [mat("a", "第03回_資料.pdf", "環境工学4"),           # 別表記のフォルダ名
            mat("b", "lec5.pdf", "授業動画"),                    # 名前に科目なし → 内容（用語）から
            mat("c", "メモ.pdf", ""),                            # 判断できない
            mat("d", "04.mp4", "建築設備Ⅲ/授業動画")]            # 一覧に無い科目 → フォルダ名
    rd = {"a": {"units": [{"kind": "page", "text": "残響時間と吸音率"}]},
          "b": {"units": [{"kind": "page", "text": "中立軸 曲げ耐力 引張鉄筋 の計算"}]},
          "c": {"units": [{"kind": "page", "text": "買い物"}]}, "d": {"units": []}}
    r = C.classify(mats, rd, cat)
    assert r["a"]["subject"] == "環境工学Ⅳ" and r["a"]["lecture"] == 3
    assert r["b"]["subject"] == "鉄筋コンクリート構造Ⅱ" and r["b"]["lecture"] == 5
    assert r["c"]["subject"] == C.UNKNOWN
    assert r["d"]["subject"] == "建築設備Ⅲ" and r["d"]["lecture"] == 4


def test_ai_must_stay_disabled(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("ai:\n  enabled: true\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_config(p)
