"""途中停止と再開・新規ファイル検出・重複処理の防止・更新の再解析・アップロード途中の除外。"""
from __future__ import annotations

import os
import shutil
import time

import dummy_data
from conftest import run_pipeline
from structlab.ai.mock_provider import MockProvider
from structlab.db import DB
from structlab.scanner import scan
from structlab.sources import LocalSource


def _counts(db):
    return {r["status"]: r["c"] for r in db.q("SELECT status, COUNT(*) c FROM units GROUP BY status")}


def test_stop_and_resume_without_rework(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    stop_file = cfg.work_dir / "STOP"
    calls = {"n": 0}

    def progress():  # 5単位処理したところで停止要求（STOPファイル）
        calls["n"] += 1
        if calls["n"] == 5:
            stop_file.parent.mkdir(parents=True, exist_ok=True)
            stop_file.touch()

    db, _, stats, prov1 = run_pipeline(cfg, on_progress=progress)
    assert stats["stop_reason"] == "STOP ファイルを検出"
    done_first = {r["id"] for r in db.q("SELECT id FROM units WHERE status='done'")}
    assert 0 < len(done_first) < 21
    db.close()

    stop_file.unlink()
    db, _, stats, prov2 = run_pipeline(cfg)
    assert stats["stop_reason"] is None
    # 1回目に完了した単位は2回目で再処理されていない（attempts=1 のまま）
    rows = db.q("SELECT id, attempts, status FROM units WHERE status!='superseded'")
    assert all(r["status"] == "done" for r in rows)
    assert all(r["attempts"] == 1 for r in rows)
    # AIも完了済み単位には再度呼ばれていない
    assert len(prov1.calls) + len(prov2.calls) == db.q1("SELECT COUNT(*) c FROM units WHERE ai_status='done'")["c"]


def test_crash_recovery_resets_running_units(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    # 異常終了を模擬: 処理中のまま残った単位とファイル
    u = db.q1("SELECT id, file_id FROM units WHERE unit_type='pdf_page' LIMIT 1")
    db.x("UPDATE units SET status='running' WHERE id=?", (u["id"],))
    db.x("UPDATE files SET status='processing' WHERE id=?", (u["file_id"],))
    db.close()
    db, _, _, _ = run_pipeline(cfg)
    assert db.q1("SELECT status FROM units WHERE id=?", (u["id"],))["status"] == "done"
    assert db.q1("SELECT status FROM files WHERE id=?", (u["file_id"],))["status"] == "done"


def test_max_runtime_stops_gracefully(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    from structlab.pipeline import Pipeline
    db = DB(cfg.db_path)
    src = LocalSource(src_dir)
    scan(db, src, cfg)
    p = Pipeline(cfg, db, src, provider=MockProvider())
    p.deadline = time.time() - 1
    stats = p.run()
    assert stats["stop_reason"] == "最大実行時間に到達"
    assert db.q1("SELECT COUNT(*) c FROM units WHERE status='done'")["c"] == 0


def test_second_run_does_nothing(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, s1, p1 = run_pipeline(cfg)
    before = [tuple(r) for r in db.q("SELECT id, attempts, ai_attempts, status FROM units ORDER BY id")]
    n_items = db.q1("SELECT COUNT(*) c FROM items")["c"]
    db.close()
    db, res, s2, p2 = run_pipeline(cfg)
    assert res.new == [] and res.changed == []
    assert s2["units_done"] == 0 and len(p2.calls) == 0
    assert [tuple(r) for r in db.q("SELECT id, attempts, ai_attempts, status FROM units ORDER BY id")] == before
    assert db.q1("SELECT COUNT(*) c FROM items")["c"] == n_items


def test_new_file_detected_later(src_dir, cfg_factory, tmp_path):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    db.close()
    dummy_data.make_pdf(src_dir / "第3回/追加資料.pdf", deadline="12月4日")
    db, res, stats, prov = run_pipeline(cfg)
    assert res.new == ["第3回/追加資料.pdf"]
    assert stats["files_done"] == 1
    assert {c.source_label.split(" ")[0] for c in prov.calls} == {"第3回/追加資料.pdf"}


def test_identical_copy_is_not_reanalyzed(src_dir, cfg_factory):
    shutil.copy(src_dir / "第1回/実験指導書.pdf", src_dir / "第1回/実験指導書のコピー.pdf")
    cfg = cfg_factory(src_dir)
    db, _, stats, _ = run_pipeline(cfg)
    row = db.q1("SELECT status, status_detail FROM files WHERE path='第1回/実験指導書のコピー.pdf'")
    assert row["status"] == "duplicate" and "実験指導書.pdf" in row["status_detail"]
    assert db.q1("SELECT COUNT(*) c FROM units u JOIN files f ON f.id=u.file_id WHERE f.path LIKE '%コピー%'")["c"] == 0


def test_updated_file_is_reanalyzed_and_old_results_hidden(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    fid = db.q1("SELECT id FROM files WHERE path='第2回/補足資料.pdf'")["id"]
    db.close()
    dummy_data.make_pdf(src_dir / "第2回/補足資料.pdf", deadline="12月11日", pages_extra=1)
    db, res, _, prov = run_pipeline(cfg)
    assert res.changed == ["第2回/補足資料.pdf"]
    assert db.q1("SELECT COUNT(*) c FROM units WHERE file_id=? AND status='superseded'", (fid,))["c"] == 3
    assert db.q1("SELECT COUNT(*) c FROM units WHERE file_id=? AND status='done'", (fid,))["c"] == 4
    from structlab.report import current_items
    texts = " ".join(i["statement"] for i in current_items(db) if i["file_id"] == fid)
    assert "12月11日" in texts and "11月27日" not in texts


def test_deleted_file_is_excluded(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    db.close()
    (src_dir / "第1回/板書.png").unlink()
    db, res, _, _ = run_pipeline(cfg)
    assert res.deleted == ["第1回/板書.png"]
    from structlab.report import current_items
    assert not [i for i in current_items(db) if i["source_path"] == "第1回/板書.png"]


def test_files_still_uploading_are_not_analyzed(tmp_path, cfg_factory):
    src = tmp_path / "src"
    dummy_data.make_pdf(src / "a.pdf")
    old = time.time() - 3600
    os.utime(src / "a.pdf", (old, old))
    dummy_data.make_pdf(src / "uploading.pdf")  # 今まさに書き込まれたファイル
    (src / "~$測定.xlsx").write_bytes(b"lock")  # Excel のロックファイル
    (src / "video.mp4.part").write_bytes(b"partial")
    cfg = cfg_factory(src, stability={"min_age_seconds": 300, "min_stable_seconds": 300})
    db, res, _, _ = run_pipeline(cfg)
    assert res.ready == ["a.pdf"] and res.waiting == ["uploading.pdf"]
    assert sorted(res.ignored) == ["video.mp4.part", "~$測定.xlsx"]
    assert db.q1("SELECT status FROM files WHERE path='uploading.pdf'")["status"] == "discovered"
    # 10分後のスキャンでは解析対象になる
    db2 = DB(cfg.db_path)
    res2 = scan(db2, LocalSource(src), cfg, now=time.time() + 600)
    assert "uploading.pdf" in res2.ready


def test_file_changed_between_scan_and_fetch(tmp_path, cfg_factory):
    src = tmp_path / "src"
    dummy_data.make_pdf(src / "a.pdf")
    cfg = cfg_factory(src)
    db = DB(cfg.db_path)
    s = LocalSource(src)
    scan(db, s, cfg)
    dummy_data.make_pdf(src / "a.pdf", pages_extra=2)  # スキャン後に上書き（アップロード中）
    from structlab.pipeline import Pipeline
    Pipeline(cfg, db, s, provider=None).run()
    row = db.q1("SELECT status, status_detail FROM files WHERE path='a.pdf'")
    assert row["status"] == "discovered" and "変更" in row["status_detail"]
    assert db.q1("SELECT COUNT(*) c FROM units")["c"] == 0
