"""チェック項目の全体設計（カタログ）と実装の一致、および全体設計で追加した各項目。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import dummy_data
from conftest import needs_ffmpeg, run_pipeline
from structlab.checks.catalog import BY_TYPE, CATALOG, REQUIREMENTS, STAGE_ORDER, coverage_matrix
from test_semantic import _cfg, _pdf

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_structure():
    ids = [c["id"] for c in CATALOG]
    assert len(ids) == len(set(ids))
    assert len(BY_TYPE) == len(CATALOG)  # 工程×種類が一意
    m = coverage_matrix()
    for r in REQUIREMENTS:  # すべての要件を、少なくとも1つの工程の項目が担当している
        assert any(m[r][s] for s in STAGE_ORDER), r
    for c in CATALOG:
        assert c["stage"] in STAGE_ORDER and set(c["requirements"]) <= set(REQUIREMENTS)
        assert c["mode"] in ("runtime", "design", "future")
        if c["mode"] == "design":
            assert c["tests"], c["id"]


def test_design_items_are_backed_by_existing_tests():
    names = set()
    for p in (ROOT / "tests").glob("test_*.py"):
        names |= set(re.findall(r"^def (test_\w+)", p.read_text(encoding="utf-8"), re.M))
    for c in CATALOG:
        for t in c["tests"]:
            assert t in names, (c["id"], t)


@pytest.fixture
def full_run(tmp_path, cfg_factory):
    """動画・PDF・Excel・ZIP・一時ファイル・未対応形式を含む一式を、模擬AIで最後まで解析する。"""
    src = tmp_path / "src"
    dummy_data.make_video(src / "講義/第1回_データ整理.mp4", tmp_path / "w")
    _pdf(src / "資料/レポート作成説明.pdf", ["レポートはWordで提出してください。", "断面積は78.5 mm2とする。",
                                           "試験機には触ってもよいです。"])
    dummy_data.make_excel(src / "データ/測定.xlsx", rows=5)
    inner = dummy_data.make_pdf(tmp_path / "inner.pdf")
    dummy_data.make_zip(src / "配布.zip", {"資料/inner.pdf": inner, "../evil.txt": b"x"})
    (src / "~$lock.xlsx").write_bytes(b"lock")
    (src / "古い.xls").write_bytes(b"\xd0\xcf\x11\xe0")
    cfg = _cfg(cfg_factory, src, semantic={"ai": True})
    db, _, _, _ = run_pipeline(cfg)
    return cfg, db


@needs_ffmpeg
def test_every_recorded_check_is_designed_and_core_items_run(full_run):
    cfg, db = full_run
    recorded = {(r["stage"], r["check_type"]) for r in db.q("SELECT DISTINCT stage, check_type FROM checks")}
    assert recorded - set(BY_TYPE) == set()  # カタログに無いチェックを勝手に作っていない
    core = {(c["stage"], c["check_type"]) for c in CATALOG if c["mode"] == "runtime" and not c["conditional"]}
    assert core - recorded == set()  # 状況に依存しない項目は必ず記録される
    for k in [("acquisition", "ignored"), ("acquisition", "zip_safety"), ("acquisition", "format_supported"),
              ("extraction", "unit_coverage"), ("extraction", "chart_refs"), ("extraction", "table_structure"),
              ("understanding", "scene_neighbors"), ("understanding", "apparent_contradictions"),
              ("understanding", "scene_role_coverage"), ("directives", "time_located"), ("integration", "numeric")]:
        assert k in recorded, k
    md = (cfg.results_dir / "checks" / "catalog.md").read_text(encoding="utf-8")
    assert "カタログ未登録" not in md and "| **R15**" in md and "仕組みで保証" in md
    st = {(r["check_type"]): r["status"] for r in db.q("SELECT * FROM checks WHERE stage='acquisition'")}
    assert st["zip_safety"] == "needs_review" and st["format_supported"] == "unverified" and st["ignored"] == "ok"
    ops = {r["check_type"]: r for r in db.q("SELECT * FROM checks WHERE stage='operations'")}
    assert ops["external_permission"]["status"] == "ok" and ops["source_readonly"]["status"] == "ok"
    assert ops["processing_completeness"]["status"] == "needs_review"  # 未対応形式が残っている


@needs_ffmpeg
def test_unit_coverage_detects_gap(full_run):
    cfg, db = full_run
    from structlab.checks.extraction import check_file
    f = db.q1("SELECT * FROM files WHERE kind='video'")
    assert db.q1("SELECT status FROM checks WHERE check_type='unit_coverage' AND file_id=?", (f["id"],))["status"] == "ok"
    u = db.q1("SELECT * FROM units WHERE file_id=? AND unit_type='video_segment' ORDER BY seq LIMIT 1 OFFSET 1", (f["id"],))
    db.x("UPDATE units SET status='superseded', file_hash='old' WHERE id=?", (u["id"],))  # 区間が1つ欠けた状態
    check_file(db, cfg, f, None)
    row = db.q1("SELECT * FROM checks WHERE check_type='unit_coverage' AND file_id=?", (f["id"],))
    assert row["status"] == "mismatch" and "隙間" in row["detail"]


@needs_ffmpeg
def test_inference_never_gets_teacher_speaker(tmp_path, cfg_factory):
    src = tmp_path / "src"
    dummy_data.make_video(src / "v.mp4", tmp_path / "w")
    cfg = _cfg(cfg_factory, src, semantic={"ai": True, "video_speaker": "teacher"})
    db, _, _, _ = run_pipeline(cfg)
    inf = db.q("SELECT * FROM directives WHERE origin='ai_inference'")
    assert inf and all(d["speaker"] == "none" for d in inf)
    assert db.q1("SELECT status FROM checks WHERE check_type='inference_separation'")["status"] == "ok"


def test_table_and_chart_reference_checks(tmp_path):
    from structlab.checks.extraction import _chart_refs, _pdf
    from structlab.db import DB
    db = DB(tmp_path / "s.db")
    u = {"id": 1}
    _pdf(db, u, {"page": 1}, {"text": "x"}, {"tables": ["| a | b |\n|---|---|\n| 1 | 2 | 3 |"]}, {"file_id": None})
    assert db.q1("SELECT status FROM checks WHERE check_type='table_structure'")["status"] == "needs_review"
    _chart_refs(db, u, {"sheets": [{"name": "データ"}], "charts": [{"title": "g", "series": [{"val": "'別シート'!$B$2:$B$5"}]}]},
                {"file_id": None})
    assert db.q1("SELECT status FROM checks WHERE check_type='chart_refs'")["status"] == "mismatch"


def test_style_pdf_not_in_repository():
    """本人の文章スタイルPDFとその読み取り結果はリポジトリに含めない。"""
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    assert not [f for f in files if re.search(r"writing_style|文章スタイル.*\.pdf|style_profile|work/style", f)]
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*writing_style*.pdf" in gi and "work/" in gi


def test_style_checks_are_recorded(tmp_path, cfg_factory):
    from structlab.ai.mock_provider import MockProvider
    from structlab.db import DB
    from structlab.style.apply import apply_style
    from structlab.style.profile import build_profile
    from test_style_feedback import ORIG, make_style_pdf
    src = tmp_path / "src"
    src.mkdir()
    cfg = cfg_factory(src, style={"enabled": True})
    db = DB(cfg.db_path)
    build_profile(make_style_pdf(tmp_path / "s.pdf"), cfg.work_dir / "style" / "profile.json", db=db)
    apply_style(db, cfg, ORIG, provider=MockProvider())
    apply_style(db, cfg, "[[STYLE_BREAK]]" + ORIG, provider=MockProvider())
    rows = {r["status"] for r in db.q("SELECT * FROM checks WHERE stage='style' AND check_type='preservation'")}
    assert rows == {"ok", "mismatch"}
    scope = db.q1("SELECT * FROM checks WHERE check_type='profile_scope'")
    assert scope["status"] == "ok" and "除外" in scope["detail"]
    assert json.loads(scope["actual"])["parsed"] == 10
