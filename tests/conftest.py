from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dummy_data  # noqa: E402

from structlab.config import load_config  # noqa: E402
from structlab.db import DB  # noqa: E402

MOCK_SCRIPT = [
    {"start": 3, "end": 6, "text": "まず荷重と変位を記録してください"},
    {"start": 17, "end": 21, "text": "ここで必ずAVERAGE関数を使ってください"},
    {"start": 40, "end": 44, "text": "グラフは散布図にすること"},
]

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg がありません")


@pytest.fixture(scope="session")
def dummy_master(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("dummy")
    return dummy_data.make_all(root)


@pytest.fixture
def src_dir(tmp_path, dummy_master) -> Path:
    d = tmp_path / "source"
    shutil.copytree(dummy_master, d)
    return d


def make_cfg(tmp_path: Path, src: Path, **over):
    base = {
        "source": {"type": "local", "path": str(src)},
        "output": {"work_dir": str(tmp_path / "work")},
        "stability": {"min_age_seconds": 0, "min_stable_seconds": 0},
        "video": {"segment_seconds": 20},
        "transcription": {"backend": "mock", "mock_script": MOCK_SCRIPT},
        "ai": {"provider": "mock"},
        "ocr": {"backend": "none"},
    }
    for k, v in over.items():
        base.setdefault(k, {}).update(v)
    return load_config(None, base)


@pytest.fixture
def cfg_factory(tmp_path):
    def f(src, **over):
        return make_cfg(tmp_path, src, **over)
    return f


def run_pipeline(cfg, provider=None, *, use_mock=True, on_progress=None, transcriber=None):
    """CLI と同じ流れ（スキャン→解析→出力）を関数として実行する。"""
    from structlab.ai.mock_provider import MockProvider
    from structlab.media import OCR, make_transcriber
    from structlab.pipeline import Pipeline
    from structlab.report import write_reports
    from structlab.scanner import scan
    from structlab.sources import make_source

    db = DB(cfg.db_path)
    db.recover_interrupted()
    src = make_source(cfg)
    res = scan(db, src, cfg)
    if provider is None and use_mock and cfg.data["ai"]["provider"] == "mock":
        provider = MockProvider()
    p = Pipeline(cfg, db, src, provider=provider, transcriber=transcriber or make_transcriber(cfg),
                 ocr=OCR(cfg.data["ocr"]["backend"]), on_progress=on_progress)
    stats = p.run()
    write_reports(db, cfg)
    return db, res, stats, provider
