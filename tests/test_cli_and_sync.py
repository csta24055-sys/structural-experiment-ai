"""CLI と、使い捨て環境間での再開（state.db を出力先に保存→別環境で取得）。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from structlab import cli
from structlab.db import DB


class FakeOutput:
    """DropboxOutput の代わりにローカルフォルダへ保存する。"""
    store: Path

    def __init__(self, out_root, source_root, client=None):
        self.uploaded: list[str] = []

    def upload(self, local: Path, rel: str) -> None:
        dst = FakeOutput.store / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local, dst)

    def download_if_exists(self, rel: str, local: Path) -> bool:
        src = FakeOutput.store / rel
        if not src.exists():
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, local)
        return True


def _write_cfg(tmp_path, src, work, extra=""):
    p = tmp_path / f"cfg_{work.name}.yaml"
    p.write_text(f"""source: {{type: local, path: "{src}"}}
output: {{work_dir: "{work}", dropbox_path: "/結果"}}
stability: {{min_age_seconds: 0, min_stable_seconds: 0}}
video: {{segment_seconds: 20}}
ocr: {{backend: none}}
{extra}""", encoding="utf-8")
    return p


def test_resume_in_fresh_container_via_saved_state(src_dir, tmp_path, monkeypatch):
    import structlab.sources.dropbox_source as dsrc
    FakeOutput.store = tmp_path / "remote"
    monkeypatch.setattr(dsrc, "DropboxOutput", FakeOutput)
    work1 = tmp_path / "container1"
    cfg1 = _write_cfg(tmp_path, src_dir, work1, "limits: {max_attempts: 3}\n")
    (work1).mkdir()
    # 1台目: 途中で停止（STOP ファイル）→ state.db と結果を保存
    orig = cli.run_once

    def run_with_stop(cfg, args):
        stop = cfg.work_dir / "STOP"
        from structlab import pipeline
        real = pipeline.Pipeline._process_unit
        n = {"c": 0}

        def counting(self, *a, **k):
            n["c"] += 1
            if n["c"] == 4:
                stop.touch()
            return real(self, *a, **k)
        monkeypatch.setattr(pipeline.Pipeline, "_process_unit", counting)
        try:
            return orig(cfg, args)
        finally:
            monkeypatch.setattr(pipeline.Pipeline, "_process_unit", real)
    monkeypatch.setattr(cli, "run_once", run_with_stop)
    assert cli.main(["-c", str(cfg1), "run"]) == 0
    monkeypatch.setattr(cli, "run_once", orig)
    assert (FakeOutput.store / "state/state.db").exists()
    assert (FakeOutput.store / "results/knowledge.md").exists()
    done1 = DB(FakeOutput.store / "state/state.db").q1("SELECT COUNT(*) c FROM units WHERE status='done'")["c"]
    assert 0 < done1

    # 2台目（作業ディレクトリが空の新しい環境）: 保存した state.db を取得して続きから
    work2 = tmp_path / "container2"
    cfg2 = _write_cfg(tmp_path, src_dir, work2)
    assert cli.main(["-c", str(cfg2), "run"]) == 0
    db = DB(work2 / "state/state.db")
    assert db.q1("SELECT COUNT(*) c FROM units WHERE status NOT IN ('done','superseded')")["c"] == 0
    assert {r["attempts"] for r in db.q("SELECT attempts FROM units")} == {1}  # やり直しなし
    assert db.q1("SELECT COUNT(*) c FROM runs")["c"] == 2


def test_cli_status_report_errors(src_dir, tmp_path, capsys):
    work = tmp_path / "w"
    p = tmp_path / "c.yaml"
    p.write_text(f"""source: {{type: local, path: "{src_dir}"}}
output: {{work_dir: "{work}"}}
stability: {{min_age_seconds: 0, min_stable_seconds: 0}}
ocr: {{backend: none}}
""", encoding="utf-8")
    assert cli.main(["-c", str(p), "status"]) == 0
    assert "まだ一度も実行されていません" in capsys.readouterr().out
    assert cli.main(["-c", str(p), "scan"]) == 0
    out = capsys.readouterr().out
    assert '"new": 6' in out
    assert cli.main(["-c", str(p), "run"]) == 0
    capsys.readouterr()
    assert cli.main(["-c", str(p), "status"]) == 0
    assert "完了" in capsys.readouterr().out
    assert cli.main(["-c", str(p), "report"]) == 0
    assert cli.main(["-c", str(p), "errors"]) == 0
    assert cli.main(["-c", str(p), "reanalyze", "板書.png"]) == 0
    assert "1 ファイル" in capsys.readouterr().out


def test_check_without_credentials(tmp_path, monkeypatch, capsys):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_REFRESH_TOKEN", "DROPBOX_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / "c.yaml"
    p.write_text(f'source: {{type: dropbox, path: "/授業"}}\noutput: {{work_dir: "{tmp_path / "w"}"}}\n', encoding="utf-8")
    assert cli.main(["-c", str(p), "check"]) == 1
    assert "Dropbox 認証情報" in capsys.readouterr().out


def test_concurrent_run_is_blocked(tmp_path):
    lock = tmp_path / ".lock"
    with cli.RunLock(lock):
        with pytest.raises(SystemExit):
            with cli.RunLock(lock):
                pass
