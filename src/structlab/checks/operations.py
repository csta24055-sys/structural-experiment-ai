"""運用・全体のチェック（O1〜O5）と、実行の最後にまとめて記録する抽出工程の項目（B14・B15）。

B14（AIでしか読めない内容）・B15（単位ごとのAI抽出の根拠）は抽出工程の担当だが、意味理解のAIが後から
同じ単位を解析することがあるため、実行の最後の時点の状態で記録する。いずれも既存の結果の集約で、再解析はしない。
"""
from __future__ import annotations

import json

from ..db import DB
from .base import clear, record


def record_operations(db: DB, cfg, run_id: int | None = None, *, ai_note: str = "", ai_blocked: str | None = None,
                      external_allowed: bool = False) -> None:
    for ct in ("needs_ai", "legacy_evidence"):
        clear(db, "extraction", check_type=ct)
    clear(db, "operations")
    with db.tx():
        _extraction_tail(db, run_id)
        st = {r["status"]: r["c"] for r in db.q("SELECT status, COUNT(*) c FROM files WHERE parent_id IS NULL GROUP BY status")}
        # 解析していないファイル（未対応形式・上限超過を含む）は「完了」に数えない
        pending = {k: v for k, v in st.items() if k in ("discovered", "ready", "processing", "failed", "partial",
                                                        "unsupported", "skipped")}
        record(db, stage="operations", check_type="processing_completeness", target_type="run", target_id="last",
               status="needs_review" if pending else "ok", actual=st, run_id=run_id,
               detail=("解析が完了していないファイル: " + "、".join(f"{k} {v}" for k, v in pending.items())) if pending
               else f"全ファイルの処理が完了（{st}）")
        _semantic_coverage(db, run_id)
        record(db, stage="operations", check_type="ai_budget", target_type="run", target_id="last",
               status="needs_review" if ai_blocked else "ok", run_id=run_id,
               detail=f"AI使用量の上限で次回に持ち越した処理あり: {ai_blocked}" if ai_blocked else "上限による持ち越しなし")
        sent = db.q1("SELECT COUNT(*) c FROM api_usage WHERE run_id=? AND provider NOT IN ('mock')", (run_id,))["c"] if run_id else 0
        ok = external_allowed or sent == 0
        record(db, stage="operations", check_type="external_permission", target_type="run", target_id="last",
               status="ok" if ok else "mismatch", run_id=run_id,
               detail=(f"外部AIへの送信 {sent} 回（明示的な許可あり）" if sent and external_allowed else
                       f"外部AIへの送信なし（{ai_note or 'AI未使用'}）" if not sent else "許可なしに外部AIへ送信した記録がある"))
        out = (cfg.data["output"].get("dropbox_path") or "").rstrip("/").lower()
        src = (cfg.data["source"].get("path") or "").rstrip("/").lower()
        overlap = bool(out) and cfg.data["source"]["type"] == "dropbox" and (
            (out + "/").startswith(src + "/") or (src + "/").startswith(out + "/"))
        record(db, stage="operations", check_type="source_readonly", target_type="run", target_id="last",
               status="mismatch" if overlap else "ok", run_id=run_id,
               detail="出力先が元資料のフォルダと重なっている" if overlap else
               "元資料は読み取りのみ（取得側に書き込み処理なし・出力先は元資料のフォルダの外）")


def _extraction_tail(db: DB, run_id) -> None:
    rows = db.q("SELECT u.*, f.path FROM units u JOIN files f ON f.id=u.file_id WHERE u.file_hash=f.content_hash "
                "AND u.status='done' AND f.status IN ('done','partial')")
    for u in rows:
        rec = json.loads(u["extract_json"] or "{}")
        if rec.get("needs_ai"):
            done = u["ai_status"] == "done"
            record(db, stage="extraction", check_type="needs_ai", target_type="unit", target_id=u["id"], file_id=u["file_id"],
                   unit_id=u["id"], status="ok" if done else "unverified", run_id=run_id,
                   detail=f"{u['path']}: {'; '.join(rec['needs_ai'])} — " + ("AIで確認済み" if done else f"AI未確認（{u['ai_status']}）"))
    for r in db.q("SELECT i.unit_id, i.file_id, f.path, SUM(i.verification='evidence_not_found') bad, COUNT(*) n "
                  "FROM items i JOIN files f ON f.id=i.file_id WHERE i.analyzer!='local-rules' AND i.file_hash=f.content_hash "
                  "AND i.origin IN ('document_text','speech','screen') GROUP BY i.unit_id"):
        record(db, stage="extraction", check_type="legacy_evidence", target_type="unit", target_id=r["unit_id"],
               file_id=r["file_id"], unit_id=r["unit_id"], status="needs_review" if r["bad"] else "ok", run_id=run_id,
               detail=f"{r['path']}: AIが資料・発言・画面に明記と答えた {r['n']} 項目のうち、根拠を原文で確認できない {r['bad']} 項目")


def _semantic_coverage(db: DB, run_id) -> None:
    from ..semantic.report import collect_coverage
    for c in collect_coverage(db):
        if c["mode"] is None:
            record(db, stage="operations", check_type="semantic_coverage", target_type="file", target_id=c["file"],
                   status="unverified", detail=f"{c['file']}: 意味理解・指示抽出が未実施", run_id=run_id)
            continue
        short = {k: m for k, m in c["metrics"].items() if m["ratio"] is not None and m["ratio"] < 1.0 and not m.get("note")}
        record(db, stage="operations", check_type="semantic_coverage", target_type="file", target_id=c["file"],
               status="needs_review" if short else "ok", actual={k: m["ratio"] for k, m in c["metrics"].items()}, run_id=run_id,
               detail=f"{c['file']}（{c['mode']}）: " + ("、".join(f"{k} {m['ratio']:.0%}（未処理 {', '.join(m['missing'][:3])}）"
                                                      for k, m in short.items()) or "全段階 100%（＝処理した範囲の割合。指示の検出率ではない）"))
