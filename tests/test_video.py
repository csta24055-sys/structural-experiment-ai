"""動画の音声と映像の対応付け・重要場面の追加解析。"""
from __future__ import annotations

import json
import subprocess

import pytest

import dummy_data
from conftest import needs_ffmpeg, run_pipeline
from structlab.processors.video import align, focus_spec, pick_keyframes


def test_align_assigns_frame_shown_at_speech_start():
    kfs = [{"time": 0.0, "path": "f0"}, {"time": 16.0, "path": "f16"}, {"time": 36.0, "path": "f36"}]
    speech = [{"start": 3, "end": 6, "text": "a"}, {"start": 17, "end": 21, "text": "b"},
              {"start": 40, "end": 44, "text": "c"}]
    rows = align(speech, kfs, 50)
    m = {r["speech"]: r["frame_path"] for r in rows if r["speech"]}
    assert m == {"a": "f0", "b": "f16", "c": "f36"}
    assert len(rows) == 3  # すべてのキーフレームが発話に対応


def test_align_keeps_frames_without_speech():
    rows = align([], [{"time": 0.0, "path": "f0"}, {"time": 10.0, "path": "f10"}], 20)
    assert [(r["t_start"], r["t_end"], r["frame_path"]) for r in rows] == [(0.0, 10.0, "f0"), (10.0, 20, "f10")]


def test_pick_keyframes_limit_keeps_first_and_largest():
    idx = pick_keyframes([0, 2, 4, 6, 8], [1.0, 0.5, 0.01, 0.9, 0.3], threshold=0.1, max_n=3)
    assert idx == [0, 1, 3]


def test_focus_spec_is_clamped_to_duration():
    s = focus_spec(45, 60, "x", 50)
    assert s.locator["end"] <= 49.8 and s.locator["start"] == 45


@needs_ffmpeg
def test_video_speech_and_screen_alignment_end_to_end(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir, ocr={"backend": "auto"})
    db, _, _, prov = run_pipeline(cfg)
    f = db.q1("SELECT * FROM files WHERE kind='video'")
    assert f["status"] == "done"
    tl = db.q("SELECT * FROM timeline WHERE file_id=? ORDER BY t_start", (f["id"],))
    by_speech = {r["speech"]: r for r in tl if r["speech"]}
    # 17秒の発話（AVERAGE関数）は 15秒から表示される STEP 2 の画面に対応
    r = by_speech["ここで必ずAVERAGE関数を使ってください"]
    assert 15 <= r["frame_time"] <= 17
    assert r["important"] == 1 and "必ず" in r["reason"]
    assert by_speech["まず荷重と変位を記録してください"]["frame_time"] < 15
    assert by_speech["グラフは散布図にすること"]["frame_time"] >= 35
    # 重要場面の追加解析単位が作られ、処理済み
    focus = db.q("SELECT * FROM units WHERE file_id=? AND unit_type='video_focus'", (f["id"],))
    assert focus and all(u["status"] == "done" for u in focus)
    assert any(json.loads(u["locator"])["start"] <= 17 <= json.loads(u["locator"])["end"] for u in focus)
    # 発話から抽出した項目に時刻が付く
    it = db.q1("SELECT * FROM items WHERE origin='speech' AND statement LIKE '%AVERAGE%'")
    assert it["t_start"] == 17 and it["source_path"].endswith("講義動画.mp4")
    # AIへは区間ごとに時刻ラベル付きのフレームと文字起こしが渡されている
    seg_calls = [c for c in prov.calls if c.task == "video_segment"]
    assert len(seg_calls) == 3
    assert any("00:00:17" in c.text and "AVERAGE" in c.text for c in seg_calls)
    assert all(img.label.endswith("の画面") for c in seg_calls for img in c.images)
    md = next((cfg.results_dir / "timelines").glob("*.md")).read_text(encoding="utf-8")
    assert "ここで必ずAVERAGE関数を使ってください" in md and "★" in md
    if "tesseract" in (json.loads(db.q1("SELECT extract_json FROM units WHERE unit_type='video_segment'")["extract_json"])
                       ["data"]["ocr"]):
        assert "AVERAGE" in r["screen_text"]


@needs_ffmpeg
def test_audio_only_file(tmp_path, cfg_factory):
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=30",
                    str(src / "lecture.m4a")], check=True)
    cfg = cfg_factory(src)
    db, _, _, _ = run_pipeline(cfg)
    assert db.q1("SELECT status FROM files")["status"] == "done"
    assert db.q1("SELECT COUNT(*) c FROM units WHERE unit_type='video_focus'")["c"] == 0
    assert db.q1("SELECT COUNT(*) c FROM items WHERE origin='speech' AND t_start=17")["c"] >= 1


@needs_ffmpeg
def test_truncated_video_is_reported(tmp_path, cfg_factory):
    src = tmp_path / "src"
    v = dummy_data.make_video(tmp_path / "v.mp4", tmp_path / "w")
    src.mkdir()
    (src / "broken.mp4").write_bytes(v.read_bytes()[:2000])  # アップロード途中で切れた動画
    cfg = cfg_factory(src, limits={"max_attempts": 1})
    db, _, _, _ = run_pipeline(cfg)
    row = db.q1("SELECT * FROM files")
    assert row["status"] == "failed" and row["last_error"]


@pytest.mark.slow
@needs_ffmpeg
def test_real_transcription_with_faster_whisper(tmp_path, cfg_factory):
    """faster-whisper と音声合成(espeak-ng)がある環境のみ。実音声での文字起こし配線を確認する。"""
    pytest.importorskip("faster_whisper")
    import shutil
    if not shutil.which("espeak-ng"):
        pytest.skip("espeak-ng がありません")
    src = tmp_path / "src"
    src.mkdir()
    wav = tmp_path / "s.wav"
    subprocess.run(["espeak-ng", "-v", "en", "-s", "140", "-w", str(wav),
                    "Please calculate the average of the load. Submit the report as a PDF."], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "color=white:size=320x180:duration=8",
                    "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(src / "talk.mp4")], check=True)
    try:
        cfg = cfg_factory(src, transcription={"backend": "faster_whisper", "model": "tiny", "language": "en"})
        db, _, _, _ = run_pipeline(cfg)
    except Exception as e:  # モデルをダウンロードできない環境
        pytest.skip(f"faster-whisper を実行できません: {e}")
    # 精度ではなく「実音声 → 文字起こし → 時刻付きタイムライン」の配線を確認する
    rows = db.q("SELECT * FROM timeline WHERE speech != ''")
    text = " ".join(r["speech"] for r in rows).lower()
    assert "report" in text and "pdf" in text
    assert all(0 <= r["t_start"] < r["t_end"] <= 8 for r in rows)


# --- 付録: 開発中に使った作業用スクリプト（scratchpad、リポジトリ外） 12 件 ---
