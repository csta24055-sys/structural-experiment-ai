"""解析結果の出典管理・先生の指示とAI推測の区別・10分類の出力。"""
from __future__ import annotations

import json

from conftest import run_pipeline
from structlab.ai import items_from_response
from structlab.models import CATEGORIES


def test_every_item_has_source(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    rows = db.q("SELECT i.*, f.kind FROM items i JOIN files f ON f.id=i.file_id")
    assert rows
    for r in rows:
        assert r["source_path"]
        if r["kind"] == "pdf" and r["origin"] not in ("system",):
            assert r["page"] is not None
        if r["kind"] == "video":
            assert r["t_start"] is not None
    # ZIP 内のファイルは「ZIP名!/内部パス」で出典を示す
    assert db.q1("SELECT COUNT(*) c FROM items WHERE source_path='配布資料.zip!/資料/inner.pdf' AND page=2")["c"] > 0


def test_ai_inference_is_never_instructor_instruction(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    bad = db.q1("SELECT COUNT(*) c FROM items WHERE origin='ai_inference' AND is_instructor_instruction=1")["c"]
    assert bad == 0
    assert db.q1("SELECT COUNT(*) c FROM items WHERE origin='ai_inference'")["c"] > 0


def test_ai_output_validation():
    corpus = "提出期限は11月20日です。PDF形式で提出してください。"
    data = {"items": [
        {"category": "submission_requirements", "statement": "期限は11月20日", "origin": "document_text",
         "is_instructor_instruction": True, "evidence": "提出期限は11月20日です", "confidence": "high", "time_sec": None},
        {"category": "submission_requirements", "statement": "期限は12月1日", "origin": "document_text",
         "is_instructor_instruction": True, "evidence": "提出期限は12月1日です", "confidence": "high", "time_sec": None},
        {"category": "nonexistent", "statement": "x", "origin": "document_text", "is_instructor_instruction": False,
         "evidence": "", "confidence": "low", "time_sec": None},
        {"category": "procedure", "statement": "時刻外", "origin": "speech", "is_instructor_instruction": False,
         "evidence": "PDF形式で提出", "confidence": "low", "time_sec": 9999},
    ], "unreadable": ["右下の表が不鮮明"], "important_moments": []}
    items, rejected = items_from_response(data, corpus, analyzer="t", page=None, t_start=0, t_end=60)
    assert len(rejected) == 1
    ok, hallucinated, outside = items[0], items[1], items[2]
    assert ok.verification == "evidence_matched" and ok.is_instructor_instruction
    # 根拠が原文に無い「指示」は先生の指示として確定しない
    assert hallucinated.verification == "evidence_not_found" and not hallucinated.is_instructor_instruction
    assert outside.t_start == 0  # 区間外の時刻は採用しない
    assert items[3].origin == "unreadable" and items[3].category == "unknowns_contradictions"


def test_knowledge_outputs(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    run_pipeline(cfg)
    kj = json.loads((cfg.results_dir / "knowledge.json").read_text(encoding="utf-8"))
    assert list(kj["categories"]) == list(CATEGORIES)
    item = kj["categories"]["submission_requirements"]["items"][0]
    assert {"file", "page", "time_start", "time_label"} <= set(item["source"])
    assert item["origin_label"]
    # 資料間の矛盾（提出期限 11/20 と 11/27）を検出
    c = [x for x in kj["contradictions"] if x["key"] == "提出期限"][0]
    assert {v["value"] for v in c["values"]} == {"11月20日", "11月27日"}
    md = (cfg.results_dir / "knowledge.md").read_text(encoding="utf-8")
    for title in CATEGORIES.values():
        assert f"## {title}" in md
    assert "AIの推測（原資料で未確認）" in md
    assert "資料間で値が食い違う可能性" in md
    assert "../evil.txt" in md  # 危険なZIPエントリをスキップした記録
    inv = json.loads((cfg.results_dir / "inventory.json").read_text(encoding="utf-8"))
    paths = {r["path"] for r in inv}
    assert "第1回/講義動画.mp4" in paths and "配布資料.zip!/資料/nested.zip!/deep/memo.txt" in paths
    assert all({"kind", "size", "modified", "status"} <= set(r) for r in inv)
    assert list((cfg.results_dir / "files").glob("*.json"))


def test_local_only_mode_marks_gaps(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir, ai={"provider": "none"})
    db, _, _, _ = run_pipeline(cfg, use_mock=False)
    assert db.q1("SELECT COUNT(*) c FROM api_usage")["c"] == 0
    md = (cfg.results_dir / "knowledge.md").read_text(encoding="utf-8")
    assert "AI未解析のため内容が未確認の図・画面" in md
    assert "スキャン画像の可能性" in md


def test_external_ai_requires_explicit_permission(src_dir, cfg_factory):
    from structlab.ai import make_provider
    cfg = cfg_factory(src_dir, ai={"provider": "anthropic", "allow_external": False})
    p, why = make_provider(cfg)
    assert p is None and "許可" in why
    db, _, _, _ = run_pipeline(cfg, use_mock=False)
    assert db.q1("SELECT COUNT(*) c FROM units WHERE ai_status='skipped_no_permission'")["c"] > 0
    assert db.q1("SELECT COUNT(*) c FROM api_usage")["c"] == 0
