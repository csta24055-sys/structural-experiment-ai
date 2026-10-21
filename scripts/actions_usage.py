#!/usr/bin/env python3
"""GitHub Actions の実行時間（ジョブごとの分）を記録する。非公開リポジトリでは月の無料枠から差し引かれるので、
費用（利用枠）として記録する。GitHub の API をジョブの GITHUB_TOKEN で読むだけ（読み取りのみ）。

  python3 scripts/actions_usage.py --out corpus/costs/actions_usage.json
環境変数: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID（Actions が自動で設定）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.request
from datetime import datetime


def _t(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def jobs(repo: str, run_id: str, token: str) -> list[dict]:
    out, page = [], 1
    while True:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}",
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        out += data.get("jobs", [])
        if len(out) >= data.get("total_count", 0) or not data.get("jobs"):
            return out
        page += 1


def summarize(js: list[dict], now: datetime | None = None) -> dict:
    rows = []
    for j in js:
        a, b = _t(j.get("started_at")), _t(j.get("completed_at")) or now
        if not a or not b:
            continue
        sec = max(0.0, (b - a).total_seconds())
        rows.append({"job": j.get("name"), "status": j.get("status"), "seconds": round(sec),
                     "billed_minutes": math.ceil(sec / 60) if sec else 0})  # ジョブごとに分単位で切り上げ
    return {"jobs": rows, "billed_minutes": sum(r["billed_minutes"] for r in rows),
            "note": "非公開リポジトリは月の無料枠（プランにより 2,000〜3,000 分）から差し引かれる。"
                    "実行中のジョブ（この記録を書いているジョブ）は記録した時点までの時間"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo, run_id, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_TOKEN")
    if not (repo and run_id and token):
        print("GitHub Actions の外では実行時間を取得できません")
        return 0
    from datetime import timezone
    res = summarize(jobs(repo, run_id, token), datetime.now(timezone.utc))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"GitHub Actions の実行時間: 合計 {res['billed_minutes']} 分（{len(res['jobs'])} ジョブ）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
