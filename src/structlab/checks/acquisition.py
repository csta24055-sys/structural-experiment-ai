"""資料の取得工程のチェック（A1〜A10）。

取得の検証（全件列挙・アップロード途中の除外・content_hash の照合・ZIPの安全な展開・上限・未対応形式）は
既存の処理（scanner / sources / pipeline）の中で行われている。ここでは再実行せず、その結果をファイルごとに記録する。
"""
from __future__ import annotations

import json

from ..db import DB
from .base import clear, record

STAGE = "acquisition"


def record_acquisition(db: DB, cfg, run_id: int | None = None) -> None:
    clear(db, STAGE)
    with db.tx():
        last = db.get_kv("last_scan")
        record(db, stage=STAGE, check_type="scan_complete", target_type="scan", target_id="last",
               status="ok" if last else "unverified",
               detail=(f"一覧取得を最後まで実施: {db.get_kv('last_scan_summary')}" if last else "スキャン未実施"), run_id=run_id)
        ignored = json.loads(db.get_kv("last_scan_ignored") or "[]")
        if ignored:
            record(db, stage=STAGE, check_type="ignored", target_type="scan", target_id="last", status="ok",
                   detail=f"一時ファイル等として対象外にした {len(ignored)} 件: {', '.join(ignored[:10])}", run_id=run_id)
        for f in db.q("SELECT * FROM files WHERE status!='deleted' ORDER BY path"):
            kw = dict(target_type="file", target_id=f["id"], file_id=f["id"], run_id=run_id)
            st, detail = f["status"], f["status_detail"] or ""
            if st == "discovered":
                record(db, stage=STAGE, check_type="upload_stability", status="needs_review",
                       detail=f"{f['path']}: アップロード完了待ちのため未解析（{detail}）", **kw)
                continue
            if st == "unsupported":
                record(db, stage=STAGE, check_type="format_supported", status="unverified",
                       detail=f"{f['path']}: 未対応の形式のため内容を確認していない（{detail}）", **kw)
                continue
            if st == "skipped":
                record(db, stage=STAGE, check_type="size_limit", status="needs_review",
                       detail=f"{f['path']}: 上限のため解析していない（{detail}）", **kw)
                continue
            if st == "duplicate":
                record(db, stage=STAGE, check_type="duplicate", status="ok",
                       detail=f"{f['path']}: {detail}（内容が同一のため1回だけ解析）", **kw)
                continue
            if st in ("failed", "partial", "ready", "processing"):
                record(db, stage=STAGE, check_type="file_processing",
                       status="failed" if st == "failed" else "needs_review",
                       detail=f"{f['path']}: {st} {detail or f['last_error'] or ''}", **kw)
            if st in ("done", "partial"):
                if f["parent_id"] is None:
                    record(db, stage=STAGE, check_type="download_integrity", status="ok",
                           detail=f"{f['path']}: 取得した内容を content_hash と照合済み", **kw)
                cur = f["processed_hash"] == f["content_hash"]
                record(db, stage=STAGE, check_type="version_current", status="ok" if cur else "needs_review",
                       detail=f"{f['path']}: " + ("結果は現在の版に対応" if cur else "結果が現在の版に対応していない"), **kw)
        for u in db.q("SELECT u.*, f.path FROM units u JOIN files f ON f.id=u.file_id WHERE u.unit_type='zip_manifest' "
                      "AND u.file_hash=f.content_hash AND f.status!='deleted'"):
            man = json.loads(u["extract_json"] or "{}")
            sk = man.get("skipped") or []
            record(db, stage=STAGE, check_type="zip_safety", target_type="file", target_id=u["file_id"], file_id=u["file_id"],
                   status="needs_review" if sk else "ok", run_id=run_id,
                   detail=f"{u['path']}: " + (f"安全のため展開しなかったエントリ {len(sk)} 件: {'; '.join(sk[:5])}" if sk
                                             else f"{len(man.get('members') or [])} ファイルを安全に展開"))
