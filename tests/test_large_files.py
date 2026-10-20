"""大容量ファイルの分割処理（メモリに一括で載せないこと）。"""
from __future__ import annotations

import json
import subprocess
import tracemalloc

import pytest

import dummy_data
from conftest import needs_ffmpeg, run_pipeline
from structlab.models import RemoteFile
from structlab.safety import DropboxHasher, dropbox_content_hash
from structlab.sources import SourceChanged
from structlab.sources.dropbox_source import DropboxSource

BIG = 96 * 1024 * 1024


def test_hash_of_large_file_uses_little_memory(tmp_path):
    p = tmp_path / "big.bin"
    with open(p, "wb") as f:
        f.truncate(BIG)  # スパースファイル
    tracemalloc.start()
    dropbox_content_hash(p)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 16 * 1024 * 1024, peak


class _Resp:
    def __init__(self, total, chunk_byte=b"\1"):
        self.total = total
        self.byte = chunk_byte

    def iter_content(self, chunk_size):
        sent = 0
        block = self.byte * chunk_size
        while sent < self.total:
            n = min(chunk_size, self.total - sent)
            yield block[:n]
            sent += n

    def close(self):
        pass


class FakeDbx:
    def __init__(self, total):
        self.total = total
        self.download_calls = []

    def files_download(self, target):
        self.download_calls.append(target)
        return None, _Resp(self.total)


def _expected_hash(total):
    h = DropboxHasher()
    chunk = b"\1" * (1024 * 1024)
    left = total
    while left:
        n = min(left, len(chunk))
        h.update(chunk[:n])
        left -= n
    return h.hexdigest()


def test_dropbox_download_is_streamed_and_verified(tmp_path):
    total = BIG
    # 初回のライブラリ読み込み分を計測から除くため、小さなファイルで一度動かしておく
    DropboxSource("/授業", client=FakeDbx(10)).fetch(
        RemoteFile(path="w", size=10, server_modified=0, content_hash=_expected_hash(10), rev="w"), tmp_path / "w")
    src = DropboxSource("/授業", client=FakeDbx(total))
    rf = RemoteFile(path="動画.mp4", size=total, server_modified=0, content_hash=_expected_hash(total), rev="abc123")
    tracemalloc.start()
    out = src.fetch(rf, tmp_path / "cache" / "v.mp4")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert out.stat().st_size == total
    assert peak < 24 * 1024 * 1024, peak  # 96MB のファイルでも一定量しか使わない
    assert src.dbx.download_calls == ["rev:abc123"]  # スキャン時のリビジョンを指定して取得
    # 2回目はキャッシュを再利用（再ダウンロードしない）
    src.fetch(rf, tmp_path / "cache" / "v.mp4")
    assert len(src.dbx.download_calls) == 1


def test_dropbox_download_hash_mismatch_detected(tmp_path):
    src = DropboxSource("/授業", client=FakeDbx(1000))
    rf = RemoteFile(path="a.pdf", size=1000, server_modified=0, content_hash="0" * 64, rev="r1")
    with pytest.raises(SourceChanged):
        src.fetch(rf, tmp_path / "a.pdf")
    assert not (tmp_path / "a.pdf").exists() and not (tmp_path / "a.pdf.part").exists()


def test_large_pdf_is_split_per_page(tmp_path, cfg_factory):
    src = tmp_path / "src"
    dummy_data.make_pdf(src / "big.pdf", pages_extra=150)
    cfg = cfg_factory(src, ai={"provider": "none"})
    db, _, stats, _ = run_pipeline(cfg)
    units = db.q("SELECT * FROM units WHERE unit_type='pdf_page' ORDER BY seq")
    assert len(units) == 153
    assert [json.loads(u["locator"])["page"] for u in units] == list(range(1, 154))
    assert all(u["status"] == "done" and u["attempts"] == 1 for u in units)
    pages = {r["page"] for r in db.q("SELECT DISTINCT page FROM items")}
    assert 153 in pages


def test_large_excel_is_split_by_rows(tmp_path, cfg_factory):
    src = tmp_path / "src"
    dummy_data.make_excel(src / "big.xlsx", rows=1200)
    cfg = cfg_factory(src, ai={"provider": "none"}, excel={"rows_per_unit": 500})
    db, _, _, _ = run_pipeline(cfg)
    sheet_units = [json.loads(u["locator"]) for u in db.q("SELECT locator FROM units WHERE unit_type='excel_sheet' ORDER BY seq")]
    data_units = [u for u in sheet_units if u["sheet"] == "データ"]
    assert [(u["row_start"], u["row_end"]) for u in data_units] == [(1, 500), (501, 1000), (1001, 1203)]


@needs_ffmpeg
@pytest.mark.slow
def test_long_video_is_split_into_segments(tmp_path, cfg_factory):
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x180:rate=5:duration=300", "-f", "lavfi", "-i", "sine=duration=300",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src / "long.mp4")],
                   check=True)
    cfg = cfg_factory(src, ai={"provider": "none"}, video={"segment_seconds": 60, "focus_analysis": False},
                      transcription={"backend": "none"})
    db, _, _, _ = run_pipeline(cfg)
    segs = [json.loads(u["locator"]) for u in db.q("SELECT locator FROM units WHERE unit_type='video_segment' ORDER BY seq")]
    assert len(segs) == 5
    assert segs[0]["start"] == 0 and abs(segs[-1]["end"] - 300) < 1
    assert all(abs(a["end"] - b["start"]) < 1e-6 for a, b in zip(segs, segs[1:]))
    assert db.q1("SELECT COUNT(*) c FROM units WHERE status='done'")["c"] == 5
