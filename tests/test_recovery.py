"""読み込み失敗時の記録・再試行・復旧。"""
from __future__ import annotations

import dummy_data
from conftest import run_pipeline
from structlab.ai.mock_provider import MockProvider
from structlab.cli import main
from structlab.processors import pdf as pdfmod


def test_corrupt_file_recorded_and_retried_up_to_limit(tmp_path, cfg_factory):
    src = tmp_path / "src"
    dummy_data.make_pdf(src / "ok.pdf")
    (src / "broken.pdf").write_bytes(b"%PDF-1.4 this is not a pdf")
    cfg = cfg_factory(src, limits={"max_attempts": 2})
    for i in range(3):
        db, _, stats, _ = run_pipeline(cfg)
        row = db.q1("SELECT * FROM files WHERE path='broken.pdf'")
        db.close()
    assert row["status"] == "failed" and row["attempts"] == 2
    db, _, _, _ = run_pipeline(cfg)
    assert db.q1("SELECT status FROM files WHERE path='ok.pdf'")["status"] == "done"
    errs = db.q("SELECT * FROM errors WHERE file_id=?", (row["id"],))
    assert len(errs) == 2 and all(e["stage"] == "plan" for e in errs)
    knowledge = (cfg.results_dir / "status.md").read_text(encoding="utf-8")
    assert "broken.pdf" in knowledge


def test_transient_unit_failure_recovers_next_run(tmp_path, cfg_factory, monkeypatch):
    src = tmp_path / "src"
    dummy_data.make_pdf(src / "a.pdf")
    cfg = cfg_factory(src)
    orig = pdfmod.PDFProcessor.extract
    state = {"fail": True}

    def flaky(self, path, unit, ctx):
        if unit.locator["page"] == 2 and state["fail"]:
            raise OSError("一時的な読み込みエラー（模擬）")
        return orig(self, path, unit, ctx)

    monkeypatch.setattr(pdfmod.PDFProcessor, "extract", flaky)
    db, _, stats, _ = run_pipeline(cfg)
    assert stats["units_failed"] == 1
    u = db.q1("SELECT * FROM units WHERE locator LIKE '%\"page\": 2%'")
    assert u["status"] == "failed" and "一時的" in u["last_error"]
    assert db.q1("SELECT status FROM files")["status"] == "ready"  # 次回再試行
    md = (cfg.results_dir / "knowledge.md").read_text(encoding="utf-8")
    assert "解析に失敗した部分" in md and "一時的な読み込みエラー" in md
    db.close()
    state["fail"] = False
    db, _, stats, _ = run_pipeline(cfg)
    u = db.q1("SELECT * FROM units WHERE id=?", (u["id"],))
    assert u["status"] == "done" and u["attempts"] == 2
    assert db.q1("SELECT status FROM files")["status"] == "done"
    # 成功済みの p.1, p.3 は再処理されていない
    assert {r["attempts"] for r in db.q("SELECT attempts FROM units WHERE id!=?", (u["id"],))} == {1}


def test_ai_failure_is_retried_on_next_run(tmp_path, cfg_factory):
    src = tmp_path / "src"
    (src).mkdir()
    (src / "memo.txt").write_text("[[AI_FAIL]] 提出期限は11月20日です。必ず守ること。", encoding="utf-8")
    cfg = cfg_factory(src)
    db, _, stats, _ = run_pipeline(cfg, provider=MockProvider(fail_times=1))
    assert stats["ai_failed"] == 1
    assert db.q1("SELECT ai_status FROM units")["ai_status"] == "failed"
    # ローカル抽出結果は失われていない
    assert db.q1("SELECT COUNT(*) c FROM items WHERE analyzer='local-rules'")["c"] > 0
    db.close()
    db, _, stats, prov = run_pipeline(cfg, provider=MockProvider(fail_times=0))
    assert len(prov.calls) == 1
    assert db.q1("SELECT ai_status, ai_attempts FROM units")["ai_status"] == "done"


def test_ai_budget_limit_defers_remaining_units(tmp_path, cfg_factory):
    src = tmp_path / "src"
    dummy_data.make_pdf(src / "a.pdf", pages_extra=5)
    cfg = cfg_factory(src, ai={"provider": "mock", "max_calls_per_run": 3})
    db, _, stats, prov = run_pipeline(cfg)
    assert len(prov.calls) == 3 and stats["ai_blocked"]
    assert db.q1("SELECT COUNT(*) c FROM units WHERE ai_status='budget_exceeded'")["c"] == 5
    assert db.q1("SELECT COUNT(*) c FROM units WHERE status='done'")["c"] == 8  # ローカル抽出は全ページ完了
    db.close()
    db, _, _, prov2 = run_pipeline(cfg)
    assert len(prov2.calls) == 3  # 次の実行で続きのAI解析


def test_retry_command_resets_failures(tmp_path, cfg_factory, monkeypatch):
    src = tmp_path / "src"
    (src).mkdir()
    (src / "broken.pdf").write_bytes(b"not a pdf")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(f"""source: {{type: local, path: "{src}"}}
output: {{work_dir: "{tmp_path / 'work'}"}}
stability: {{min_age_seconds: 0, min_stable_seconds: 0}}
limits: {{max_attempts: 1}}
""", encoding="utf-8")
    assert main(["-c", str(cfg_path), "run"]) == 0
    from structlab.db import DB
    db = DB(tmp_path / "work/state/state.db")
    assert db.q1("SELECT status FROM files")["status"] == "failed"
    assert main(["-c", str(cfg_path), "retry"]) == 0
    assert db.q1("SELECT status, attempts FROM files")["status"] == "ready"
    dummy_data.make_pdf(src / "broken.pdf")  # 壊れたファイルが正しく上げ直された
    assert main(["-c", str(cfg_path), "run"]) == 0
    assert db.q1("SELECT status FROM files")["status"] == "done"
