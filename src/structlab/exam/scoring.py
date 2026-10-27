"""出題可能性の判定（S / A / B / C）。

  S  教員が出題を明示した論点（後の授業で取り消されていないもの）
  A  過去問・頻出の指示・課題・反復などの根拠が比較的強い論点
  B  可能性はあるが根拠が足りない論点（未解析の資料があり判断できない論点もここに残す）
  C  出題しないと明示された論点（C-除外）／根拠が乏しい補足（C-低）

点数は根拠ごとの内部点数で、統計的な確率ではない（確率の％表示はしない）。
未解析の資料がある科目では、根拠が無いことを理由に C にしない（誤除外の防止）。
"""
from __future__ import annotations

EXPLICIT = {"exam_explicit", "frequent", "sufficient"}
FACTOR_LABEL = {"explicit": "出題の明示", "directive": "教員の指示（暗記・重要・計算）", "past_exam": "過去問との一致",
                "assignment": "課題・演習", "repetition": "反復", "emphasis": "資料上の強調",
                "problemability": "問題にしやすい内容"}
DIRECTIVE = {"memorize", "important_formula", "calc_skill", "quiz", "understand"}


def score_topic(t: dict, cues: list, info: dict, cfg: dict, unanalyzed: list[dict]) -> dict:
    w = cfg["scoring"]["weights"]
    mine = sorted([c for c in cues if c.target_topic == t["id"]], key=lambda c: c.order)
    factors: dict[str, float] = {}
    support, against, unknowns = [], [], []

    pos = [c for c in mine if c.polarity == "+"]
    neg = [c for c in mine if c.polarity == "-"]
    corr = [c for c in mine if c.polarity == "0"]
    last_pos = max((c.order for c in pos if c.type in EXPLICIT | DIRECTIVE), default=None)
    last_neg = max((c.order for c in neg), default=None)
    excluded = last_neg is not None and (last_pos is None or last_neg > last_pos)
    explicit = [c for c in pos if c.type in EXPLICIT]
    directive = [c for c in pos if c.type in DIRECTIVE]
    if explicit:
        factors["explicit"] = w["explicit"]
    if directive:
        factors["directive"] = w["directive"] * min(len(directive), 2)
    for c in pos + neg + corr:
        rec = {"type": "teacher_cue", "cue": c.type, "label": c.label, "text": c.text, "material": c.material,
               "lecture": c.lecture, "time": c.start, "page": c.page, "basis": "明示（教員の発言・記述）",
               "target": c.target_note}
        (support if c.polarity == "+" else against if c.polarity == "-" else unknowns).append(rec)
    if corr:
        unknowns.append({"type": "correction", "text": "訂正の発言がある。訂正後の内容を確認すること"})

    past = [e for e in t["evidence"] if e["type"] == "past_exam"]
    years = sorted({e.get("year") or e["name"] for e in past}, key=str)
    if past:
        factors["past_exam"] = w["past_exam"] * len(years)
    assign = [e for e in t["evidence"] if e["type"] == "assignment"]
    if assign:
        factors["assignment"] = w["assignment"]
    support += past + assign

    rep = t.get("repetition", {})
    n_rep = len(rep.get("other_lectures", [])) + (1 if rep.get("other_places", 0) >= 2 else 0)
    n_rep += max(0, len(t["lectures"]) - 1)
    if n_rep:
        factors["repetition"] = w["repetition"] * min(n_rep, cfg["scoring"].get("repetition_cap", 3))
        support.append({"type": "repetition", "text": f"別の講義回・資料にも登場（{n_rep} か所）",
                        "lectures": rep.get("other_lectures", []), "basis": "推測（出現回数）"})
    if t.get("emphasis"):
        factors["emphasis"] = w["emphasis"]
        support.append({"type": "emphasis", "text": "資料に「重要」等の強調", "basis": "推測（強調表記）"})
    if t["formulas"] or any(k in t["summary"] for k in ("とは", "定義", "違い", "比較")):
        factors["problemability"] = w["problemability"]
    total = round(sum(factors.values()), 2)

    scope = info.get("scope_lectures")
    out_of_scope = bool(scope and t["lectures"] and not set(t["lectures"]) & set(scope))
    in_scope = bool(scope and t["lectures"] and set(t["lectures"]) & set(scope))
    if out_of_scope:
        against.append({"type": "scope", "text": f"試験範囲（第{scope[0]}〜{scope[-1]}回）の外",
                        "basis": "明示（試験範囲の告知）"})
    if in_scope:
        support.append({"type": "scope", "text": "告知された試験範囲の内（範囲内であることは出題確定ではない）",
                        "basis": "明示（試験範囲の告知）"})
    lect_unanalyzed = [u for u in unanalyzed if u.get("lecture") is None or u.get("lecture") in t["lectures"]
                       or not t["lectures"]]
    if lect_unanalyzed:
        unknowns.append({"type": "unanalyzed", "text": f"未解析の資料 {len(lect_unanalyzed)} 件（教員の発言を確認できていない）",
                         "items": [u["name"] for u in lect_unanalyzed][:5]})
    if not t["lectures"]:
        unknowns.append({"type": "lecture_unknown", "text": "講義回が特定できない"})

    a_th, b_th = cfg["scoring"]["a_threshold"], cfg["scoring"]["b_threshold"]
    if excluded or out_of_scope:
        grade, sub = "C", "除外"
        reason = "教員が出題しないと明示（後の訂正なし）" if excluded else "告知された試験範囲の外"
    elif explicit:
        grade, sub, reason = "S", "", "教員が出題を明示"
    elif total >= a_th:
        grade, sub, reason = "A", "", "根拠の合計が A の基準以上: " + "・".join(FACTOR_LABEL[k] for k in factors)
    elif total >= b_th or (support and len(support) > (1 if t.get("emphasis") else 0)):
        grade, sub, reason = "B", "", "根拠はあるが不足: " + ("・".join(FACTOR_LABEL[k] for k in factors) or "弱い根拠のみ")
    elif lect_unanalyzed:
        grade, sub, reason = "B", "要確認", "根拠が見つからないが、未解析の資料があるため C にしない"
    else:
        grade, sub, reason = "C", "低", "出題根拠が見つからない（解析済みの資料の範囲で）"
    confidence = "高" if (grade == "S" or (grade == "C" and sub == "除外")) else (
        "中" if grade == "A" and (past or explicit or directive) else "低")
    if lect_unanalyzed and confidence == "高" and grade != "S":
        confidence = "中"
    return {"grade": grade, "sub": sub, "score": total, "factors": factors, "reason": reason,
            "confidence": confidence, "support": support, "against": against, "unknowns": unknowns,
            "past_exam_years": years}


def apply_overrides(t: dict, j: dict, overrides: dict) -> dict:
    """利用者による手動修正（configs/exam_overrides.yaml）。理由と元の判定を残す。"""
    o = overrides.get(t["id"]) or overrides.get(t["title"])
    if not o:
        return j
    j = dict(j)
    j["auto_grade"] = j["grade"]
    j["grade"] = o["grade"]
    j["reason"] = f"手動修正: {o.get('reason', '理由の記載なし')}（自動判定は {j['auto_grade']}）"
    j["overridden"] = True
    return j
