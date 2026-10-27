"""教材の内容データ（ブロックの列）を作る。見た目は render.py が決める。

S・A の論点だけを詳しく扱い、B・C は判定一覧に題名と理由だけを載せる。
"""
from __future__ import annotations

GRADE_LABEL = {"S": "S（出題明示）", "A": "A（出題有力）", "B": "B（根拠不足）", "C": "C（対象外・低優先）"}
TYPE_LABEL = {"calc": "計算", "fill": "穴埋め", "choice": "選択", "tf": "正誤", "essay": "記述"}


def fmt_time(sec) -> str:
    if sec is None:
        return ""
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def fmt_source(s: dict, names: dict) -> str:
    name = s.get("name") or names.get(s.get("material"), s.get("material", ""))
    lec = f"第{s['lecture']}回 " if s.get("lecture") else ""
    if s.get("page"):
        return f"{lec}{name} p.{s['page']}"
    if s.get("start") is not None:
        return f"{lec}{name} {fmt_time(s['start'])}"
    return f"{lec}{name}"


def _warn_blocks(ctx: dict) -> list[dict]:
    out = []
    if ctx["unanalyzed"]:
        out.append({"t": "small", "text": "【注意】未解析の資料があります。この資料の判定は、その資料内の教員の発言を含んでいません: "
                    + "、".join(f"{u['name']}（{u['why']}）" for u in ctx["unanalyzed"][:6])})
    sa = [t for t in ctx["topics"] if ctx["j"][t["id"]]["grade"] in "SA"]
    if len(sa) <= 2:
        out.append({"t": "small", "text": f"【注意】S・A の論点が {len(sa)} 件と少ないため、範囲を絞りすぎている可能性があります。"
                    "判定一覧の B（特に「要確認」）も確認してください。"})
    return out


def doc_summary(ctx: dict) -> list[dict]:
    b = [{"t": "title", "text": f"{ctx['subject']}　最重要事項シート"},
         {"t": "small", "text": "S・A と判定した論点だけを載せています。出題の確率ではなく、根拠の強さによる分類です。"}]
    b += _warn_blocks(ctx)
    for t in ctx["selected"]:
        j = ctx["j"][t["id"]]
        b.append({"t": "h1", "text": f"［{j['grade']}］{t['title']}"})
        b.append({"t": "small", "text": f"判定理由: {j['reason']}"})
        if t["key_sentences"]:
            b.append({"t": "h2", "text": "必ず押さえる内容"})
            b.append({"t": "list", "items": t["key_sentences"][:3]})
        if t["formulas"]:
            b.append({"t": "h2", "text": "重要公式"})
            b.append({"t": "list", "items": t["formulas"][:4]})
        calc = next((q for q in ctx["questions"][t["id"]] if q["type"] == "calc"), None)
        if calc:
            b.append({"t": "h2", "text": "典型解法"})
            b.append({"t": "list", "items": calc["steps"][:-1]})
            b.append({"t": "h2", "text": "よくあるミス"})
            b.append({"t": "list", "items": calc["mistakes"]})
        if t["terms"]:
            b.append({"t": "p", "text": "記述のキーワード: " + "・".join(t["terms"][:6])})
    return b


def doc_deep(ctx: dict) -> list[dict]:
    names = ctx["names"]
    b = [{"t": "title", "text": f"{ctx['subject']}　徹底対策資料"}]
    b += _warn_blocks(ctx)
    for t in ctx["selected"]:
        j = ctx["j"][t["id"]]
        b.append({"t": "h1", "text": f"［{GRADE_LABEL[j['grade']]}］{t['title']}"})
        b.append({"t": "small", "text": "授業での位置: " + "／".join(fmt_source(s, names) for s in t["sources"][:5])})
        b.append({"t": "h2", "text": "出題根拠"})
        b.append({"t": "list", "items": [f"{e.get('label') or e['type']}: {e.get('text', '')[:120]}（{e.get('basis', '')}）"
                                         for e in j["support"][:6]] or ["（強い根拠なし）"]})
        if j["unknowns"]:
            b.append({"t": "small", "text": "未確認: " + "／".join(u["text"] for u in j["unknowns"])})
        b.append({"t": "h2", "text": "内容"})
        b.append({"t": "list", "items": t["key_sentences"][:8] or [t["summary"]]})
        qs = ctx["questions"][t["id"]]
        for q in [q for q in qs if q["type"] == "calc"][:2]:
            b.append({"t": "h2", "text": f"公式と計算: {q['formula']}"})
            b.append({"t": "list", "items": q["symbols"]})
            b.append({"t": "p", "text": "例題: " + q["prompt"]})
            b.append({"t": "pre", "text": "\n".join(q["steps"])})
            b.append({"t": "p", "text": "答え: " + q["answer"]})
            b.append({"t": "small", "text": q["note"]})
        types = sorted({TYPE_LABEL[q["type"]] for q in qs})
        b.append({"t": "small", "text": "想定される出題形式: " + "・".join(types)})
    return b


def doc_formulas(ctx: dict) -> list[dict]:
    rows = [["公式", "記号の意味・単位", "論点", "出典"]]
    for t in ctx["selected"]:
        for q in [q for q in ctx["questions"][t["id"]] if q["type"] == "calc"]:
            rows.append([q["formula"], "\n".join(q["symbols"]), t["title"], fmt_source(t["sources"][0], ctx["names"])])
        for f in t["formulas"]:
            if not any(r[0] == f for r in rows):
                rows.append([f, "（式を自動で解釈できない: 資料で確認）", t["title"], fmt_source(t["sources"][0], ctx["names"])])
    b = [{"t": "title", "text": f"{ctx['subject']}　公式一覧"}]
    if len(rows) == 1:
        b.append({"t": "p", "text": "S・A の論点に公式はありませんでした。"})
    else:
        b.append({"t": "table", "rows": rows})
    return b


def _q_text(q: dict, n: int, points: bool = False) -> list[dict]:
    head = f"問{n}" + (f"（{q['points']}点）" if points and q.get("points") else "") + f"［{TYPE_LABEL[q['type']]}］"
    return [{"t": "h2", "text": head}, {"t": "p", "text": q["prompt"]}]


def doc_questions(title: str, qs: list[dict], meta: str = "", points: bool = False) -> list[dict]:
    b = [{"t": "title", "text": title}]
    if meta:
        b.append({"t": "small", "text": meta})
    for i, q in enumerate(qs, 1):
        b += _q_text(q, i, points)
    return b


def doc_answer_sheet(title: str, qs: list[dict]) -> list[dict]:
    b = [{"t": "title", "text": title}, {"t": "p", "text": "学籍番号（　　　　　　　）　氏名（　　　　　　　　　　）"}]
    for i, q in enumerate(qs, 1):
        b.append({"t": "h2", "text": f"問{i}"})
        b.append({"t": "space", "lines": {"calc": 7, "essay": 8, "fill": 2, "choice": 1, "tf": 1}[q["type"]]})
    return b


def doc_solutions(title: str, qs: list[dict], ctx: dict, show_grade: bool = True) -> list[dict]:
    b = [{"t": "title", "text": title}]
    for i, q in enumerate(qs, 1):
        j = ctx["j"][q["topic"]]
        t = ctx["by_id"][q["topic"]]
        b.append({"t": "h2", "text": f"問{i}　{q['topic_title']}" + (f"［重要度 {j['grade']}］" if show_grade else "")})
        b.append({"t": "p", "text": "解答: " + q["answer"]})
        if q.get("steps"):
            b.append({"t": "pre", "text": "\n".join(q["steps"])})
        if q.get("explanation"):
            b.append({"t": "pre", "text": q["explanation"]})
        if q.get("short_answer"):
            b.append({"t": "p", "text": "短い解答: " + q["short_answer"]})
        if q.get("note"):
            b.append({"t": "small", "text": q["note"]})
        b.append({"t": "small", "text": "根拠: " + j["reason"] + "／出典: " + fmt_source(t["sources"][0], ctx["names"])})
    return b


def doc_judgments(ctx: dict) -> list[dict]:
    rows = [["判定", "論点", "講義回", "理由", "未確認"]]
    order = {"S": 0, "A": 1, "B": 2, "C": 3}
    for t in sorted(ctx["topics"], key=lambda t: (order[ctx["j"][t["id"]]["grade"]], -ctx["j"][t["id"]]["score"])):
        j = ctx["j"][t["id"]]
        rows.append([j["grade"] + (f"-{j['sub']}" if j["sub"] else ""), t["title"],
                     ",".join(map(str, t["lectures"])) or "不明", j["reason"],
                     "／".join(u["text"] for u in j["unknowns"])[:80]])
    b = [{"t": "title", "text": f"{ctx['subject']}　出題予測の判定一覧"},
         {"t": "small", "text": "S: 出題明示 / A: 出題有力 / B: 根拠不足（要確認を含む） / C-除外: 出題しないと明示・範囲外 / C-低: 根拠なし。"
          "点数は根拠の内部点数で、確率ではありません。"}]
    b += _warn_blocks(ctx)
    b.append({"t": "table", "rows": rows})
    if ctx["info"].get("unmatched_questions"):
        b.append({"t": "h2", "text": "どの論点にも照合できなかった過去問の設問"})
        b.append({"t": "list", "items": [f"{u['material']} p.{u['page']}: {u['text'][:80]}"
                                         for u in ctx["info"]["unmatched_questions"][:15]]})
    return b
