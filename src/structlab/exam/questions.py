"""S・A の論点から予想問題・解答解説・模擬テストを作る（外部 AI を使わない規則ベース）。

- 計算問題: 資料の式を sympy で解き、代入した値で検算する。数値はシステムが設定した例題の値（資料の値ではない）
- 穴埋め・選択・正誤・記述: 資料の文をそのまま使い、資料に無い内容を足さない
- 採点基準・配点・試験時間は不明なので「推定」「仮設定」と明記する
"""
from __future__ import annotations

import hashlib
import random
import re

from . import text as T

GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "θ": "theta", "λ": "lambda_",
         "μ": "mu", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "ψ": "psi", "ω": "omega", "Δ": "Delta"}
SUP = str.maketrans({"²": "**2", "³": "**3"})


def _seed(*parts) -> random.Random:
    return random.Random(int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16))


def parse_formula(f: str):
    """「σ = M / Z」→ (sympy の式, 左辺の記号, {記号名: 元の表記})。解釈できなければ None。"""
    import sympy as sp
    from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application,
                                            parse_expr, standard_transformations)
    f = T.norm(f.translate(SUP)).split("。")[0]  # 上付き数字は正規化（²→2）の前に累乗へ直す
    if f.count("=") != 1:
        return None
    lhs, rhs = (x.strip() for x in f.split("="))
    rhs = re.split(r"\s{2,}|[（(][^)）]*単位|\[", rhs)[0].strip()
    if not lhs or not rhs or re.search(r"[ぁ-んァ-ン一-龠]", lhs + rhs):
        return None
    if re.search(r"[A-Za-zα-ω]\d", lhs + " " + rhs):
        # 「bh2」が bh² か添字つきの記号か、文字だけでは区別できない。誤った計算問題を作らない
        return None
    names: dict[str, str] = {}

    def conv(s: str) -> str:
        s = s.translate(SUP).replace("×", "*").replace("・", "*").replace("÷", "/").replace("√", "sqrt")
        for g, n in GREEK.items():
            if g in s:
                names[n] = g
                s = s.replace(g, f" {n} ")
        return s

    tr = standard_transformations + (implicit_multiplication_application, convert_xor)
    # E・I・S・N などを sympy の定数（ネイピア数・虚数単位など）ではなく記号として読む
    local = {c: sp.Symbol(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"}
    try:
        L = parse_expr(conv(lhs), local_dict=local, transformations=tr)
        R = parse_expr(conv(rhs), local_dict=local, transformations=tr)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(L, sp.Symbol) or len(R.free_symbols) < 1 or L in R.free_symbols:
        return None
    return sp.Eq(L, R), L, names


def symbol_meanings(sym_names: list[str], text: str) -> dict[str, dict]:
    out = {}
    t = T.norm(text)
    for n in sym_names:
        m = re.search(rf"(?<![A-Za-z]){re.escape(n)}\s*[:：はを]\s*([^\s,、。:：=]{{2,20}})(?:\s*[\[［(（]\s*([^\]］)）]{{1,12}})\s*[\]］)）])?", t)
        out[n] = {"meaning": m.group(1) if m else None, "unit": (m.group(2) if m and m.group(2) else None)}
    return out


def _display(sym, names: dict) -> str:
    return names.get(sym.name, sym.name)


def calc_questions(topic: dict, variant: int = 0, salt: str = "") -> list[dict]:
    import sympy as sp
    qs = []
    for fi, f in enumerate(topic["formulas"][:3]):
        parsed = parse_formula(f)
        if not parsed:
            continue
        eq, lhs, names = parsed
        syms = sorted(eq.rhs.free_symbols, key=lambda s: s.name)
        means = symbol_meanings([_display(s, names) for s in [lhs, *syms]], " ".join(topic["texts"]))
        rng = _seed(topic["id"], fi, variant, salt)
        target = lhs
        if variant and syms:  # 条件変更: 別の記号を求めさせる
            target = syms[(variant - 1) % len(syms)]
        known = [s for s in [lhs, *syms] if s != target]
        vals = {}
        for s in known:
            vals[s] = sp.Integer(rng.choice([2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 40, 50, 100]))
        try:
            sol = sp.solve(eq.subs({k: v for k, v in vals.items()}), target)
        except Exception:  # noqa: BLE001
            continue
        sol = [x for x in sol if x.is_real and (x.is_positive or x.is_zero is False)]
        if not sol:
            continue
        ans = sp.nsimplify(sol[0])
        check = sp.simplify(eq.lhs.subs({**vals, target: ans}) - eq.rhs.subs({**vals, target: ans})) == 0
        disp = lambda s: _display(s, names)  # noqa: E731
        unit = lambda s: (f" {means[disp(s)]['unit']}" if means.get(disp(s), {}).get("unit") else "")  # noqa: E731
        given = "、".join(f"{disp(s)} = {vals[s]}{unit(s)}" for s in known)
        mean_lines = [f"{k}: {v['meaning'] or '（資料で意味を確認できない記号）'}"
                      + (f" [{v['unit']}]" if v["unit"] else "") for k, v in means.items()]
        steps = [f"使う式: {f}", f"与えられた値: {given}"]
        if target != lhs:
            back = {sp.Symbol(k): sp.Symbol(v) for k, v in names.items()}
            steps.append(f"{disp(target)} について解く: {disp(target)} = {sp.solve(eq, target)[0].subs(back)}"
                         .replace("**", "^"))
        steps.append(f"代入して計算: {disp(target)} = {ans}" + (f" ≈ {float(ans):.4g}" if not ans.is_Integer else ""))
        steps.append("Python（sympy）で、求めた値を元の式に代入して両辺が一致することを確認した" if check
                     else "検算で不一致（要確認）")
        qs.append({
            "type": "calc", "topic": topic["id"], "variant": variant,
            "prompt": f"{topic['title']}について、式 {f} を用いる。{given} のとき、{disp(target)} を求めよ。",
            "answer": f"{disp(target)} = {ans}" + (f"（≈ {float(ans):.4g}）" if not ans.is_Integer else "")
                      + unit(target),
            "steps": steps, "symbols": mean_lines,
            "mistakes": ["式の変形で分母・分子を取り違える", "単位をそろえずに代入する（資料の単位を確認）"],
            "note": "数値はシステムが設定した例題の値（資料・試験の値ではない）", "verified": bool(check),
            "formula": f, "unit_known": bool(unit(target)),
        })
    return qs


def fill_question(topic: dict) -> dict | None:
    for s in topic["key_sentences"]:
        for term in topic["terms"]:
            if term in s and len(s) <= 120 and s.count(term) == 1:
                return {"type": "fill", "topic": topic["id"],
                        "prompt": f"次の文の空欄に当てはまる語句を答えよ。\n「{s.replace(term, '（　　　）')}」",
                        "answer": term, "explanation": f"資料の記述: 「{s}」", "source_sentence": s}
    return None


def choice_question(topic: dict, others: list[dict]) -> dict | None:
    correct = next((s for s in topic["key_sentences"] if 10 <= len(s) <= 90), None)
    if not correct or not others:
        return None
    distract = []
    for o in others:
        s = next((x for x in o["key_sentences"] if 10 <= len(x) <= 90 and topic["title"] not in x), None)
        if s and T.similarity(s, correct) < 0.5:
            distract.append((s, o["title"]))
        if len(distract) == 3:
            break
    if len(distract) < 2:
        return None
    rng = _seed(topic["id"], "choice")
    opts = [(correct, None)] + distract
    rng.shuffle(opts)
    labels = "アイウエ"
    k = next(i for i, (s, _) in enumerate(opts) if s == correct)
    expl = [f"{labels[i]}: " + ("正しい（資料の記述どおり）" if o is None else f"誤り。これは「{o}」についての記述")
            for i, (_, o) in enumerate(opts)]
    return {"type": "choice", "topic": topic["id"],
            "prompt": f"「{topic['title']}」についての記述として正しいものを一つ選べ。\n"
                      + "\n".join(f"{labels[i]}. {s}" for i, (s, _) in enumerate(opts)),
            "answer": labels[k], "explanation": "\n".join(expl)}


def tf_question(topic: dict) -> dict | None:
    for s in topic["key_sentences"]:
        m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", s)
        if not m or len(s) > 100:
            continue
        v = m.group(1)
        wrong = str(int(float(v) * 2)) if float(v) >= 1 and "." not in v else str(round(float(v) * 2, 3))
        if wrong == v:
            continue
        rng = _seed(topic["id"], "tf")
        use_wrong = rng.random() < 0.5
        stmt = s[:m.start()] + wrong + s[m.end():] if use_wrong else s
        return {"type": "tf", "topic": topic["id"], "prompt": f"次の記述の正誤を答えよ。\n「{stmt}」",
                "answer": "誤" if use_wrong else "正",
                "explanation": (f"資料では「{v}」（{wrong} ではない）。資料の記述: 「{s}」" if use_wrong
                                else f"資料の記述どおり: 「{s}」")}
    return None


def essay_question(topic: dict) -> dict:
    keys = topic["terms"][:5]
    return {"type": "essay", "topic": topic["id"],
            "prompt": f"「{topic['title']}」について、授業の内容に沿って説明せよ。",
            "answer": " ".join(topic["key_sentences"][:3]) or topic["summary"],
            "short_answer": (topic["key_sentences"][0] if topic["key_sentences"] else topic["summary"][:80]),
            "keywords": keys,
            "explanation": "採点基準は公表されていないため推定: キーワード " + "・".join(keys) + " を含め、資料の説明に沿っているか",
            "basis": "推定"}


def questions_for(topic: dict, others: list[dict], variant: int = 0, salt: str = "") -> list[dict]:
    qs = calc_questions(topic, variant, salt)
    for fn in (fill_question, tf_question):
        q = fn(topic)
        if q:
            qs.append(q)
    q = choice_question(topic, others)
    if q:
        qs.append(q)
    qs.append(essay_question(topic))
    for i, q in enumerate(qs):
        q["id"] = f"{topic['id']}-{q['type']}-{variant}{salt}-{i}"
        q["topic_title"] = topic["title"]
    return qs


KIND_ORDER = {"calc": 0, "fill": 1, "choice": 2, "tf": 3, "essay": 4}


def mock_exams(selected: list[dict], judgments: dict[str, dict], past_info: dict) -> list[dict]:
    """模擬テスト 3 回分。selected = S・A の論点（重要度順）"""
    if not selected:
        return []
    by_id = {t["id"]: t for t in selected}
    others = lambda t: [o for o in selected if o["id"] != t["id"]]  # noqa: E731
    n = max(5, min(10, len(selected) * 2))

    def pick(order: list[dict], variant: int, prefer: list[str], limit: int, salt: str = "") -> list[dict]:
        out, used = [], set()
        per = max(1, -(-limit // len(order)))  # 論点が少ないときは 1 論点から形式を変えて複数問
        for t in order:
            qs = sorted(questions_for(t, others(t), variant, salt), key=lambda q: (prefer.index(q["type"])
                                                                               if q["type"] in prefer else 9))
            for q in qs[:per + (1 if judgments[t["id"]]["grade"] == "S" else 0)]:
                if q["prompt"] not in used:
                    out.append(q)
                    used.add(q["prompt"])
            if len(out) >= limit:
                break
        return out[:limit]

    core = sorted(selected, key=lambda t: (judgments[t["id"]]["grade"] != "S", -judgments[t["id"]]["score"]))
    past = sorted(selected, key=lambda t: (-len(judgments[t["id"]].get("past_exam_years", [])),
                                           -judgments[t["id"]]["score"]))
    calc_share = past_info.get("calc_share")
    prefer2 = ["calc", "fill", "essay", "choice", "tf"] if (calc_share or 0.5) >= 0.5 else \
        ["essay", "fill", "choice", "calc", "tf"]
    exams = [
        {"no": 1, "title": "模擬テスト 第1回（最重要問題集中型）", "questions": pick(core, 0, ["calc", "fill", "essay"], n),
         "design": "S と A の上位を中心に、基本の解法・用語を確認する"},
        {"no": 2, "title": "模擬テスト 第2回（本番出題予測型）", "questions": pick(past, 0, prefer2, n, salt="m2"),
         "design": ("過去問の出題形式（計算の割合 " + (f"{calc_share:.0%}" if calc_share is not None else "不明")
                    + "）と過去問に一致した論点を優先") if past_info.get("past_exam_years")
         else "過去問が無いため、S・A の重要度順に本番に近い形式の配分（仮）で構成"},
        {"no": 3, "title": "模擬テスト 第3回（別パターン・応用型）", "questions": pick(core, 1, ["calc", "choice", "tf", "essay"], n),
         "design": "同じ重要論点を、数値・求める量・問い方を変えて出題する"},
    ]
    for ex in exams:
        k = len(ex["questions"]) or 1
        base = 100 // k
        for i, q in enumerate(ex["questions"]):
            q = ex["questions"][i] = dict(q)
            q["number"] = i + 1
            q["points"] = base + (1 if i < 100 - base * k else 0)
            q["grade"] = judgments[q["topic"]]["grade"]
        ex["time_minutes"] = 60
        ex["provisional"] = "試験時間・配点は根拠が無いため仮設定"
        ex["topics"] = sorted({q["topic"] for q in ex["questions"]})
        ex["topic_titles"] = [by_id[t]["title"] for t in ex["topics"]]
    return exams
