"""テスト用: 意味理解層の各タスクに対する決定的な応答（外部送信なし）。

実際のAIの理解力を模倣するものではない。多段階の処理（局所理解→全体理解→再解釈→指示抽出→照合→統合）が
正しくつながり、検証機構が働くことを確かめるためのもの。テキスト中の次の印で挙動を変える:
  [[MISS]]     … 指示抽出でわざと拾わない（見落とし検出のテスト）
  [[MISCLASS]] … 指示の向きをわざと逆に分類する（分類ガードのテスト）
  [[NOTDIR]]   … 規則候補を「指示ではない」と判断する
"""
from __future__ import annotations

import json
import re

from ..semantic.rules import DIRECTIVE_CLASSES, classify, discourse_markers, norm, similarity

_LINE = re.compile(r"^(?:\[(\d\d:\d\d:\d\d|p\.\d+)\] )?(発話|画面|資料): (.*)$")
_LABEL2CLS = {v: k for k, v in DIRECTIVE_CLASSES.items()}


def _sec(t: str | None) -> float | None:
    if not t or t.startswith("p."):
        return None
    h, m, s = map(int, t.split(":"))
    return h * 3600 + m * 60 + s


def _section(text: str, head: str, nxt: str = "\n## ") -> str:
    i = text.find(head)
    if i < 0:
        return ""
    rest = text[i + len(head):]
    j = rest.find(nxt)
    return rest if j < 0 else rest[:j]


def _json_after(text: str, head: str):
    sec = text[text.find(head):] if head in text else ""
    m = re.search(r"```json\n(.*?)\n```", sec, re.S)
    return json.loads(m.group(1)) if m else None


def _lines(sec: str) -> list[tuple[float | None, str, str]]:
    out = []
    for ln in sec.splitlines():
        m = _LINE.match(ln.strip())
        if m:
            out.append((_sec(m.group(1)), m.group(2), m.group(3)))
    return out


def scene_analysis(text: str) -> dict:
    sec = _section(text, "### 時刻同期した発話・画面")
    ls = _lines(sec)
    speech = [x for x in ls if x[1] in ("発話", "資料")]
    first = speech[0][2] if speech else (ls[0][2] if ls else "")
    disc = []
    for t, _, tx in ls:
        for k in discourse_markers(tx):
            disc.append({"type": k, "description": f"mock: {k}", "time_sec": t, "evidence": tx})
    return {"analysis": {
        "speech": " / ".join(x[2] for x in speech), "screen": " / ".join(x[2] for x in ls if x[1] == "画面"),
        "operations": "mock: 画面の操作", "meaning": f"「{first[:40]}」についての説明", "purpose": "mock: 目的",
        "relation_prev": "mock", "relation_next": "mock", "audience": "受講する学生", "task": first[:30],
        "deliverables": ["レポート"] if any("提出" in x[2] or "レポート" in x[2] for x in ls) else [],
        "key_points": [{"text": x[2], "time_sec": x[0], "basis": "speech" if x[1] == "発話" else "document"} for x in speech],
        "discourse": disc, "uncertain": [], "unreadable": [], "items": [],
    }}


def global_synthesis(text: str) -> dict:
    sc = _json_after(text, "## 場面の理解") or []
    if isinstance(sc, dict):
        sc = [s for p in sc.get("partial_understandings", []) for s in p["understanding"].get("flow", [])]
    scenes = [s for s in sc if isinstance(s, dict) and s.get("meaning")]
    changes, contra = [], []
    for s in scenes:
        for d in s.get("discourse") or []:
            if d["type"] == "correction":
                earlier = None
                for e in scenes:
                    if e["scene_seq"] < s["scene_seq"] and similarity(e.get("speech", ""), d["evidence"]) > 0.05:
                        earlier = e["scene_seq"]
                        break
                changes.append({"type": "correction", "description": "mock: 訂正", "earlier_scene": earlier,
                                "later_scene": s["scene_seq"], "evidence": d["evidence"]})
                contra.append({"description": "前半と後半の説明が異なる", "scene_seqs": [x for x in (earlier, s["scene_seq"]) if x],
                               "resolved": True, "resolution": "後半で訂正された", "resolution_evidence": d["evidence"]})
    kps = [k for s in scenes for k in s.get("key_points") or []]
    return {
        "theme": scenes[0]["meaning"] if scenes else "", "purpose": "mock: 目的", "experiment_purpose": "mock",
        "message": "mock", "flow": [{"order": i + 1, "description": s["meaning"], "scene_seqs": [s["scene_seq"]]}
                                     for i, s in enumerate(scenes)],
        "logic_structure": "mock", "scene_roles": [{"scene_seq": s["scene_seq"], "role": "mock"} for s in scenes],
        "procedure": [{"step": i + 1, "action": k["text"], "purpose": "mock", "depends_on": [i] if i else [],
                       "scene_seqs": []} for i, k in enumerate(kps[:10])],
        "conclusion": "mock", "important_points": [], "changes": changes,
        "repeated_cautions": [], "apparent_contradictions": contra,
        "final_requirements": [{"text": k["text"], "scene_seqs": []} for k in kps if re.search("必ず|提出|すること", k["text"])],
        "understand_goals": ["mock"], "action_goals": ["mock"], "open_questions": [],
        "explanation": " ".join(s["meaning"] for s in scenes),
    }


def scene_reinterpret(text: str) -> dict:
    cur = _json_after(text, "### この場面の現在の解釈") or {}
    g = _json_after(text, "## 資料全体の理解") or {}
    m = re.search(r"## 対象場面 #(\d+)", text)
    seq = int(m.group(1)) if m else -1
    hit = [c for c in g.get("changes") or [] if c.get("earlier_scene") == seq]
    if hit and "後の場面で訂正" not in cur.get("meaning", ""):
        after = cur.get("meaning", "") + "（後の場面で訂正あり）"
        new = dict(cur, meaning=after)
        return {"revised": True, "changes": [{"field": "meaning", "before": cur.get("meaning", ""), "after": after,
                                               "reason": f"場面 {hit[0]['later_scene']} で訂正"}], "analysis": new}
    return {"revised": False, "changes": [], "analysis": cur}


def directive_extraction(text: str) -> dict:
    sec = _section(text, "## 規則による候補（全件確認すること）")
    out, dismissed = [], []
    for ln in sec.splitlines():
        m = re.match(r"候補 (\d+): \[(.+?)\] (.*?)(?:（(\d\d:\d\d:\d\d)）)?(?:（条件: (.*)）)?$", ln.strip())
        if not m:
            continue
        n, label, content, t, cond = int(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5)
        if "[[MISS]]" in content:
            continue
        if "[[NOTDIR]]" in content:
            dismissed.append({"candidate": n, "reason": "mock: 指示ではない"})
            continue
        cls = _LABEL2CLS.get(label, "other")
        if "[[MISCLASS]]" in content:
            cls = "recommended" if cls in ("prohibited", "discouraged") else "prohibited"
        ts = _sec(t)
        out.append({"content": content, "directive_class": cls, "base_strength": "mandatory" if cls == "conditional" else None,
                    "condition": cond, "target_task": "mock作業", "deliverable": "", "audience": "学生", "t_start": ts,
                    "t_end": ts, "screen": "", "operation": "", "speech_quote": content, "context_before": "",
                    "context_after": "", "certainty": "medium", "unconfirmed": "", "origin": "explicit",
                    "speaker": "teacher" if "[[TEACHER]]" in content else "unknown", "speaker_basis": "",
                    "relation_type": "none", "relation_refers_to": "", "matched_candidates": [n]})
    if "## 対象場面 #1 " in text:
        out.append({"content": "測定データは実験当日中に整理しておく", "directive_class": "recommended", "base_strength": None,
                    "condition": None, "target_task": "データ整理", "deliverable": "", "audience": "学生", "t_start": None,
                    "t_end": None, "screen": "", "operation": "", "speech_quote": "", "context_before": "",
                    "context_after": "", "certainty": "low", "unconfirmed": "mock: 推論", "origin": "ai_inference",
                    "speaker": "unknown", "speaker_basis": "",
                    "relation_type": "none", "relation_refers_to": "", "matched_candidates": []})
    return {"directives": out, "dismissed_candidates": dismissed}


def directive_verify(text: str) -> dict:
    tgt = _json_after(text, "## 確認対象") or {}
    win = _lines(_section(text, "## 元の発話・画面・資料（"))
    key = norm(re.sub(r"\[\[\w+\]\]", "", tgt.get("content", "")))[:10]
    for t, _, tx in win:
        if key and key in norm(re.sub(r"\[\[\w+\]\]", "", tx)):
            rc, _ = classify(tx)
            if rc and rc != tgt.get("directive_class"):
                return {"verdict": "revised", "reason": "mock: 原文の言い回しに合わせて分類を修正", "evidence_quote": tx,
                        "revised": {"content": tgt["content"], "directive_class": rc, "base_strength": None,
                                    "condition": tgt.get("condition"), "t_start": t, "t_end": t, "certainty": "medium"},
                        "additional_directives": []}
            return {"verdict": "confirmed", "reason": "mock: 原文で確認", "evidence_quote": tx, "revised": None,
                    "additional_directives": []}
    return {"verdict": "rejected", "reason": "mock: 根拠が見つからない", "evidence_quote": "", "revised": None,
            "additional_directives": []}


def corpus_integration(text: str) -> dict:
    dirs = []
    for ln in _section(text, "## 指示一覧（#番号で参照）").splitlines():
        m = re.match(r"#(\d+) \[(.+?)\] (.*?) — (\S+)", ln.strip())
        if m:
            dirs.append((int(m.group(1)), _LABEL2CLS.get(m.group(2)), m.group(3), m.group(4)))
    hints = _section(text, "## 規則で検出した資料間の関連・矛盾候補")
    same = [list(map(int, x)) for x in re.findall(r"#(\d+) と #(\d+): same_as", hints)]
    conf = [list(map(int, x)) for x in re.findall(r"#(\d+) と #(\d+): conflicts_with", hints)]
    sub = [d for d in dirs if re.search("提出|レポート", d[2])]
    return {
        "experiment_purpose": "mock: 鋼材の引張試験", "experiment_content": "mock",
        "procedure": [{"step": i + 1, "action": d[2], "sources": [d[3]], "depends_on": []} for i, d in enumerate(dirs[:5])],
        "data_processing": "mock", "excel_method": "mock", "report_method": "mock",
        "work_order": [d[2] for d in dirs[:3]], "dependencies": [],
        "deliverables": [{"name": "レポート", "requirements": [d[2] for d in sub], "directive_ids": [d[0] for d in sub] + [999999],
                          "sources": sorted({d[3] for d in sub})}],
        "requirements": [{"text": d[2], "directive_ids": [d[0]], "sources": [d[3]]} for d in dirs
                         if d[1] in ("mandatory", "prohibited")],
        "common_directive_groups": [{"summary": "mock: 共通", "directive_ids": g} for g in same],
        "supplements": [], "corrections": [],
        "contradictions": [{"description": "mock: 資料間の矛盾", "directive_ids": g} for g in conf],
        "open_questions": ["mock: 提出期限はどちらか"] if conf else [], "explanation": "mock",
    }


def blind_directives(text: str) -> dict:
    """独立した再検証: 元の範囲だけから規則で指示を列挙する（[[MISCLASS]] の誤りを含まない）。"""
    from ..semantic.rules import directives_in
    out = []
    for t, _, tx in _lines(_section(text, "## 元の発話・画面・資料")):
        for rd in directives_in(re.sub(r"\[\[\w+\]\]", "", tx)):
            if "[[BLINDFLIP]]" in tx:
                rd.directive_class = "recommended" if rd.directive_class in ("prohibited", "discouraged") else "prohibited"
            out.append({"content": rd.content, "directive_class": rd.directive_class, "base_strength": rd.base_strength,
                        "condition": rd.condition, "quote": tx, "t_start": t})
    return {"directives": out}


def style_rewrite(text: str) -> dict:
    """文章スタイル: 表現だけを置き換える。[[STYLE_BREAK]] で数値を変え、[[STYLE_INVENT]] で文を書き足す（検査のテスト用）。"""
    body = _section(text, "## 調整する文章（内容は確定済み・変更禁止）\n", "\n## ")
    out = body.replace("したがって、", "そのため、").replace("であると考えられる。", "だと考えられる。")
    if "[[STYLE_BREAK]]" in out:
        out = re.sub(r"\d+", lambda m: str(int(m.group()) + 1), out, count=1)
    if "[[STYLE_INVENT]]" in out:
        out += "この実験はとても楽しく、将来の研究にも大いに役立つ貴重な経験となった。"
    return {"rewritten": out.strip("\n"), "changes": [{"before": "したがって、", "after": "そのため、", "rule_id": "P4-2-0101",
                                                     "reason": "mock"}], "not_applied": []}


def directive_verify_group(text: str) -> dict:
    """まとめた照合: 各確認対象を1件ずつの照合と同じ規則で判定する。"""
    win = _section(text, "## 元の発話・画面・資料（")
    out = []
    for m in re.finditer(r"### 確認対象 (\d+)\n```json\n(.*?)\n```", text, re.S):
        one = f"## 確認対象\n```json\n{m.group(2)}\n```\n## 元の発話・画面・資料（{win}"
        out.append({"id": int(m.group(1)), **directive_verify(one)})
    return {"results": out}


TASKS = {"scene_analysis": scene_analysis, "global_synthesis": global_synthesis,
         "global_synthesis_part": global_synthesis, "scene_reinterpret": scene_reinterpret,
         "directive_extraction": directive_extraction, "directive_verify": directive_verify,
         "directive_verify_group": directive_verify_group,
         "corpus_integration": corpus_integration, "blind_directives": blind_directives,
         "style_rewrite": style_rewrite}
