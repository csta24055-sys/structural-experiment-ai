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
    {"id": "C1", "config": {"semantic.cache_layout": "prefix"}, "opts": {"prefix_layout": True},
     "what": "意味理解の再解釈・照合で、指示文と資料全体の理解を依頼の先頭（照合では画像より前）に置いてキャッシュする"
             "（内容は同じ、順序だけ変わる）。再解釈は場面の順に送るので 1 時間のキャッシュ",
     "sample": "事前説明1", "kind": "semantic",
     "saving": "資料全体の理解（最大 2 万字）を毎回送る再解釈・照合の依頼で、その部分の入力が約 1/7〜1/20 になる"},
    {"id": "C2", "config": {"reading.semantic_material": "compact"}, "opts": {"compact": True},
     "what": "読み取り結果を意味理解に渡すとき、JSON の括弧・引用符を省いた短い書き方にする（キー・値はすべて残す）",
     "sample": "事前説明1", "kind": "semantic", "saving": "場面の材料の入力が 1〜2 割減る"},
    {"id": "C4", "config": {"semantic.verify_group": 5}, "opts": {"verify_group": 5},
     "what": "第3回の照合で、同じ場面の指示（最大 5 件）を1回でまとめて照合する（画面・範囲は各指示の分を合わせる）",
     "sample": "事前説明1", "kind": "semantic",
     "saving": "照合の依頼が約 6 割減り、同じ画面・資料全体の理解を何度も送らない"},
    {"id": "C3", "config": {"reading.roles[experiment].group": 1}, "opts": None,
     "what": "（逆方向の確認）実験区間を4つずつまとめて読む今の構成が、区間ごとに読む方式と同等かを確かめる",
     "sample": "鋼材実験経過先生2", "kind": "reading",
     "saving": "同等でなければ区間ごとに戻す（費用は増えるが品質を優先。指示 P085 の矛盾の解消）"},
]


#: 段階を分けた比較の対象（候補 → 費用モデルの設定）
ARM_OPTS = {"C1v": {"prefix_layout": True, "layout_scope": "verify"}, "C4": {"verify_group": 5},
            "C1v+C4": {"prefix_layout": True, "layout_scope": "verify", "verify_group": 5},
            "C1r": {"prefix_layout": True, "layout_scope": "reinterpret"}, "C5": {"reinterpret_delta": True},
            "C1r+C5": {"prefix_layout": True, "layout_scope": "reinterpret", "reinterpret_delta": True},
            "C1+C4+C5": {"prefix_layout": True, "verify_group": 5, "reinterpret_delta": True},
            "C2": {"compact": True}}
#: 段階ごとに、比較で実行し直す工程（費用モデルの行の名前）
STAGE_ROWS = {"verify": ("3 元資料との照合",),
              "reinterpret": ("1 再解釈", "1 全体の理解", "2 指示の抽出", "3 元資料との照合"),
              "all": None}


def plan_costs(db_path: Path | None, sample: str = "事前説明1") -> list[dict]:
    """候補ごとの、見本の比較にかかる追加の費用と本番 1 回あたりの削減（mid）。
    段階を分けた比較（branch_run）: 本番の今の方式の 1 回はそのまま使い、「今の方式をもう 1 回」と「候補」の 2 回分が追加。
    全工程の比較: 今の方式×2＋候補（本番とは別）。"""
    from . import costmodel as CM
    if not (db_path and Path(db_path).exists()):
        return []
    full = CM.semantic(db_path, "mid")["yen"]
    out = []
    for arm, opts in ARM_OPTS.items():
        stage = ARMS.get(arm, {}).get("stage", "all")
        rows = STAGE_ROWS[stage]
        base = CM.semantic(db_path, "mid", only=sample)["rows"]
        cand = CM.semantic(db_path, "mid", only=sample, **opts)["rows"]
        part = (lambda r: sum(v["yen"] for k, v in r.items() if rows is None or k in rows))
        extra = part(base) + part(cand) if rows is not None else 2 * part(base) + part(cand)
        saving = full - CM.semantic(db_path, "mid", **opts)["yen"]
        out.append({"arm": arm, "stage": stage, "sample": sample, "pilot_extra_yen": round(extra),
                    "saving_per_run_yen": round(saving), "net_after_one_run_yen": round(saving - extra)})
    return out


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


VERIFY_VERDICTS = {"verified", "revised", "rejected", "needs_review", "class_mismatch_unresolved", "found_in_verify"}


def semantic_items(directives: list[dict]) -> dict[str, set[str]]:
    """指示の一覧から比べる項目。照合（第3回）の判定も比べる（照合の段階だけを比べる見本で使う）。"""
    out: dict[str, set[str]] = {"critical": set(), "directive": set(), "verdict": set()}
    for d in directives:
        key = f"{d.get('class')}|{_norm(d.get('content'))}"
        out["directive"].add(key)
        if d.get("class") in CRITICAL_CLASSES:
            out["critical"].add(key)
        if d.get("verification") in VERIFY_VERDICTS:
            out["verdict"].add(f"{key}|{d.get('verification')}")
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
    return semantic_items(json.loads(f.read_text(encoding="utf-8"))) if f else {"critical": set(), "directive": set(),
                                                                                  "verdict": set()}


def compare(kind: str, baseline: Path, repeat: Path, candidate: Path, tol: float = 0.02) -> dict:
    load = load_reading if kind == "reading" else load_semantic
    return decide(kind, load(baseline), load(repeat), load(candidate), tol)


# ---------------- 段階を分けた見本の比較（費用を抑える） ----------------
#: 候補が影響する最初の段階。その直前まで本番どおりに進めた意味理解の DB を複製し、その段階から先だけを
#: 「今の方式（もう 1 回）」と「候補」で実行して比べる。本番の 1 回目（今の方式）はそのまま本番の結果に使う
ARMS = {
    "C1v": {"stage": "verify", "semantic": {"cache_layout": "prefix_verify"}},
    "C4": {"stage": "verify", "semantic": {"verify_group": 5}},
    "C1v+C4": {"stage": "verify", "semantic": {"cache_layout": "prefix_verify", "verify_group": 5}},
    "C1r": {"stage": "reinterpret", "semantic": {"cache_layout": "prefix_reinterpret"}},
    "C5": {"stage": "reinterpret", "semantic": {"reinterpret_output": "delta"}},
    "C1r+C5": {"stage": "reinterpret", "semantic": {"cache_layout": "prefix_reinterpret", "reinterpret_output": "delta"}},
    # まとめて 1 回で比べる（再解釈から先をすべて）。不合格なら個別の比較に分ける
    "C1+C4+C5": {"stage": "reinterpret", "semantic": {"cache_layout": "prefix", "verify_group": 5,
                                                      "reinterpret_output": "delta"}},
}


def branch_run(cfg, db_path: Path, record: dict, arm: str | None, out: Path, provider, log=print) -> Path:
    """見本の資料 1 件について、DB を複製して指定の段階から先を実行する（arm=None なら今の方式）。
    有料（provider が外部 AI）。利用者の承認後にだけ使う。戻り値: その実行の出力フォルダ（semantic/directives.json を含む）。"""
    import copy
    import shutil
    from .reading.semantic_bridge import run_semantic
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, out / "semantic.db")
    c2 = copy.copy(cfg)
    sem = {**cfg.data.get("semantic", {}), "stop_before": None}
    if arm:
        sem.update(ARMS[arm]["semantic"])
    c2.data = {**cfg.data, "semantic": sem}
    run_semantic(c2, out, {record["path"]: record}, provider, batch=bool(cfg.data.get("reading", {}).get("batch")), log=log)
    return out


def branch_compare(cfg, db_path: Path, record: dict, arm: str, out: Path, provider, log=print) -> dict:
    """段階を分けた比較を 1 回で行う: 今の方式で 2 回（baseline・repeat）と候補（candidate）を実行して比べる。
    baseline は本番の結果としてそのまま使う（追加の費用は repeat と candidate の 2 回分）。
    有料。利用者の承認後にだけ使う。結果は out/pilot_decision.json。"""
    base = branch_run(cfg, db_path, record, None, out / "baseline", provider, log)
    rep = branch_run(cfg, db_path, record, None, out / "repeat", provider, log)
    cand = branch_run(cfg, db_path, record, arm, out / "candidate", provider, log)
    res = compare("semantic", base, rep, cand)
    res.update(arm=arm, sample=record["path"], stage=ARMS[arm]["stage"], config=ARMS[arm]["semantic"])
    # 今の方式の 1 回目（baseline）は本番の結果としてそのまま使う: 本番の DB をその DB で置き換える（元は .before_pilot に残す）。
    # 本番を再開すると、見本の資料は照合済みとして扱われ、ほかの資料だけが続きから進む（見本の資料の照合を二重に払わない）
    import shutil
    shutil.copy2(db_path, Path(str(db_path) + ".before_pilot"))
    shutil.copy2(base / "semantic.db", db_path)
    (out / "pilot_decision.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res
