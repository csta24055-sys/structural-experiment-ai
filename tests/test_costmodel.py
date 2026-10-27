"""費用モデル（structlab cost）。API は呼ばない。"""
import json

from structlab import costmodel as CM
from structlab.db import DB
from structlab.reading.semantic_bridge import load_records


def test_prefix_units_priming_pays_off_only_for_large_groups():
    for case in CM.CASES:
        assert CM.prefix_units(10, "none", case) == 10
        assert CM.prefix_units(40, "primed", case) < CM.prefix_units(40, "batch", case)
        # 件数が少ないときは先書きしない（1 回の Batch と同じ）
        assert CM.prefix_units(CM.PRIME_MIN_GROUP - 1, "primed", case) == CM.prefix_units(CM.PRIME_MIN_GROUP - 1, "batch", case)
        assert CM.prefix_units(30, "seq1h", case) < CM.prefix_units(30, "seq5m", case) or case == "high"
    from structlab.ai import anthropic_provider as AP
    assert CM.PRIME_MIN_GROUP == AP.PRIME_MIN_GROUP


def _db(tmp_path):
    rec = {"path": "v.mp4", "kind": "video", "duration": 1500.0, "has_audio": True, "content_hash": "h", "transcriber": "x",
           "scenes": [{"index": 0, "start": 0, "end": 1500, "frame_time": 1, "frame": "", "mode": "slides",
                       "ai": {"title": "t", "text_blocks": [{"text": "必ず単位を書くこと", "role": "body"}]}}],
           "speech": [{"start": 10.0 * k, "end": 10.0 * k + 5, "text": f"説明 {k} の内容を必ず記録してください"} for k in range(140)]}
    db = DB(tmp_path / "s.db")
    load_records(db, {"v.mp4": rec})
    db.close()
    return tmp_path / "s.db"


def test_semantic_cost_after_m24_is_lower_and_candidates_are_reported(tmp_path):
    dbp = _db(tmp_path)
    for case in CM.CASES:
        before = CM.semantic(dbp, case, parallel=False, priming=False)["yen"]
        after = CM.semantic(dbp, case)["yen"]
        assert after <= before
    mid = CM.semantic(dbp, "mid")
    assert mid["docs"] == 1 and mid["rows"]["2 指示の抽出"]["calls"] == 13
    assert CM.semantic(dbp, "mid", prefix_layout=True)["yen"] < mid["yen"]
    assert CM.semantic(dbp, "mid", verify_group=5)["yen"] < mid["yen"]
    assert CM.semantic(dbp, "mid", batch=False)["yen"] > mid["yen"]
    res = CM.report([], dbp, tmp_path / "out")
    saved = json.loads((tmp_path / "out" / "costs.json").read_text(encoding="utf-8"))
    assert saved["savings"] == res["savings"] and saved["semantic_before_M24"]["mid"] >= saved["semantic"]["mid"]["total"]


def test_actions_usage_rounds_each_job_up_to_minutes():
    import importlib.util
    from datetime import datetime, timezone
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("au", Path(__file__).parent.parent / "scripts" / "actions_usage.py")
    au = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(au)
    js = [{"name": "a", "status": "completed", "started_at": "2026-10-21T12:00:00Z", "completed_at": "2026-10-21T12:10:01Z"},
          {"name": "b", "status": "in_progress", "started_at": "2026-10-21T12:00:00Z", "completed_at": None},
          {"name": "c", "status": "queued", "started_at": None, "completed_at": None}]
    r = au.summarize(js, datetime(2026, 10, 21, 12, 30, tzinfo=timezone.utc))
    assert [x["billed_minutes"] for x in r["jobs"]] == [11, 30] and r["billed_minutes"] == 41
