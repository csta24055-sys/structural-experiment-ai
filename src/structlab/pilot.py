"""費用を下げる候補の「品質が同じか」を、本番の前に小さな見本で確かめる（確かめられた候補だけを使う）。

AI の出力は同じ依頼でも毎回少し違うため、次の3つを比べる:
  baseline … 今の方式
  repeat   … 今の方式をもう一度（同じ方式どうしの揺れの大きさ）
  candidate… 候補の方式
採用の条件（どちらも満たすときだけ採用。満たさなければ今の方式のまま）:
  1. baseline の項目が candidate に含まれる割合 ≥ baseline の項目が repeat に含まれる割合 − 許容差（既定 0.02）
  2. 重要な項目（禁止・必須の指示、数値、数式、セル番地）で、candidate だけが落としたものが repeat より多くない

  structlab pilot compare --kind reading|semantic --baseline DIR --repeat DIR --candidate DIR --out DIR
  structlab pilot plan     （候補・見本・確かめるための費用・見込める削減の一覧）
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

NUM = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?")
CELL = re.compile(r"\b[A-Z]{1,3}\$?\d{1,5}\b")
CRITICAL_CLASSES = {"prohibited", "mandatory", "conditional"}

CANDIDATES = [
    {"id": "C1", "config": {"semantic.cache_layout": "prefix"},
     "what": "意味理解の再解釈・照合で、資料全体の理解を依頼の先頭に置いてキャッシュする（内容は同じ、順序だけ変わる）",
     "sample": "事前説明1（8 場面）の意味理解の全段階", "kind": "semantic",
     "saving": "資料全体の理解（最大 2 万字）を毎回送る約 400 件の依頼で、その部分の入力が約 1/10 になる"},
    {"id": "C2", "config": {"reading.semantic_material": "compact"},
     "what": "読み取り結果を意味理解に渡すとき、JSON の括弧・引用符を省いた短い書き方にする（キー・値はすべて残す）",
     "sample": "事前説明1 の意味理解の全段階", "kind": "semantic", "saving": "場面の材料の入力が 1〜2 割減る"},
    {"id": "C4", "config": {"semantic.verify_group": 5},
     "what": "第3回の照合で、同じ場面の指示（最大 5 件）を1回でまとめて照合する（画面・範囲は各指示の分を合わせる）",
     "sample": "エクセルの作り方1 の照合対象（約 30 件）", "kind": "semantic",
     "saving": "照合の依頼が約 6 割減り、同じ画面・資料全体の理解を何度も送らない"},
    {"id": "C3", "config": {"reading.roles[experiment].group": 1},
     "what": "（逆方向の確認）実験区間を4つずつまとめて読む今の構成が、区間ごとに読む方式と同等かを確かめる",
     "sample": "先生2 の 8 区間", "kind": "reading",
     "saving": "同等でなければ区間ごとに戻す（費用は増えるが品質を優先。指示 P085 の矛盾の解消）"},
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def reading_items(ai: dict | None) -> dict[str, set[str]]:
    """読み取り結果から比べる項目（数値・セル番地・数式・操作・設定・音声の要点・本文）。"""
    out = {"number": set(), "cell": set(), "formula": set(), "operation": set(), "setting": set(), "speech": set(),
           "text": set()}
    if not ai:
        return out
    blob = json.dumps(ai, ensure_ascii=False)
    out["number"] = {n.replace(",", "") for n in NUM.findall(blob)}
    out["cell"] = set(CELL.findall(blob))
    for f in ai.get("formulas", []) or []:
        out["formula"].add(_norm(f.get("plain") or f.get("latex")))
    for o in ai.get("operations", []) or []:
        out["operation"].add(_norm(f"{o.get('target', '')}{o.get('value', '')}"))
    for x in ai.get("settings_shown", []) or []:
        out["setting"].add(_norm(x.get("value")))
    for x in ai.get("speech_points", []) or []:
        out["speech"].add(_norm(x.get("text")))
    for b in ai.get("text_blocks", []) or []:
        out["text"].add(_norm(b.get("text")))
    return {k: {v for v in s if v} for k, s in out.items()}


def semantic_items(directives: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {"critical": set(), "directive": set()}
    for d in directives:
        key = f"{d.get('class')}|{_norm(d.get('content'))}"
        out["directive"].add(key)
        if d.get("class") in CRITICAL_CLASSES:
            out["critical"].add(key)
    return out


def _contains(item: str, pool: set[str], fuzzy: bool) -> bool:
    if item in pool:
        return True
    if not fuzzy:
        return False
    cls, _, txt = item.partition("|")
    for p in pool:
        pc, _, pt = p.partition("|")
        if (not cls or cls == pc) and (txt in pt or pt in txt or SequenceMatcher(None, txt, pt).ratio() >= 0.8):
            return True
    return False


def containment(a: dict[str, set[str]], b: dict[str, set[str]], fuzzy: bool = False) -> tuple[float, dict[str, list[str]]]:
    total, hit, missing = 0, 0, {}
    for k, items in a.items():
        miss = [x for x in items if not _contains(x, b.get(k, set()), fuzzy)]
        total += len(items)
        hit += len(items) - len(miss)
        if miss:
            missing[k] = miss
    return (hit / total if total else 1.0), missing


CRITICAL_KINDS = {"reading": ("number", "cell", "formula", "setting"), "semantic": ("critical",)}


def decide(kind: str, base: dict, repeat: dict, cand: dict, tol: float = 0.02) -> dict:
    fuzzy = kind == "semantic"
    c_rep, m_rep = containment(base, repeat, fuzzy)
    c_can, m_can = containment(base, cand, fuzzy)
    crit = CRITICAL_KINDS[kind]
    crit_rep = sum(len(m_rep.get(k, [])) for k in crit)
    crit_can = sum(len(m_can.get(k, [])) for k in crit)
    ok = c_can >= c_rep - tol and crit_can <= crit_rep
    return {"adopt": ok, "containment_repeat": round(c_rep, 4), "containment_candidate": round(c_can, 4),
            "critical_missing_repeat": crit_rep, "critical_missing_candidate": crit_can,
            "missing_in_candidate": {k: v[:50] for k, v in m_can.items()},
            "rule": f"候補の包含率 ≥ 同じ方式どうしの包含率 − {tol}、かつ重要項目の欠落が同じ方式どうし以下"}


def load_reading(d: Path) -> dict[str, set[str]]:
    agg: dict[str, set[str]] = {}
    for rp in sorted(Path(d).rglob("reading.json")):
        r = json.loads(rp.read_text(encoding="utf-8"))
        units = r.get("pages", []) + [s for s in r.get("scenes", []) if s.get("same_as") is None]
        for u in units:
            for k, v in reading_items(u.get("ai")).items():
                agg.setdefault(k, set()).update(f"{r['path']}#{x}" for x in v)
    return agg


def load_semantic(d: Path) -> dict[str, set[str]]:
    f = next(iter(sorted(Path(d).rglob("directives.json"))), None)
    return semantic_items(json.loads(f.read_text(encoding="utf-8"))) if f else {"critical": set(), "directive": set()}


def compare(kind: str, baseline: Path, repeat: Path, candidate: Path, tol: float = 0.02) -> dict:
    load = load_reading if kind == "reading" else load_semantic
    return decide(kind, load(baseline), load(repeat), load(candidate), tol)
