"""チェック結果の出力（results/checks/）。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..db import DB
from .base import STAGE_LABEL, STATUS_LABEL

ORDER = ["mismatch", "failed", "needs_review", "unverified", "ok"]


def summary(db: DB) -> dict:
    out: dict = defaultdict(lambda: defaultdict(int))
    for r in db.q("SELECT stage, status, COUNT(*) c FROM checks GROUP BY stage, status"):
        out[r["stage"]][r["status"]] = r["c"]
    return {k: dict(v) for k, v in out.items()}


MODE_LABEL = {"runtime": "実行時に記録", "design": "仕組みで保証（テストで確認）", "future": "提出物・最終工程で実行"}


def catalog_rows(db: DB) -> tuple[list[dict], list[tuple[str, str]]]:
    """カタログの各項目に最新の結果を付ける。カタログに無いチェック（未登録）も返す。"""
    from .catalog import BY_TYPE, CATALOG
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for r in db.q("SELECT stage, check_type, status, COUNT(*) c FROM checks GROUP BY stage, check_type, status"):
        counts[(r["stage"], r["check_type"])][r["status"]] = r["c"]
    rows = []
    for c in CATALOG:
        rows.append({**c, "results": counts.get((c["stage"], c["check_type"]), {})})
    orphans = [k for k in counts if k not in BY_TYPE]
    return rows, orphans


def write_catalog_report(db: DB, cfg) -> Path:
    from .catalog import REQUIREMENTS, STAGE_ORDER, coverage_matrix
    out = cfg.results_dir / "checks"
    out.mkdir(parents=True, exist_ok=True)
    rows, orphans = catalog_rows(db)
    m = coverage_matrix()
    L = ["# チェック項目の全体設計と実施状況", "",
         "システム全体の検証要件（R1〜R15）を、各工程のチェック項目で分担しています。下の表は「どの要件を、どの工程のどの項目が担当するか」です。",
         "", "| 要件 | " + " | ".join(STAGE_LABEL[s] for s in STAGE_ORDER) + " |", "|---|" + "---|" * len(STAGE_ORDER)]
    for r, desc in REQUIREMENTS.items():
        L.append(f"| **{r}** {desc} | " + " | ".join(", ".join(m[r][s]) or "" for s in STAGE_ORDER) + " |")
    L += ["", "## 項目と最新の結果", "",
          "| ID | 工程 | 要件 | 項目 | 方法 | 実施 | 由来 | 最新の結果 |", "|---|---|---|---|---|---|---|---|"]
    for c in rows:
        res = "、".join(f"{STATUS_LABEL[k]} {v}" for k, v in sorted(c["results"].items(), key=lambda x: ORDER.index(x[0])))
        if not res:
            res = ("テスト: " + ", ".join(c["tests"])) if c["mode"] == "design" else \
                  "未実行（該当する状況・工程がまだ無い）" if c["conditional"] or c["mode"] == "future" else "未実行"
        L.append(f"| {c['id']} | {STAGE_LABEL[c['stage']]} | {','.join(c['requirements'])} | {c['title']} | {c['how']} | "
                 f"{MODE_LABEL[c['mode']]}{'（AI使用時）' if c['ai'] else ''} | {'既存' if c['origin'] == 'existing' else '今回'} | {res} |")
    if orphans:
        L += ["", "## カタログ未登録のチェック（設計の見直しが必要）"] + [f"- {s}/{t}" for s, t in orphans]
    (out / "catalog.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return out / "catalog.md"


def write_checks_report(db: DB, cfg) -> Path:
    out = cfg.results_dir / "checks"
    out.mkdir(parents=True, exist_ok=True)
    sm = summary(db)
    L = ["# 各工程のチェック結果", "",
         "> 成功＝照合して一致 ／ 不一致＝照合して食い違い ／ 要確認＝機械的に判断できない疑い ／ 未検証＝照合の材料・手段がない ／ チェック不能＝チェック自体が実行できない",
         "> 「未検証」は成功ではありません。全体設計（どの要件をどの工程が担当するか）は catalog.md を参照。", "",
         "| 工程 | " + " | ".join(STATUS_LABEL[s] for s in ORDER) + " |", "|---|" + "---:|" * len(ORDER)]
    for st in STAGE_LABEL:  # 工程の順に並ぶ（取得 → 抽出 → … → 運用・全体）
        if st in sm:
            L.append(f"| {STAGE_LABEL[st]} | " + " | ".join(str(sm[st].get(s, 0)) for s in ORDER) + " |")
    rows = db.q("SELECT c.*, f.path FROM checks c LEFT JOIN files f ON f.id=c.file_id WHERE c.status!='ok' "
                "ORDER BY c.stage, CASE c.status WHEN 'mismatch' THEN 0 WHEN 'failed' THEN 1 WHEN 'needs_review' THEN 2 "
                "ELSE 3 END, c.id")
    by = defaultdict(list)
    for r in rows:
        by[r["stage"]].append(r)
    for st in STAGE_LABEL:
        if not by.get(st):
            continue
        L += ["", f"## {STAGE_LABEL[st]}（成功以外 {len(by[st])} 件）"]
        for r in by[st][:500]:
            L.append(f"- [{STATUS_LABEL[r['status']]}] {r['check_type']} {r['path'] or ''} — {r['detail']}")
    revs = db.q("SELECT * FROM revisions ORDER BY id DESC LIMIT 200")
    if revs:
        L += ["", "## 修正案（独立した再検証による。未適用）"]
        L += [f"- {r['target_type']} #{r['target_id']} {r['field']}: {r['before']} → {r['after']}（{r['reason']}）" for r in revs]
    (out / "checks.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    write_catalog_report(db, cfg)
    allrows = [dict(r) for r in db.q("SELECT * FROM checks ORDER BY stage, id")]
    (out / "checks.json").write_text(json.dumps({"summary": sm, "checks": allrows}, ensure_ascii=False, indent=1,
                                                default=str), encoding="utf-8")
    return out / "checks.md"
