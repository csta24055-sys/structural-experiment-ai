#!/usr/bin/env python3
"""見本の比較 P-G5: Claude Code（G5）の答えが API の答えと同じ品質かを、依頼ごとに比べる。

同じ依頼（G5 の受け渡し口に書き出した依頼を、API の形に作り直したもの）を API で 2 回、G5 で 1 回答えさせ、
「API どうしの揺れ」を基準に G5 の答えが API の答えの項目を落としていないかを見る。

  python3 scripts/pg5.py select  [--work work/g5]                見本の依頼を選ぶ（種類ごと。無料）
  python3 scripts/pg5.py run-api --allow-external-ai [--runs 2]  見本を API で答えさせる（有料、約 1,000 円。承認後だけ）
  python3 scripts/pg5.py compare                                 比べて判定（無料）→ work/g5/pg5/decision.json・decision.md

判定（すべて満たすときだけ合格）:
  1. G5 の答えはすべて出力の形に合う（取り込み時に検査済み）
  2. 種類ごとに、API1 の項目が G5 に含まれる割合 ≥ API1 の項目が API2 に含まれる割合（許容差なし。M28 で 0.02 の許容差を撤廃。
     小数の計算の誤差だけは EPS=1e-9 で吸収する）
  3. 重要な項目（数値・セル番地・数式・設定・禁止/必須/条件付きの指示・照合の判定）で、G5 だけが落とした数が API2 以下
  4. G5 の「読めない・不確か」の件数が API の 2 回の平均の 1.25 倍＋1 以下（画像が縮小されて読めていない等の検出）
不合格の種類（例: 細部の読み取り）があれば、その種類だけ API に戻すことを提案する（G5 と A の組み合わせ）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

#: 種類ごとの見本の数（費用の見積もり: docs/OPTIMIZATION_M27.md）
STRATA = {"read_pdf_page": 3, "read_video_scene": 2, "read_experiment_windows": 4, "read_video_detail": 3,
          "read_video_zoom": 2, "fix_speech": 2, "scene_analysis": 3, "scene_reinterpret": 3,
          "directive_extraction": 3, "directive_verify": 4, "global_synthesis": 1,
          # 本番で使う場合だけ現れる種類も 1 件ずつ（無ければ選ばない）
          "global_synthesis_part": 1, "corpus_integration": 1, "experiment_correspondence": 1,
          "scene_reinterpret_delta": 2, "directive_verify_group": 2}
NUM = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?")


def P(work: Path) -> dict:
    return {"queue": work / "queue", "pg5": work / "pg5"}


def select(work: Path) -> list[str]:
    from structlab.ai.queue_provider import Queue
    q = Queue(P(work)["queue"])
    by: dict[str, list[dict]] = {}
    for p in sorted((q.root / "requests").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        by.setdefault(r["task"], []).append(r)
    chosen = []
    for task, n in STRATA.items():
        rs = by.get(task, [])
        # 資料が偏らないように、資料ごとに順に取る（同じ並びで毎回同じ見本）
        groups: dict[str, list[dict]] = {}
        for r in rs:
            groups.setdefault(r["source_label"].split(" ")[0], []).append(r)
        order = [g for g in sorted(groups.values(), key=lambda g: g[0]["source_label"])]
        k = 0
        while len([c for c in chosen if c["task"] == task]) < min(n, len(rs)):
            g = order[k % len(order)]
            if g:
                chosen.append(g.pop(0))
            k += 1
    out = P(work)["pg5"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "sample.json").write_text(json.dumps([{"id": c["id"], "task": c["task"], "source_label": c["source_label"]}
                                                  for c in chosen], ensure_ascii=False, indent=1), encoding="utf-8")
    return [c["id"] for c in chosen]


def run_api(work: Path, runs: int, provider=None, config: str = "configs/dropbox.yaml") -> None:
    """有料。見本を API の Batch で runs 回答えさせ、pg5/api{n}/<id>.json に保存する（保存済みは送らない）。"""
    from structlab.ai.queue_provider import Queue, api_request
    if provider is None:
        from structlab.ai import make_provider
        from structlab.config import load_config
        cfg = load_config(config)
        if cfg.data["ai"]["provider"] != "anthropic":
            raise SystemExit("API の設定（ai.provider: anthropic）で実行してください")
        provider, why = make_provider(cfg, allow_external_flag=True)
        if provider is None:
            raise SystemExit(f"API を使えません: {why}")
    q = Queue(P(work)["queue"])
    sample = json.loads((P(work)["pg5"] / "sample.json").read_text(encoding="utf-8"))
    for n in range(1, runs + 1):
        d = P(work)["pg5"] / f"api{n}"
        d.mkdir(parents=True, exist_ok=True)
        todo = [s for s in sample if not (d / f"{s['id']}.json").exists()]
        if not todo:
            continue
        reqs = [api_request(q.request(s["id"])) for s in todo]
        res = provider.analyze_batch(reqs, log=print)
        for s, r in zip(todo, res):
            if isinstance(r, Exception):
                print(f"  失敗: {s['id']} {r}")
                continue
            (d / f"{s['id']}.json").write_text(json.dumps({"data": r.data, "model": r.model, "input_tokens": r.input_tokens,
                                                           "output_tokens": r.output_tokens}, ensure_ascii=False, indent=1),
                                               encoding="utf-8")


def items(task: str, data) -> dict[str, set[str]]:
    """答えから比べる項目。重要な項目は critical に入れる。"""
    from structlab.pilot import CRITICAL_CLASSES, _norm, reading_items
    out: dict[str, set[str]] = {"critical": set(), "text": set(), "unreadable": set()}
    if task.startswith("read_") and isinstance(data, dict) and task != "read_experiment_windows":
        ri = reading_items(data)
        for k, v in ri.items():
            (out["critical"] if k in ("number", "cell", "formula", "setting") else out["text"]).update(f"{k}:{x}" for x in v)
    ds = []
    if isinstance(data, dict):
        ds = data.get("directives") or data.get("additional_directives") or []
        if data.get("verdict"):
            out["critical"].add(f"verdict:{data['verdict']}")
    for d in ds:
        key = f"{d.get('directive_class')}|{_norm(d.get('content'))}"
        (out["critical"] if d.get("directive_class") in CRITICAL_CLASSES else out["text"]).add(key)

    def walk(x, path=""):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for v in x:
                walk(v, path)
        elif isinstance(x, str) and x.strip():
            if any(w in path for w in ("unreadable", "uncertain", "unconfirmed", "open_questions")):
                out["unreadable"].add(_norm(x))
            else:
                if len(_norm(x)) >= 4:
                    out["text"].add(_norm(x))
                for n in NUM.findall(x):
                    out["critical"].add(f"num:{n.replace(',', '')}")
    walk(data)
    return out


#: 小数の平均の計算誤差だけを吸収する値（品質の許容差ではない）
EPS = 1e-9
RULE = "種類ごとに: G5 の包含率 ≥ API どうしの包含率（許容差なし）、重要項目の欠落 ≤ API どうし、読めない件数 ≤ API の 1.25 倍＋1"


def judge(ca: float, cg: float, crit_api: int, crit_g5: int, unread_api: float, unread_g5: int) -> bool:
    """1 種類の合否。ca = API1 の項目が API2 に含まれる割合、cg = API1 の項目が G5 に含まれる割合（いずれも平均）。"""
    return cg + EPS >= ca and crit_g5 <= crit_api and unread_g5 <= 1.25 * unread_api + 1


def compare(work: Path) -> dict:
    from structlab.pilot import containment
    from structlab.ai.queue_provider import Queue
    q = Queue(P(work)["queue"])
    pg = P(work)["pg5"]
    sample = json.loads((pg / "sample.json").read_text(encoding="utf-8"))
    by: dict[str, dict] = {}
    missing = []
    for s in sample:
        a1, a2 = pg / "api1" / f"{s['id']}.json", pg / "api2" / f"{s['id']}.json"
        g = q.root / "answers" / f"{s['id']}.json"
        if not (a1.exists() and a2.exists() and g.exists()):
            missing.append(s["id"])
            continue
        load = lambda p: json.loads(p.read_text(encoding="utf-8"))["data"]  # noqa: E731
        i1, i2, ig = (items(s["task"], load(p)) for p in (a1, a2, g))
        row = by.setdefault(s["task"], {"n": 0, "api_api": [], "api_g5": [], "crit_api": 0, "crit_g5": 0,
                                        "unread_api": 0.0, "unread_g5": 0})
        base = {k: v for k, v in i1.items() if k != "unreadable"}
        fz = not s["task"].startswith("read_")  # 読み取りは完全一致で比べる（意味理解は言い回しの揺れを許す）
        c_rep, m_rep = containment(base, {k: v for k, v in i2.items() if k != "unreadable"}, fuzzy=fz)
        c_g5, m_g5 = containment(base, {k: v for k, v in ig.items() if k != "unreadable"}, fuzzy=fz)
        row["n"] += 1
        row["api_api"].append(c_rep)
        row["api_g5"].append(c_g5)
        row["crit_api"] += len(m_rep.get("critical", []))
        row["crit_g5"] += len(m_g5.get("critical", []))
        row["unread_api"] += (len(i1["unreadable"]) + len(i2["unreadable"])) / 2
        row["unread_g5"] += len(ig["unreadable"])
    verdict = {}
    for task, r in by.items():
        ca = sum(r["api_api"]) / r["n"]
        cg = sum(r["api_g5"]) / r["n"]
        ok = judge(ca, cg, r["crit_api"], r["crit_g5"], r["unread_api"], r["unread_g5"])
        verdict[task] = {"pass": ok, "requests": r["n"], "containment_api_vs_api": round(ca, 3),
                         "containment_api_vs_g5": round(cg, 3), "critical_missing_api2": r["crit_api"],
                         "critical_missing_g5": r["crit_g5"], "unreadable_api_avg": r["unread_api"],
                         "unreadable_g5": r["unread_g5"]}
    res = {"pass": bool(verdict) and not missing and all(v["pass"] for v in verdict.values()),
           "missing_answers": missing, "by_task": verdict,
           "rule": RULE}
    pg.mkdir(parents=True, exist_ok=True)
    (pg / "decision.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 見本の比較 P-G5 の判定", "", f"**{'合格' if res['pass'] else '不合格'}**（不足 {len(missing)} 件）", "",
             "| 種類 | 件数 | API どうし | API と G5 | 重要項目の欠落（API2 / G5） | 読めない（API 平均 / G5） | 判定 |", "|---|---|---|---|---|---|---|"]
    for t, v in verdict.items():
        lines.append(f"| {t} | {v['requests']} | {v['containment_api_vs_api']} | {v['containment_api_vs_g5']} | "
                     f"{v['critical_missing_api2']} / {v['critical_missing_g5']} | {v['unreadable_api_avg']} / {v['unreadable_g5']} | "
                     f"{'合格' if v['pass'] else '不合格（この種類は API に戻す）'} |")
    (pg / "decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["select", "run-api", "compare"])
    ap.add_argument("--work", default=str(ROOT / "work" / "g5"))
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--allow-external-ai", action="store_true")
    ap.add_argument("-c", "--config", default=str(ROOT / "configs" / "dropbox.yaml"))
    a = ap.parse_args()
    work = Path(a.work)
    if a.action == "select":
        ids = select(work)
        print(f"見本 {len(ids)} 件: {P(work)['pg5'] / 'sample.json'}")
    elif a.action == "run-api":
        if not a.allow_external_ai:
            raise SystemExit("有料の比較です。利用者の承認後に --allow-external-ai を付けて実行してください")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY がありません。Claude Code のクラウド環境の設定に ANTHROPIC_API_KEY を登録し、"
                             "新しいセッションで同じ作業フォルダ（work/g5）から実行してください（依頼の画像はこの環境にだけある）")
        run_api(work, a.runs, config=a.config)
    else:
        res = compare(work)
        print(("合格" if res["pass"] else "不合格") + f": {P(work)['pg5'] / 'decision.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
