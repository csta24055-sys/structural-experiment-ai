"""資料読み取り基盤（structlab read）: 棚卸し・PDFの位置付き読み取り・画面録画の領域検出と場面分割・照合・AI書き起こしの流れ。
すべてダミーデータで確認する（実際の資料・有料APIは使わない）。"""
from __future__ import annotations

import json
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
    assert e["calls"] == {"pdf_page": 3, "video_scene": 1, "video_ops": 1, "speech_fix": 0} and e["images"] == 3 + 1 + 2
    assert e["input_tokens"] > 0 and e["output_tokens"] > 0


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
    assert list_targets(cfg, src) == ["v/c.MOV", "v/b.MP4"]  # 重複・補助ファイルを除き、大きい順
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
    assert up["with"]["path"] == "work/reading" and up["with"]["retention-days"] <= 7
    assert "GH_SECRETS_TOKEN" not in text and "echo $DROPBOX" not in text
