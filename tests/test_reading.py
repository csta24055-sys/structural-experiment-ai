"""資料読み取り基盤（structlab read）: 棚卸し・PDFの位置付き読み取り・画面録画の領域検出と場面分割・照合・AI書き起こしの流れ。
すべてダミーデータで確認する（実際の資料・有料APIは使わない）。"""
from __future__ import annotations

import json
import threading
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import dummy_data
from conftest import make_cfg, needs_ffmpeg
from structlab.ai.base import AIResponse
from structlab.models import RemoteFile
from structlab.reading import verify
from structlab.reading.inventory import build_inventory


# ---------- 棚卸し ----------
def _rf(path, h, size=100, t=2.0):
    return RemoteFile(path=path, size=size, server_modified=t, content_hash=h)


def test_inventory_duplicates_and_helpers():
    files = [_rf("動画/a (1).MP4", "h1"), _rf("動画/a.MP4", "h1", t=1.0), _rf("別/b.MP4", "h1"),
             _rf("資料/講義.pdf", "h2"), _rf("資料/講義 2.pdf", "h3"),
             _rf("動画/%E5%8B%95.sh", "h4"), _rf("資料/%E5%8B%95.sh", "h4"), _rf("x/~$tmp.xlsx", "h5"),
             _rf("x/data.xls", "h6")]
    inv = {i.path: i for i in build_inventory(files, ["~$*"])}
    assert inv["動画/a.MP4"].role == "material"  # " (1)" の付かないものが代表
    assert sorted(inv["動画/a.MP4"].duplicates) == ["別/b.MP4", "動画/a (1).MP4"]
    assert inv["動画/a (1).MP4"].role == "duplicate" and inv["動画/a (1).MP4"].canonical == "動画/a.MP4"
    assert inv["資料/講義 2.pdf"].role == "material"  # 名前が似ていても内容が違えば重複にしない
    assert inv["動画/%E5%8B%95.sh"].role == "helper" and "2 か所" in inv["動画/%E5%8B%95.sh"].note
    assert inv["x/~$tmp.xlsx"].role == "ignored" and inv["x/data.xls"].role == "unsupported"


# ---------- PDF（回転したページ・表の見た目の行） ----------
def _rotated_pdf(path: Path) -> Path:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # 縦長で作って横向き表示（PowerPoint の PDF に多い形）
    page.set_rotation(90)
    # 表示上の座標で書く: 回転後の座標系へ変換して配置
    m = page.derotation_matrix
    for (x, y), text in {(60, 80): "強度試験の結果", (130, 140): "試験体", (220, 140): "1", (310, 140): "2",
                         (400, 140): "Ave.", (40, 180): "強度(N/mm2)", (220, 180): "23.0", (310, 180): "21.3",
                         (400, 180): "22.1"}.items():
        p = pymupdf.Point(x, y) * m
        page.insert_text(p, text, fontname="japan", fontsize=14, rotate=90)
    doc.save(str(path))
    return path


def test_pdf_rows_follow_displayed_layout(tmp_path):
    from structlab.reading.pdf_read import read_pdf
    rec = read_pdf(_rotated_pdf(tmp_path / "r.pdf"), tmp_path / "pages")
    rows = rec["pages"][0]["rows"]
    assert "試験体 | 1 | 2 | Ave." in rows and "強度(N/mm2) | 23.0 | 21.3 | 22.1" in rows
    assert rows.index("試験体 | 1 | 2 | Ave.") < rows.index("強度(N/mm2) | 23.0 | 21.3 | 22.1")
    assert {n["value"] for n in rec["pages"][0]["numbers"]} >= {"23.0", "21.3", "22.1"}


# ---------- 画面録画（スマホ縦長・時計が変わる・スライドが中央） ----------
def make_screen_recording(path: Path, work: Path, slides: list[list[str]], secs_each: int = 8) -> dict:
    from PIL import Image, ImageDraw
    work.mkdir(parents=True, exist_ok=True)
    W, H = 360, 780
    box = (30, 290, 330, 515)  # スライドの位置
    lst = work / "list.txt"
    with open(lst, "w") as f:
        t = 0
        for i, lines in enumerate(slides):
            slide = dummy_data.text_image(work / f"s{i}.png", lines, size=(box[2] - box[0], box[3] - box[1]),
                                          bg="lightyellow", font_size=26)
            for k in range(secs_each):
                im = Image.new("RGB", (W, H), (20, 20, 20))
                d = ImageDraw.Draw(im)
                d.rectangle((0, 0, W, 60), fill="white")
                d.text((20, 15), f"19:{t // 60:02d}:{t % 60:02d}", fill="black", font=dummy_data._font(28))
                d.rectangle((0, H - 70, W, H), fill=(40, 40, 40))
                im.paste(Image.open(slide), box[:2])
                fp = work / f"f{t:04d}.png"
                im.save(fp)
                f.write(f"file '{fp}'\nduration 1\n")
                t += 1
        f.write(f"file '{fp}'\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={t}", "-vf", "fps=5,format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-t", str(t), "-shortest", str(path)],
                   check=True)
    return {"box": box, "duration": t}


@needs_ffmpeg
@pytest.mark.slow
def test_screen_recording_region_scenes_and_revisits(tmp_path):
    from structlab import media
    from structlab.reading.video_read import read_video
    v = tmp_path / "rec.mp4"
    meta = make_screen_recording(v, tmp_path / "w", [["SLIDE A", "Load 23.0 kN"], ["SLIDE B", "Strain 2000"],
                                                      ["SLIDE A", "Load 23.0 kN"]])
    reg = media.detect_content_region(v, media.probe(v))
    x0, y0, x1, y1 = meta["box"]
    assert not reg.is_full
    assert abs(reg.x - x0) <= 12 and abs(reg.y - y0) <= 12 and abs(reg.x + reg.w - x1) <= 12 and abs(reg.y + reg.h - y1) <= 12
    rec = read_video(v, tmp_path / "frames", ocr=media.OCR("auto", "eng"), transcriber=None, log=lambda m: None)
    sc = rec["scenes"]
    # 時計の変化では区切らず、スライドの切り替えだけで3場面。3つ目は1つ目と同じ画面
    assert rec["mode"] == "slides" and len(sc) == 3, [(s["start"], s["end"]) for s in sc]
    assert sc[2]["same_as"] == 0 and sc[1]["same_as"] is None and rec["n_unique_scenes"] == 2
    assert not any(s["events"] for s in sc)  # 時計の変化は「操作」に数えない
    if shutil.which("tesseract"):
        assert "SLIDE" in sc[0]["ocr"].upper() and "19:" not in sc[0]["ocr"]  # 時計は切り出しの外


# ---------- 照合 ----------
def _ai(**kw):
    base = {"title": "", "text_blocks": [], "tables": [], "formulas": [], "figures": [], "operations": [],
            "unreadable": [], "notes": ""}
    base.update(kw)
    return base


def test_verify_pdf_page_coverage_and_numbers():
    page = {"page": 1, "text_layer": "試験体 1 2 Ave.\n強度 23.0 21.3 22.1", "needs_vision": True,
            "lines": [{"text": "試験体"}, {"text": "強度"}, {"text": "23.0"}, {"text": "21.3"}, {"text": "22.1"}]}
    good = _ai(tables=[{"caption": "", "header": ["試験体", "1", "2", "Ave."], "rows": [["強度", "23.0", "21.3", "22.1"]],
                        "notes": ""}],
               figures=[{"kind": "graph", "caption": "", "description": "", "axes": [], "legend": [], "labels": [],
                         "values_read": [{"what": "最大", "value": "24.1", "approx": True}]}])
    v = verify.verify_pdf_page(page, good)
    assert v["status"] == "ok" and v["text_layer_coverage"] == 1.0 and v["numbers"]["approx_values"] == 1
    bad = _ai(tables=[{"caption": "", "header": ["試験体"], "rows": [["強度", "23.0", "21.8"]], "notes": ""}])
    v = verify.verify_pdf_page(page, bad)
    assert v["status"] == "needs_review" and v["numbers"]["unverified"] == ["21.8"] and "21.3" in v["missing_lines"]
    assert verify.verify_pdf_page(page, None)["status"] == "unverified"


def test_scene_links_to_pdf_page_and_is_checked_against_it():
    pages = [("講義.pdf", {"page": 3, "text_layer": "コンクリートの圧縮試験結果 強度 23.0 21.3 22.1",
                           "lines": [{"text": "コンクリートの圧縮試験結果"}, {"text": "23.0"}]}),
             ("講義.pdf", {"page": 4, "text_layer": "鉄筋の引張試験 384 393", "lines": [{"text": "鉄筋の引張試験"}]})]
    scenes = [{"index": 0, "start": 0, "end": 5, "same_as": None, "ocr": "コンクリ一トの圧縮試験結果\n強度 23.0 21.3"}]
    verify.link_scenes_to_pdf(scenes, pages)
    assert scenes[0]["pdf_match"]["page"] == 3
    v = verify.verify_video_scene(scenes[0], _ai(title="コンクリートの圧縮試験結果", text_blocks=[{"text": "23.0", "role": "body"}]),
                                  pages[0][1])
    assert v["status"] == "ok" and v["verified_against"] == "PDFのテキスト層＋OCR"


# ---------- 全体の流れ（ローカル資料・模擬AI） ----------
class FakeReader:
    """書き起こしの模擬。リクエストに含まれる参考テキストの数値をそのまま返す（課金なし）。"""
    external = False
    model = "fake"

    def __init__(self):
        self.calls = []

    def analyze(self, req):
        self.calls.append(req)
        assert req.schema is not None and req.images and all(Path(i.path).exists() for i in req.images)
        nums = verify.numbers(req.text.split("##", 2)[-1])
        data = _ai(title="資料", text_blocks=[{"text": " ".join(nums), "role": "body"}])
        return AIResponse(data=data, model="fake", input_tokens=1000, output_tokens=200)


@needs_ffmpeg
@pytest.mark.slow
def test_run_reading_end_to_end(tmp_path, monkeypatch):
    from structlab.reading.runner import run_reading
    src = tmp_path / "src"
    dummy_data.make_pdf(src / "第1回/指導書.pdf")
    shutil.copy(src / "第1回/指導書.pdf", src / "第1回/指導書 (1).pdf")
    make_screen_recording(src / "動画/録画.mp4", tmp_path / "w", [["STEP 1 LOAD", "P = 23.0 kN"], ["STEP 2 GRAPH", "E = 21.3 GPa"]], secs_each=6)
    (src / "動画/分割.sh").write_text("#!/bin/sh\n")
    cfg = make_cfg(tmp_path, src, transcription={"backend": "none"}, ai={"provider": "mock"})
    fake = FakeReader()
    logs = []
    summary = run_reading(cfg, allow_ai=True, provider=fake, log=logs.append)
    out = cfg.work_dir / "reading"
    inv = json.loads((out / "inventory.json").read_text())
    roles = {i["path"]: i["role"] for i in inv}
    assert roles["第1回/指導書 (1).pdf"] == "duplicate" and roles["動画/分割.sh"] == "helper"
    assert len(summary["files"]) == 2  # 重複・補助ファイルは読まない
    est = summary["estimate"]
    assert est["calls"]["pdf_page"] >= 1 and est["calls"]["video_scene"] + est["calls"]["video_ops"] == 2
    assert len(fake.calls) == est["total_calls"] and summary["ai_usage"]["calls"] == est["total_calls"]
    md = (out / "summary.md").read_text()
    assert "概算" in md and "未照合は成功ではない" in md
    video = next(f for f in summary["files"] if f["kind"] == "video")
    assert video["unique_scenes"] == 2 and video["region_cropped"]
    # 2回目は保存済みの読み取り・書き起こしを使い、AI を呼び直さない
    fake2 = FakeReader()
    run_reading(cfg, allow_ai=True, provider=fake2, log=logs.append)
    assert fake2.calls == []
    # AI なし（見積もりのみ）でも照合結果は「未照合」として残る
    s3 = run_reading(cfg, allow_ai=False, out_dir=tmp_path / "noai", log=logs.append)
    assert s3["status_counts"]["unverified"] >= 2 and s3["ai_usage"]["calls"] == 0


def test_estimate_counts_unique_scenes_only():
    from structlab.reading.ai_read import estimate_calls
    recs = {"v": {"kind": "video", "scenes": [{"same_as": None}, {"same_as": 0},
                                               {"same_as": None, "events_with_frames": [{"frame": "x"}]}]},
            "p": {"kind": "pdf", "n_pages": 3}}
    e = estimate_calls(recs)
    assert e["calls"] == {"pdf_page": 3, "video_scene": 1, "video_ops": 1, "experiment_window": 0, "speech_fix": 0}
    assert e["images"] == 3 + 1 + 2 and e["input_tokens"] > 0 and e["output_tokens"] > 0
    # 実験経過の区間は、区間内の画面＋表面の基準画像を1枚添える
    ex = estimate_calls({"x": {"kind": "video", "scenes": [{"same_as": None, "mode": "experiment",
                                                            "events_with_frames": [{"frame": "a"}, {"frame": "b"}]}]}})
    assert ex["calls"]["experiment_window"] == 1 and ex["images"] == 4


def test_speech_corrections_need_support_in_materials():
    speech = [{"start": 0, "end": 3, "text": "RC針の先段火力実験を行います"}, {"start": 3, "end": 6, "text": "視点を確認"}]
    fixes = [{"segment": 0, "original": "針の先段火力", "corrected": "梁のせん断加力", "reason": "スライドの題名"},
             {"segment": 1, "original": "視点", "corrected": "支店", "reason": "誤った訂正"},
             {"segment": 1, "original": "存在しない", "corrected": "支点", "reason": ""}]
    res = verify.apply_speech_corrections(speech, fixes, "RC梁のせん断加力実験（1） 支点 載荷点")
    assert speech[0]["text"] == "RC梁のせん断加力実験を行います" and speech[0]["text_raw"] == "RC針の先段火力実験を行います"
    assert speech[1]["text"] == "視点を確認"  # 資料に無い語・見つからない文字への訂正は採用しない
    assert res["applied"] == 1 and len(res["rejected"]) == 2


def test_merge_counts_pdf_once(tmp_path):
    from structlab.reading.merge import merge, to_markdown
    pdf = {"path": "a.pdf", "kind": "pdf", "pages": 25, "pages_needing_vision": 20,
           "status": {"ok": 0, "needs_review": 0, "unverified": 20, "text_only": 5}}
    for i, v in enumerate(("v1.mp4", "v2.mp4")):
        d = tmp_path / f"r{i}"
        d.mkdir()
        vid = {"path": v, "kind": "video", "duration_min": 10, "scenes": 5, "unique_scenes": 4, "speech_segments": 50,
               "low_conf_speech": 1, "pdf_linked_scenes": 1, "status": {"ok": 0, "needs_review": 0, "unverified": 5, "text_only": 0}}
        est = {"calls": {"pdf_page": 25, "video_scene": 4, "video_ops": 0, "speech_fix": 1}, "total_calls": 30, "images": 29,
               "input_tokens": 1000, "output_tokens": 100, "usd": 1.0}
        (d / "summary.json").write_text(json.dumps({"files": [pdf, vid], "estimate": est,
                                                    "ai_usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0}}))
    m = merge([tmp_path])
    assert len(m["files"]) == 3 and m["status_counts"]["unverified"] == 30 and m["status_counts"]["text_only"] == 5
    assert m["estimate"]["calls"] == {"pdf_page": 25, "video_scene": 8, "video_ops": 0, "speech_fix": 2}
    assert "未照合は成功ではない" in to_markdown(m)


def test_list_targets_one_job_per_video(tmp_path):
    from structlab.reading.runner import list_targets
    files = [_rf("a.pdf", "p", 10), _rf("v/b.MP4", "v1", 300), _rf("v/b (1).MP4", "v1", 300), _rf("v/c.MOV", "v2", 900),
             _rf("v/x.sh", "s", 1)]
    src = SimpleNamespace(iter_files=lambda: iter(files))
    cfg = make_cfg(tmp_path, tmp_path)
    assert list_targets(cfg, src) == ["v/c.MOV", "v/b.MP4", "a.pdf"]  # 重複・補助ファイルを除き、動画は大きい順・PDFは最後
    keyed = list_targets(cfg, SimpleNamespace(iter_files=lambda: iter(files)), with_hash=True)
    assert keyed[0]["path"] == "v/c.MOV" and keyed[0]["key"].startswith("v") and len({k["key"] for k in keyed}) == 3
    src2 = SimpleNamespace(iter_files=lambda: iter([files[0]]))
    assert list_targets(cfg, src2) == ["a.pdf"]


def test_reading_workflow_is_readonly_and_ai_gated():
    import yaml
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/reading.yml").read_text()
    d = yaml.safe_load(text)
    on = d.get("on", d.get(True))
    assert on["push"]["paths"] == ["ops/requests/reading.txt"] and d["permissions"]["contents"] == "read"
    run = "\n".join(s.get("run", "") for s in d["jobs"]["read"]["steps"])
    assert 'if [ "${{ needs.plan.outputs.allow_ai }}" = "true" ]; then FLAGS="--allow-external-ai"' in run
    # 設定ファイルの work_dir は configs/ からの相対パスになるため、成果物として保存する場所を明示する
    assert "--out work/reading" in run
    up = next(s for s in d["jobs"]["read"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
    assert up["with"]["path"].split()[0] == "work/reading" and up["with"]["retention-days"] <= 30
    assert "GH_SECRETS_TOKEN" not in text and "echo $DROPBOX" not in text


@needs_ffmpeg
def test_crop_survives_resolution_change(tmp_path):
    """録画の途中で画面の向き・大きさが変わっても、切り出しで失敗しない（実際の画面録画で発生した不具合）。"""
    from structlab import media
    a, b = tmp_path / "a.ts", tmp_path / "b.ts"
    for p, size in ((a, "360x780"), (b, "780x360")):
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=yellow:s={size}:d=3",
                        "-c:v", "libx264", "-preset", "ultrafast", "-f", "mpegts", str(p)], check=True)
    ab = tmp_path / "ab.ts"
    ab.write_bytes(a.read_bytes() + b.read_bytes())
    reg = media.Region(40, 200, 280, 600, 360, 780)
    frames = media.sample_gray_frames(ab, 0, 6, 1.0, w=100, h=200, crop=reg.ffmpeg_crop())
    assert len(frames) >= 5


def test_stream_seconds_reads_mkv_duration_tag():
    from structlab.reading.video_read import _stream_seconds
    assert _stream_seconds({"duration": "12.5"}) == 12.5
    assert abs(_stream_seconds({"tags": {"DURATION": "00:20:27.626000000"}}) - 1227.626) < 1e-6
    assert _stream_seconds({}) == 0.0


def _tail_case(back_to_opening: bool, silent: bool):
    import numpy as np
    rng = np.random.default_rng(0)
    opening = rng.integers(0, 255, (20, 40)).astype(np.uint8)
    experiment = rng.integers(0, 255, (20, 40)).astype(np.uint8)
    times = [float(t) for t in range(0, 200)]
    frames = [opening if t < 20 else experiment for t in times[:180]]
    frames += [opening if back_to_opening else experiment] * 20
    audio = {"track_seconds": 200.0, "silences": [[180.2, 200.0]] if silent else [[50.0, 53.0]]}
    return times, frames, audio


def test_trim_only_when_returning_to_opening_and_silent():
    from structlab.reading.video_read import detect_return_to_start
    r = detect_return_to_start(*_tail_case(True, True), 200.0)
    assert r["status"] == "trimmed" and 179 <= r["trim_at"] <= 181 and r["evidence"]["reason"]
    # 無音だけでは除外しない（冒頭に戻った根拠が必要）
    assert detect_return_to_start(*_tail_case(False, True), 200.0)["status"] == "needs_confirmation"
    # 冒頭に似た画面でも、末尾が無音でなければ除外しない
    assert detect_return_to_start(*_tail_case(True, False), 200.0)["status"] == "not_found"


def test_role_matching_and_teacher_timeline_is_not_filled():
    from structlab.config import load_config
    from structlab.reading.corpus import student_observations, teacher_timeline
    from structlab.reading.runner import role_for
    cfg = load_config(Path(__file__).resolve().parents[1] / "configs" / "dropbox.yaml")
    t1 = role_for(cfg, "鋼材実験経過先生1/a.MP4")
    t2 = role_for(cfg, "/鋼材実験経過先生2/b.mov")
    st = role_for(cfg, "鋼材実験経過生徒1/IMG_1 (1).MOV")
    assert (t1["order"], t2["order"]) == (1, 2) and t1["trim_end"] and t2["sequence"] == "teacher"
    assert st["sequence"] == "student" and not st.get("trim_end") and "order" not in st
    assert role_for(cfg, "事前説明2/y.MP4") == {}
    for path in ("レポートの作り方1/x.MP4", "レポートの作り方2/x.MP4", "エクセルの作り方1/x.MP4"):
        r = role_for(cfg, path)
        assert r["kind"] == "lecture_detail" and r["interval"] == 0.5 and r["asr_model"] == "large-v3"
    assert t1["group"] == 4 and st["group"] == 4

    def sc(a, b):
        return {"start": a, "end": b, "frame": None, "speech": []}
    recs = {
        "鋼材実験経過先生2/b.mov": {"path": "鋼材実験経過先生2/b.mov", "role": t2, "duration": 100.0, "valid_end": 80.0,
                                "trim": {"status": "trimmed", "trim_at": 80.0}, "scenes": [sc(0, 50), sc(50, 85), sc(85, 100)]},
        "鋼材実験経過先生1/a.MP4": {"path": "鋼材実験経過先生1/a.MP4", "role": t1, "duration": 60.0, "valid_end": 55.0,
                                "trim": {"status": "trimmed", "trim_at": 55.0}, "scenes": [sc(0, 30), sc(30, 60)]},
        "鋼材実験経過生徒1/s.MOV": {"path": "鋼材実験経過生徒1/s.MOV", "role": st, "duration": 90.0, "scenes": [sc(0, 90)]},
    }
    tl = teacher_timeline(recs)
    assert [p["order"] for p in tl["parts"]] == [1, 2] and tl["total_seconds"] == 135.0
    assert tl["parts"][1]["timeline_from"] == 55.0 and len(tl["gaps"]) == 1  # 切れ目は記録し、補完しない
    assert max(w["t_to"] for w in tl["windows"]) == 135.0 and all(w["source_start"] < 80 for w in tl["windows"]
                                                                  if "先生2" in w["source"])
    sv = student_observations(recs)["videos"]
    assert len(sv) == 1 and sv[0]["windows"][0]["end"] == 90  # 生徒の動画は末尾除外・連結をしない


def _player_stack(n=6, inner=(60, 150, 120, 300), rotate=False):
    """ブラウザ（上が白い帯）の暗い動画プレーヤーの中に、明るい動画がある画面（縮小・グレースケール）。"""
    import numpy as np
    rng = np.random.default_rng(1)
    out = []
    for _ in range(n):
        f = np.full((166, 360), 18, np.int16)
        f[:25, :] = 250                       # アドレスバー
        a, b, c0, c1 = inner
        f[a:b, c0:c1] = rng.integers(120, 255, (b - a, c1 - c0))
        out.append(np.rot90(f, -1) if rotate else f)
    return np.stack(out)


def test_content_box_finds_video_inside_browser_player():
    from structlab.media import content_box
    a, b, c0, c1 = content_box(_player_stack())
    assert abs(a - 60) <= 3 and abs(b - 150) <= 3 and abs(c0 - 120) <= 3 and abs(c1 - 300) <= 3
    # 明るいスライドの中の文字の塊までは切り込まない（外側が暗い余白でないときは入れ子として扱わない）
    import numpy as np
    s = np.full((4, 166, 360), 18, np.int16)
    s[:, 40:130, 30:330] = 240
    s[:, 70:90, 100:200] = 20
    a, b, c0, c1 = content_box(s)
    assert a <= 41 and b >= 129 and c0 <= 31 and c1 >= 329


def test_orientation_runs_detect_sideways_screen_and_ignore_blips():
    import numpy as np
    from structlab.media import orientation_runs, ui_orientation
    normal = np.full((111, 240), 20, np.uint8)
    normal[:12] = 250
    side = np.full((111, 240), 20, np.uint8)
    side[:, -26:] = 250
    assert ui_orientation(normal) == 0 and ui_orientation(side) == 90 and ui_orientation(side[:, ::-1]) == 270
    frames = [normal] * 5 + [side] + [normal] * 4 + [side] * 6
    runs = orientation_runs([float(2 * i) for i in range(len(frames))], frames)
    assert [r[2] for r in runs] == [0, 90] and runs[1][0] == 20.0  # 1回だけの揺れは無視


def test_frame_size_native_and_detail():
    from structlab.media import Region
    from structlab.reading.video_read import frame_size
    # 通常（2,350円構成）: 切り出しは元の解像度（長辺 1000 未満は拡大）、画面全体は 1568
    assert frame_size(Region(0, 0, 1920, 1080, 1920, 1080)) == {"max_width": 1568, "min_width": 0}
    assert frame_size(Region(10, 10, 1196, 670, 2532, 1170)) == {"max_width": 1196, "min_width": 0}
    assert frame_size(Region(10, 10, 886, 514, 2532, 1170)) == {"max_width": 1000, "min_width": 1000}
    # Excel・レポートの作り方: 元の解像度のまま保存（拡大は送るときに元の画素から作る）
    assert frame_size(Region(10, 10, 928, 612, 2532, 1170), "detail") == {"max_width": 928, "min_width": 0}
    assert frame_size(Region(0, 0, 2532, 1170, 2532, 1170), "detail")["max_width"] == 2532


def test_experiment_windows_are_read_one_by_one_by_default():
    """区間をまとめて読む方法は品質の確認ができていないので既定では使わない（従来どおり区間ごと）。"""
    from structlab.reading import ai_read
    assert ai_read.EXPERIMENT_GROUP == 1
    scenes = [{"index": i, "segment": 0 if i < 6 else 1, "start": 15 * i, "end": 15 * i + 15} for i in range(9)]
    groups = ai_read.experiment_groups(scenes, 4)
    assert [[s["index"] for s in g] for g in groups] == [[0, 1, 2, 3], [4, 5], [6, 7, 8]]


def test_reference_image_is_cached_without_changing_the_request(tmp_path):
    from PIL import Image
    from structlab.models import fmt_time
    from structlab.reading import ai_read
    f = tmp_path / "f.jpg"
    Image.new("RGB", (100, 60), "gray").save(f)
    sc = {"index": 0, "start": 0, "end": 15, "frame_time": 7, "frame": str(f),
          "events_with_frames": [{"time": 0.5, "frame": str(f)}, {"time": 14.5, "frame": str(f)}], "speech": []}
    req = ai_read.experiment_window_request("x", sc, fmt_time, str(f), "先生")
    assert [i.cache for i in req.images] == [True, False, False, False]  # 基準画像だけをキャッシュ


def test_crop_is_safe_detects_content_outside(tmp_path):
    import numpy as np
    from structlab import media
    reg = media.Region(100, 100, 200, 100, 400, 300)
    reg.ref = np.full((30, 40), 20, np.int16)
    reg.ref_box = (10, 20, 10, 30)
    calls = []

    def fake_grab(path, t, w, h, crop=None):
        calls.append(t)
        f = np.full((h, w), 20, np.uint8)
        if t > 1:
            f[:, :8] = 250  # 外側に何か映っている
        return f
    orig = media.grab_gray
    media.grab_gray = fake_grab
    try:
        assert media.crop_is_safe(Path("x"), 0.5, reg) and not media.crop_is_safe(Path("x"), 2.0, reg)
    finally:
        media.grab_gray = orig


def test_grouped_experiment_request_shares_reference(tmp_path):
    from PIL import Image
    from structlab.models import fmt_time
    from structlab.reading import ai_read
    fs = []
    for i in range(9):
        f = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (100, 60), "gray").save(f)
        fs.append(str(f))
    scenes = [{"index": i, "start": 15 * i, "end": 15 * i + 15, "frame_time": 15 * i + 3.75, "frame": fs[2 * i],
               "events_with_frames": [{"time": 15 * i + 11.25, "frame": fs[2 * i + 1]}], "speech": []} for i in range(4)]
    req = ai_read.experiment_group_request("x", scenes, fmt_time, fs[8], (0.0, fs[8]), "先生")
    assert len(req.images) == 1 + 1 + 8 and req.schema is ai_read.EXPERIMENT_GROUP_SCHEMA
    assert "区間4" in req.images[-1].label and "基準画像" in req.images[0].label


def test_batch_mode_collects_then_reads_saved_results(tmp_path):
    from structlab.reading.runner import Reader
    cfg = make_cfg(tmp_path, tmp_path)
    cfg.data.setdefault("reading", {})["batch"] = True
    sent = []

    class FakeBatch:
        model = "claude-opus-5-5"

        def analyze_batch(self, reqs, log=print, known=None, on_created=None):
            sent.extend(reqs)
            if on_created:
                on_created(0, "batch_1")
            return [AIResponse(data={"n": i}, model=self.model, input_tokens=10, output_tokens=5, raw_text="")
                    for i, _ in enumerate(reqs)]

        def analyze(self, req):
            raise AssertionError("Batch のときは1件ずつ送らない")

    r = Reader(cfg, out_dir=tmp_path / "out", provider=FakeBatch(), log=lambda m: None)
    rec = {"dir": str(tmp_path / "rec")}
    assert r._ai_cached(rec, "a", lambda: SimpleNamespace(source_label="a")) is None
    assert r._ai_cached(rec, "b", lambda: SimpleNamespace(source_label="b")) is None
    assert r._ai_cached(rec, "a", lambda: SimpleNamespace(source_label="a")) is None  # 同じ項目は1回だけ
    assert r.flush_batch() == 2 and len(sent) == 2
    assert r._ai_cached(rec, "b", lambda: None) == {"n": 1} and r.usage["calls"] == 2
    assert json.loads((tmp_path / "out" / "batch_state.json").read_text())  # 中断しても送り直さないための記録


def test_detail_keeps_every_distinct_change():
    import numpy as np
    from structlab.reading.video_read import distinct_events
    rng = np.random.default_rng(3)
    a, b, c = (rng.integers(0, 255, (20, 40)).astype(np.uint8) for _ in range(3))
    times = [0.0, 0.5, 1.0, 1.5, 2.0]
    frames = [a, b, b, c, a]
    evs = [{"time": t, "change": 0.1} for t in (0.5, 1.0, 1.5, 2.0)]
    kept = [e["time"] for e in distinct_events(evs, times, frames)]
    assert kept == [0.5, 1.5, 2.0]  # 0.5 秒だけ出た画面も残し、同じ画面の繰り返しだけ除く


def _sheet(path, seed=0, blank_right=False, mark=None):
    """表計算の画面に似た画像（罫線と数字）。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (400, 240), "white")
    d = ImageDraw.Draw(im)
    for y in range(20, 240, 20):
        d.line((0, y, 200 if blank_right else 400, y), fill="gray")
    for x in range(0, 200 if blank_right else 400, 50):
        d.line((x, 0, x, 240), fill="gray")
    for r in range(1, 10):
        d.text((5, 20 * r + 4), f"{r * 1.5 + seed:.2f}", fill="black")
    if mark:
        d.rectangle(mark, fill="black")
    im.save(path, lossless=True)
    return str(path)


def test_detail_images_zoom_only_where_needed(tmp_path):
    from structlab.reading import detail as D
    a = _sheet(tmp_path / "a.webp", blank_right=True)
    tiles = D.content_tiles(a)
    names = {n for _, n in tiles}
    assert "左上" in names and "右上" not in names               # 白紙の部分は拡大しない
    b = _sheet(tmp_path / "b.webp", blank_right=True, mark=(60, 60, 110, 80))
    box = D.change_box(a, b)
    assert box and box[0] <= 60 and box[2] >= 110 and box[2] - box[0] < 200   # 変わったセルの周りだけ
    z = D.change_zoom(a, b)
    from PIL import Image
    assert Image.open(z).size[0] >= 3 * (box[2] - box[0]) - 2              # 元の画素から 3 倍
    assert D.change_box(a, a) is None                                       # 変化なしなら作らない


def test_detail_requests_split_zoom_pass_and_merge(tmp_path):
    from structlab.models import fmt_time
    from structlab.reading import ai_read
    frames = [_sheet(tmp_path / f"f{i}.webp", seed=i) for i in range(31)]
    sc = {"index": 3, "start": 0, "end": 60, "frame_time": 1, "frame": frames[0], "ocr": "",
          "events_with_frames": [{"time": float(i + 2), "frame": frames[i + 1]} for i in range(30)],
          "speech": [{"start": 2.0, "end": 4.0, "text": "ここは必ず絶対参照にしてください"}]}
    reqs = ai_read.video_detail_requests("excel", sc, fmt_time)
    assert len(reqs) == 3 and "絶対参照" in reqs[0].text and reqs[0].schema is ai_read.DETAIL_SCHEMA
    assert all(im.lossless for r in reqs for im in r.images)
    assert reqs[0].images[0].label.startswith("[F0]") and any(im.label.startswith("[F12]") for im in reqs[0].images)
    assert any(im.label.startswith("[F13]") for im in reqs[1].images)      # 画面番号は場面の中で通し番号
    base = {k: [] for k in ai_read.DETAIL_SCHEMA["required"]}
    base.update({"title": "t", "notes": ""})
    p1 = [{**base, "operations": [{"time": "0:01", "action": "a", "target": "B3", "value": "=A3*2"}],
           "zoom_requests": [{"frame": "F5", "x": 0.0, "y": 0.1, "w": 0.3, "h": 0.2, "target": "cell", "what": "B3 の値"}]},
          {**base, "speech_points": [{"time": "0:02", "text": "必ず絶対参照", "source": "speech"}]}, {**base}]
    zr = ai_read.zoom_requests("excel", sc, p1, fmt_time)
    assert len(zr) == 1 and len(zr[0].images) == 1 and "B3" in zr[0].text
    from PIL import Image
    assert Image.open(zr[0].images[0].path).size[0] >= 4 * int(0.3 * 400)   # 元の画素から 4 倍
    m = ai_read.merge_detail(p1, [{"readings": [{"image": 1, "time": "F5", "target": "cell", "text": "3.50",
                                                  "readable": True, "correction": ""},
                                                 {"image": 2, "time": "F6", "target": "cell", "text": "",
                                                  "readable": False, "correction": ""}]}])
    assert len(m["operations"]) == 1 and len(m["zoom_readings"]) == 2
    assert any("拡大しても読めない" in u["reason"] for u in m["unreadable"])   # 読めないものは未確認のまま
    part = ai_read.merge_detail([p1[0], None])
    assert "未完了" in part["notes"] and part["unreadable"]


def test_lossless_upload_keeps_pixels(tmp_path):
    import base64
    import io
    from PIL import Image
    from structlab.ai.anthropic_provider import encode_image
    f = _sheet(tmp_path / "s.webp")
    media, data = encode_image(f, 2576, lossless=True)
    got = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
    assert media == "image/webp" and list(got.getdata()) == list(Image.open(f).convert("RGB").getdata())


@needs_ffmpeg
def test_detail_mode_saves_native_lossless_frames_and_brief_displays(tmp_path):
    """Excel・レポートの作り方: 元の解像度の可逆画像で保存し、0.4 秒だけ出た画面も拾う。"""
    from PIL import Image, ImageDraw
    from structlab import media
    from structlab.reading.video_read import read_video
    work = tmp_path / "w"
    work.mkdir()
    W, H = 360, 780
    lst = work / "list.txt"
    with open(lst, "w") as f:
        for k, (label, dur) in enumerate([("SHEET A", 4.0), ("DIALOG 12.5", 0.4), ("SHEET A", 4.0)]):
            im = Image.new("RGB", (W, H), (20, 20, 20))
            d = ImageDraw.Draw(im)
            d.rectangle((0, 0, W, 60), fill="white")
            d.rectangle((30, 290, 330, 515), fill="lightyellow")
            d.text((60, 380), label, fill="black", font=dummy_data._font(30))
            fp = work / f"f{k}.png"
            im.save(fp)
            f.write(f"file '{fp}'\nduration {dur}\n")
        f.write(f"file '{fp}'\n")
    v = tmp_path / "rec.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(v)], check=True)
    rec = read_video(v, tmp_path / "out", ocr=media.OCR("none", "eng"), transcriber=None, interval=0.5, detail=True,
                     log=lambda m: None)
    frames = [rec["scenes"][0]["frame"]] + [e["frame"] for sc in rec["scenes"] for e in sc.get("events_with_frames", [])]
    assert all(f.endswith(".webp") for f in frames)
    assert Image.open(frames[0]).size[0] <= rec["region"]["w"] + 2       # 拡大せず元の解像度で保存
    times = [e["time"] for sc in rec["scenes"] for e in sc.get("events_with_frames", [])] + \
        [sc["frame_time"] for sc in rec["scenes"]]
    assert any(3.9 <= t <= 4.5 for t in times)                             # 0.4 秒だけの表示を拾った
    assert rec["segments"][0]["dense_scan"]["interval"] == 0.2


def test_semantic_bridge_reuses_reading_results(tmp_path):
    """読み取り結果を意味理解層の場面にする（120 秒ごと・除外した末尾を含めない・画像は第3回の照合だけ）。"""
    from structlab.db import DB
    from structlab.reading.semantic_bridge import load_records, run_semantic
    from structlab.semantic.material import build_scenes
    frame = tmp_path / "f.jpg"
    from PIL import Image
    Image.new("RGB", (64, 48), "white").save(frame)
    rec = {"path": "鋼材実験経過先生1/a.MP4", "kind": "video", "duration": 300.0, "valid_end": 250.0, "has_audio": True,
           "content_hash": "h1", "transcriber": "large-v3-turbo",
           "scenes": [{"index": i, "start": 15.0 * i, "end": 15.0 * i + 15, "frame_time": 15.0 * i + 7, "frame": str(frame),
                       "mode": "experiment", "ai": {"face": "front", "specimen_state": f"状態{i}"}} for i in range(20)],
           "speech": [{"start": 10.0, "end": 12.0, "text": "ここは必ず記録してください"},
                      {"start": 260.0, "end": 262.0, "text": "除外した部分の発話"}]}
    pdf = {"path": "p.pdf", "kind": "pdf", "content_hash": "h2", "n_pages": 1,
           "pages": [{"page": 1, "text_layer": "提出期限は必ず守ること", "rows": ["提出期限は必ず守ること"], "render": str(frame),
                      "ai": {"title": "注意"}, "needs_vision": False}]}
    db = DB(tmp_path / "s.db")
    fids = load_records(db, {rec["path"]: rec, pdf["path"]: pdf}, 120.0)
    v = db.q1("SELECT * FROM files WHERE id=?", (fids[1],)) if db.q1("SELECT kind FROM files WHERE id=?", (fids[0],))["kind"] == "pdf" \
        else db.q1("SELECT * FROM files WHERE id=?", (fids[0],))
    scenes = build_scenes(db, v)
    assert [round(s.t_end) for s in scenes] == [120, 240, 250]       # 末尾（250 秒以降）は含めない
    assert all(not s.images for s in scenes)                          # 読み取り済みの画像は第1回では送らない
    assert "状態0" in scenes[0].material and any("必ず記録" in ln.text for ln in scenes[0].lines)
    assert not any("除外した部分" in ln.text for s in scenes for ln in s.lines)
    p = db.q1("SELECT * FROM files WHERE kind='pdf'")
    ps = build_scenes(db, p)
    assert ps[0].verify_images and not ps[0].images                   # PDF のページ画像は照合のときだけ
    db.close()
    res = run_semantic(make_cfg(tmp_path, tmp_path), tmp_path / "sem", {rec["path"]: rec, pdf["path"]: pdf}, None,
                       log=lambda m: None)
    d = json.loads((tmp_path / "sem" / "semantic" / "directives.json").read_text(encoding="utf-8"))
    assert res["usage"]["calls"] == 0 and any("必ず記録" in x["content"] for x in d)


def test_semantic_bridge_batches_ai_calls_without_deadlock(tmp_path):
    """資料ごとの意味理解（第1〜3回）を並行に進め、AI の呼び出しを Batch でまとめて送る（Mock で確認）。"""
    from structlab.ai.mock_provider import MockProvider
    from structlab.reading.semantic_bridge import run_semantic

    class BatchMock(MockProvider):
        def __init__(self):
            super().__init__()
            self.batches = []

        def analyze_batch(self, reqs, log=print, known=None, on_created=None):
            self.batches.append(len(reqs))
            return [self.analyze(r) for r in reqs]
    recs = {}
    for n in range(2):
        recs[f"v{n}.mp4"] = {"path": f"v{n}.mp4", "kind": "video", "duration": 250.0, "has_audio": True,
                             "content_hash": f"h{n}", "transcriber": "x",
                             "scenes": [{"index": 0, "start": 0, "end": 250, "frame_time": 1, "frame": "",
                                         "mode": "slides", "ai": {"title": "t", "text_blocks": [{"text": "必ず単位を書くこと", "role": "body"}]}}],
                             "speech": [{"start": 5.0, "end": 8.0, "text": "グラフには必ず軸ラベルを書いてください"},
                                        {"start": 130.0, "end": 133.0, "text": "考察は自分の言葉で書いてください"}]}
    cfg = make_cfg(tmp_path, tmp_path)
    cfg.data["semantic"]["ai"] = True
    prov = BatchMock()
    res = run_semantic(cfg, tmp_path / "sem", recs, prov, batch=True, log=lambda m: None)
    assert res["usage"]["calls"] > 0 and res["usage"]["failed"] == 0
    assert max(prov.batches) >= 2   # 2つの資料の依頼が同じ Batch にまとまった


def test_detail_estimate_does_not_write_images(tmp_path):
    from structlab.reading import ai_read
    frames = [_sheet(tmp_path / f"f{i}.webp", seed=i) for i in range(3)]
    rec = {"kind": "video", "role": {"kind": "lecture_detail"}, "speech": [],
           "scenes": [{"index": 0, "start": 0, "end": 10, "frame_time": 1, "frame": frames[0], "ocr": "", "same_as": None,
                       "mode": "operations", "events_with_frames": [{"time": 2.0, "frame": frames[1]},
                                                                     {"time": 3.0, "frame": frames[2]}]}]}
    plan = ai_read.plan_requests(rec)
    assert plan[0][0] == "video_detail2" and plan[0][1] > 3 * (800 * 480 // 750)
    assert not (tmp_path / "derived").exists()          # 見積もりでは拡大画像を作らない


def test_saved_ai_result_is_reused_only_for_the_identical_request(tmp_path):
    """保存済みの AI の結果は、今送るのと全く同じ依頼（本文・指示・画像の中身）のときだけ使い直す。"""
    from structlab.ai.base import AIRequest
    from structlab.reading.runner import Reader
    cfg = make_cfg(tmp_path, tmp_path)
    calls = []

    class P:
        model = "m"

        def analyze(self, req):
            calls.append(req.text)
            return AIResponse(data={"t": req.text}, model="m", input_tokens=1, output_tokens=1, raw_text="")
    r = Reader(cfg, out_dir=tmp_path / "o", provider=P(), log=lambda m: None)
    rec = {"dir": str(tmp_path / "rec")}
    mk = lambda t: (lambda: AIRequest(task="x", source_label="s", text=t, system="sys", schema={"a": 1}))  # noqa: E731
    assert r._ai_cached(rec, "k", mk("A")) == {"t": "A"}
    assert r._ai_cached(rec, "k", mk("A")) == {"t": "A"} and calls == ["A"]      # 同じ依頼は送らない
    assert r._ai_cached(rec, "k", mk("B")) == {"t": "B"} and calls == ["A", "B"]  # 中身が変われば送り直す


def test_compact_material_keeps_every_key_and_value():
    """候補「短い書き方」: すべてのキー・値（数値・式・記号・空の値）が順序どおりに残る。"""
    from structlab.reading.semantic_bridge import compact
    ai = {"title": "荷重-変形関係", "text_blocks": [{"text": "P = 2Q", "role": "body"}, {"text": "δ=1.58", "role": "label"}],
          "tables": [{"caption": "", "header": ["班", "P(kN)"], "rows": [["1", "97.2"], ["2", "76.2"]], "notes": ""}],
          "formulas": [{"latex": "\\\\delta_{sc}=P_{sc}/K_E", "plain": "δsc = Psc / KE", "where": ""}],
          "operations": [], "unreadable": [{"where": "右下", "reason": "通知で隠れている"}], "notes": "a\nb",
          "zoom_requests": [], "flag": True, "n": 0, "none": None}
    text = compact(ai)

    def leaves(x, path=""):
        if isinstance(x, dict):
            for k, v in x.items():
                yield k
                yield from leaves(v)
        elif isinstance(x, list):
            for v in x:
                yield from leaves(v)
        else:
            yield x
    pos = -1
    for leaf in leaves(ai):
        s = {None: "null", True: "true", False: "false"}.get(leaf, leaf) if not isinstance(leaf, (int, float)) or isinstance(leaf, bool) else str(leaf)
        s = str(s).replace("\n", "\\n")
        if s == "":
            continue
        i = text.find(s, pos + 1 if isinstance(leaf, str) and leaf in ai else 0)
        assert i >= 0, s
    assert len(text) < len(__import__("json").dumps(ai, ensure_ascii=False))


def test_candidate_options_keep_results_with_mock(tmp_path):
    """候補（照合のまとめ・先頭配置・短い書き方）を入れても、Mock では同じ指示・同じ判定になる（仕組みの確認。
    実際の AI での品質の同等性は pilot で確かめるまで未検証）。"""
    from structlab.ai.mock_provider import MockProvider
    from structlab.reading.semantic_bridge import run_semantic
    rec = {"path": "e.mp4", "kind": "video", "duration": 250.0, "has_audio": True, "content_hash": "h", "transcriber": "x",
           "scenes": [{"index": 0, "start": 0, "end": 250, "frame_time": 1, "frame": "", "mode": "slides",
                       "ai": {"title": "t", "text_blocks": [{"text": "単位は必ず書くこと", "role": "body"}]}}],
           "speech": [{"start": 5.0, "end": 8.0, "text": "グラフには必ず軸ラベルを書いてください"},
                      {"start": 20.0, "end": 23.0, "text": "連続コピーは絶対にしないでください"},
                      {"start": 130.0, "end": 133.0, "text": "考察は自分の言葉で書いてください"}]}
    out = {}
    for name, opts in (("base", {}), ("cand", {"verify_group": 5, "cache_layout": "prefix"})):
        cfg = make_cfg(tmp_path, tmp_path)
        cfg.data["semantic"].update({"ai": True, **opts})
        if name == "cand":
            cfg.data.setdefault("reading", {})["semantic_material"] = "compact"
        run_semantic(cfg, tmp_path / name, {rec["path"]: rec}, MockProvider(), batch=False, log=lambda m: None)
        d = json.loads((tmp_path / name / "semantic" / "directives.json").read_text(encoding="utf-8"))
        out[name] = sorted((x["class"], x["content"], x["verification"]) for x in d)
    assert out["base"] == out["cand"] and out["base"]


def _semantic_run(tmp_path, name, parallel):
    """意味理解を Batch の Mock で実行し、送った依頼の全文と、結果（指示の一覧）を返す。"""
    from structlab.ai.mock_provider import MockProvider
    from structlab.reading.semantic_bridge import run_semantic

    class BatchMock(MockProvider):
        def __init__(self):
            super().__init__()
            self.batches, self.sent = [], []

        def analyze_batch(self, reqs, log=print, known=None, on_created=None):
            self.batches.append(len(reqs))
            # 指示の番号（DB の行番号）は資料のスレッドの進み方で入れ替わる（1件ずつ送る場合も同じ）ので伏せて比べる
            ids = lambda s: re.sub(r"(#|\"id\": ?)\d+", r"\1N", s)  # noqa: E731
            self.sent += [(r.task, ids(r.source_label), ids(r.text), r.system, json.dumps(r.schema, sort_keys=True),
                           r.cache_prefix_chars if "#" not in r.text[:r.cache_prefix_chars] else -1,
                           tuple((str(i.path), i.label) for i in r.images)) for r in reqs]
            return [self.analyze(r) for r in reqs]
    speech = ["グラフには必ず軸ラベルを書いてください", "考察は自分の言葉で書いてください", "データは消さないでください",
              "提出はPDFにしてください", "単位は必ず書くこと", "計算は有効数字3桁で", "図番号を付けてください"]
    recs = {}
    for n in range(2):
        recs[f"v{n}.mp4"] = {"path": f"v{n}.mp4", "kind": "video", "duration": 500.0, "has_audio": True,
                             "content_hash": f"h{n}", "transcriber": "x",
                             "scenes": [{"index": 0, "start": 0, "end": 500, "frame_time": 1, "frame": "", "mode": "slides",
                                         "ai": {"title": "t", "text_blocks": [{"text": "必ず単位を書くこと", "role": "body"}]}}],
                             "speech": [{"start": 10.0 + 70 * k, "end": 14.0 + 70 * k, "text": s} for k, s in enumerate(speech)]}
    cfg = make_cfg(tmp_path, tmp_path)
    cfg.data["semantic"]["ai"] = True
    cfg.data["semantic"]["parallel_independent"] = parallel
    prov = BatchMock()
    run_semantic(cfg, tmp_path / name, recs, prov, batch=True, log=lambda m: None)
    d = json.loads((tmp_path / name / "semantic" / "directives.json").read_text(encoding="utf-8"))
    keep = ("content", "directive_class", "verification", "effective", "t_start", "file")
    return prov, sorted(json.dumps({k: x.get(k) for k in keep}, ensure_ascii=False, sort_keys=True) for x in d)


def test_independent_requests_are_sent_together_with_identical_content(tmp_path):
    """指示の抽出・照合をまとめて送っても、AI に送る依頼の全文（指示文・本文・画像・スキーマ・キャッシュの境目）と
    結果（指示の一覧）は1件ずつ送る場合と同じ。Batch を待つ回数だけが減る。"""
    seq, d_seq = _semantic_run(tmp_path, "seq", False)
    par, d_par = _semantic_run(tmp_path, "par", True)
    assert sorted(seq.sent) == sorted(par.sent) and len(seq.sent) > 10
    assert any(t[0] == "directive_extraction" for t in par.sent) and any(t[0] == "directive_verify" for t in par.sent)
    assert d_seq == d_par and d_par
    assert len(par.batches) < len(seq.batches)


def test_without_batch_gate_each_result_is_saved_before_the_next_call(tmp_path):
    """Batch でまとめない経路（structlab run など）では従来どおり 1 件ずつ送ってすぐ保存する。
    途中で止まっても、受け取った結果は失われない（同じ依頼を次回送り直さない）。"""
    from structlab.ai.mock_provider import MockProvider
    from structlab.reading.semantic_bridge import run_semantic

    class Stop(Exception):
        pass

    class Interrupt(MockProvider):
        def __init__(self, n):
            super().__init__()
            self.n = n

        def analyze(self, req):
            if req.task == "directive_extraction":
                self.n -= 1
                if self.n < 0:
                    raise Stop()
            return super().analyze(req)
    speech = [f"項目{k}は必ず記録してください" for k in range(6)]
    recs = {"v.mp4": {"path": "v.mp4", "kind": "video", "duration": 700.0, "has_audio": True, "content_hash": "h",
                      "transcriber": "x", "scenes": [{"index": 0, "start": 0, "end": 700, "frame_time": 1, "frame": "",
                                                      "mode": "slides", "ai": {"title": "t", "text_blocks": []}}],
                      "speech": [{"start": 10.0 + 120 * k, "end": 14.0 + 120 * k, "text": s} for k, s in enumerate(speech)]}}
    cfg = make_cfg(tmp_path, tmp_path)
    cfg.data["semantic"]["ai"] = True
    try:
        run_semantic(cfg, tmp_path / "s", recs, Interrupt(3), batch=False, log=lambda m: None)
    except Stop:
        pass
    from structlab.db import DB
    db = DB(tmp_path / "s" / "semantic.db")
    ok = db.q("SELECT 1 FROM sem_steps WHERE step='directives' AND status='ok'")
    db.close()
    assert len(ok) == 3   # 止まる前に受け取った 3 場面分は保存済み


def test_interrupted_semantic_batch_is_fetched_not_sent_again(tmp_path):
    """意味理解の Batch を送った後に中断しても、再実行では同じ依頼の集まりなら送信済みの Batch の結果を取りに行く。"""
    from structlab.ai.base import AIRequest, AIResponse
    from structlab.reading.semantic_bridge import BatchGate
    created = []

    class P:
        def __init__(self, fail):
            self.fail = fail

        def analyze_batch(self, reqs, log=print, known=None, on_created=None):
            if known:
                return [AIResponse(data={"from": known[0], "text": r.text}, model="m") for r in reqs]
            on_created(0, "batch-1")
            created.append(len(reqs))
            if self.fail:
                raise KeyboardInterrupt
            return [AIResponse(data={"text": r.text}, model="m") for r in reqs]
    state = tmp_path / "st.json"
    reqs = [AIRequest(task="t", source_label=str(i), text=f"本文{i}") for i in range(3)]
    g = BatchGate(P(fail=True), log=lambda m: None, state_path=state)
    try:
        g._send([(r, threading.Event(), {}) for r in reqs])
    except KeyboardInterrupt:
        pass
    g2 = BatchGate(P(fail=False), log=lambda m: None, state_path=state)
    slots = [(r, threading.Event(), {}) for r in reversed(reqs)]   # 順序が変わっても同じ集まりと分かる
    g2._send(slots)
    assert created == [3]
    assert all(s["resp"].data["from"] == "batch-1" and s["resp"].data["text"] == r.text for r, _, s in slots)
    assert json.loads(state.read_text(encoding="utf-8")) == {}      # 受け取ったら記録を消す
