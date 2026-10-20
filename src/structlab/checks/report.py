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


def write_checks_report(db: DB, cfg) -> Path:
    out = cfg.results_dir / "checks"
    out.mkdir(parents=True, exist_ok=True)
    sm = summary(db)
    L = ["# 各工程のチェック結果", "",
         "> 成功＝照合して一致 ／ 不一致＝照合して食い違い ／ 要確認＝機械的に判断できない疑い ／ 未検証＝照合の材料・手段がない ／ チェック不能＝チェック自体が実行できない",
         "> 「未検証」は成功ではありません。", "",
         "| 工程 | " + " | ".join(STATUS_LABEL[s] for s in ORDER) + " |", "|---|" + "---:|" * len(ORDER)]
    for st in STAGE_LABEL:
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
    allrows = [dict(r) for r in db.q("SELECT * FROM checks ORDER BY stage, id")]
    (out / "checks.json").write_text(json.dumps({"summary": sm, "checks": allrows}, ensure_ascii=False, indent=1,
                                                default=str), encoding="utf-8")
    return out / "checks.md"
