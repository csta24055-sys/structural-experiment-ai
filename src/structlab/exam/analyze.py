"""資料の読み取り。既存の reading（read_pdf / read_video）を使い、出典つきの「単位」に揃える。

単位 = {material, kind: page|speech|scene|text, page, start, end, text, ...}
読み取り結果は内容のハッシュごとに保存し、同じ資料は読み直さない（差分処理・途中再開）。
読めなかった部分は coverage に「未読取」として残し、成功扱いにしない。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .discover import Material

TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?")


def _ts(s: str) -> float:
    m = TS.search(s)
    if not m:
        raise ValueError(s)
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + (int((ms or "0").ljust(3, "0")) / 1000)


def parse_subtitles(text: str) -> list[dict]:
    """.vtt / .srt / 「[mm:ss] 発言」形式の文字起こしを [{start, end, text}] にする。"""
    out: list[dict] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for b in blocks:
        lines = [x for x in b.strip().split("\n") if x.strip()]
        arrow = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if arrow is not None:
            a, _, z = lines[arrow].partition("-->")
            body = " ".join(lines[arrow + 1:]).strip()
            if body:
                out.append({"start": _ts(a), "end": _ts(z), "text": re.sub(r"<[^>]+>", "", body)})
            continue
        for x in lines:  # [12:34] 発言 / 12:34 発言
            m = re.match(r"^\[?((?:\d+:)?\d{1,2}:\d{2})\]?\s+(.+)$", x.strip())
            if m:
                out.append({"start": _ts(m.group(1)), "end": None, "text": m.group(2).strip()})
    for i, s in enumerate(out):
        if s["end"] is None:
            s["end"] = out[i + 1]["start"] if i + 1 < len(out) else s["start"] + 5.0
    return out


def _office_to_pdf(src: Path, out_dir: Path) -> Path | None:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
                       capture_output=True, timeout=600, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    p = out_dir / (src.stem + ".pdf")
    return p if p.exists() else None


def read_material(m: Material, out_dir: Path, *, ocr=None, transcriber=None, sidecar: Path | None = None,
                  video_interval: float = 2.0, log=print) -> dict:
    """1 資料を読み、{units, coverage, raw_kind} を返す。"""
    from ..reading.pdf_read import read_pdf
    out_dir.mkdir(parents=True, exist_ok=True)
    res: dict = {"material": m.id, "units": [], "coverage": {}, "notes": []}
    src = Path(m.local_path) if m.local_path else None
    if m.kind == "box_link" or src is None:
        res["coverage"] = {"status": "未取得", "detail": "動画本体を取得していない（Box の状態を参照）"}
        return res
    if m.kind == "office":
        pdf = _office_to_pdf(src, out_dir / "converted")
        if pdf is None:
            if src.suffix.lower() == ".docx":
                import docx
                text = "\n".join(p.text for p in docx.Document(str(src)).paragraphs)
                res["units"].append({"kind": "text", "page": 1, "text": text})
                res["coverage"] = {"status": "一部", "detail": "LibreOffice が無いため本文だけ読んだ（図・数式は未読取）"}
                return res
            res["coverage"] = {"status": "未読取", "detail": "LibreOffice が無く PDF に変換できない"}
            return res
        src, kind = pdf, "pdf"
    else:
        kind = m.kind
    if kind == "pdf":
        r = read_pdf(src, out_dir / "pages", dpi=96)
        empty = []
        for p in r["pages"]:
            text = p["text_layer"].strip()
            if len(text) < 10 and ocr is not None and ocr.available:
                text = ocr.image_to_text(Path(p["render"]))
            if len(text.strip()) < 10:
                empty.append(p["page"])
            heading = _heading(p)
            res["units"].append({"kind": "page", "page": p["page"], "text": text, "heading": heading,
                                 "render": p["render"]})
        res["coverage"] = {"status": "完了" if not empty else "一部",
                           "detail": f"{r['n_pages']} ページ" + (f"、文字が読めないページ {empty}" if empty else ""),
                           "unread_pages": empty}
    elif kind == "transcript":
        speech = parse_subtitles(src.read_text(encoding="utf-8", errors="replace"))
        res["units"] = [{"kind": "speech", **s} for s in speech]
        res["coverage"] = {"status": "完了" if speech else "未読取", "detail": f"字幕 {len(speech)} 件",
                           "audio": "字幕ファイル"}
    elif kind == "text":
        text = src.read_text(encoding="utf-8", errors="replace")
        speech = parse_subtitles(text)
        if len(speech) >= 3:
            res["units"] = [{"kind": "speech", **s} for s in speech]
        else:
            res["units"] = [{"kind": "text", "page": 1, "text": text}]
        res["coverage"] = {"status": "完了", "detail": "テキスト"}
    elif kind == "image":
        text = ocr.image_to_text(src) if ocr is not None and ocr.available else ""
        res["units"].append({"kind": "page", "page": 1, "text": text, "heading": text.strip().split("\n")[0][:60]})
        res["coverage"] = {"status": "完了" if text else "未読取",
                           "detail": "OCR" if text else "OCR が使えないため画像の文字は未読取"}
    elif kind in ("video", "audio"):
        res.update(_read_av(m, src, out_dir, ocr, transcriber, sidecar, video_interval, log))
    else:
        res["coverage"] = {"status": "未読取", "detail": f"未対応の形式 {kind}"}
    return res


def _heading(p: dict) -> str:
    """ページの見出し: 最も大きい文字の行（同じなら上の行）。"""
    lines = [ln for ln in p.get("lines", []) if ln.get("text", "").strip()]
    if not lines:
        return ""
    size = lambda ln: ln.get("size") or ln.get("font_size") or 0  # noqa: E731
    best = max(lines[:8], key=lambda ln: (round(size(ln), 1), -lines.index(ln)))
    return best["text"].strip()[:80]


def _read_av(m: Material, src: Path, out_dir: Path, ocr, transcriber, sidecar, interval, log) -> dict:
    from .. import media
    from ..reading.video_read import read_video
    res: dict = {"units": [], "coverage": {}}
    try:
        info = media.probe(src)
    except Exception as e:  # noqa: BLE001
        return {"units": [], "coverage": {"status": "解析不可", "detail": f"動画を読めない: {str(e)[:150]}"}}
    cov = {"duration": round(info.duration, 1), "video": "なし", "audio": "なし"}
    side_speech = parse_subtitles(sidecar.read_text(encoding="utf-8", errors="replace")) if sidecar else []
    use_asr = transcriber if (transcriber is not None and transcriber.available and not side_speech) else None
    if info.has_video and m.kind == "video":
        rec = read_video(src, out_dir / "video", ocr=ocr or media.OCR("none"), transcriber=use_asr,
                         interval=interval, log=log)
        for sc in rec["scenes"]:
            if sc.get("same_as") is None:
                res["units"].append({"kind": "scene", "start": sc["start"], "end": sc["end"],
                                     "text": sc.get("ocr", ""), "frame": sc.get("frame"), "scene": sc["index"]})
        speech = rec["speech"]
        cov["video"] = f"場面 {rec['n_unique_scenes']} 件" + ("（OCR あり）" if ocr is not None and ocr.available
                                                           else "（OCR なし: 画面の文字は未読取）")
    else:
        speech = []
        if use_asr is not None and info.has_audio:
            from ..reading.video_read import audio_profile, transcribe_spans, voiced_spans
            ap = audio_profile(src, info, info.duration)
            speech = transcribe_spans(src, out_dir, use_asr, voiced_spans(ap, info.duration), 600.0, ap)
    if side_speech:
        speech = side_speech
        cov["audio"] = f"字幕ファイル {len(side_speech)} 件"
    elif use_asr is not None and info.has_audio:
        cov["audio"] = f"文字起こし {len(speech)} 件（{getattr(use_asr, 'model_name', use_asr.name)}）"
    elif info.has_audio:
        cov["audio"] = "未解析（文字起こしが無効・未導入）"
    res["units"] += [{"kind": "speech", "start": s["start"], "end": s["end"], "text": s["text"]} for s in speech]
    audio_done = not (info.has_audio and cov["audio"].startswith("未解析"))
    video_done = not (info.has_video and m.kind == "video" and (ocr is None or not ocr.available))
    cov["status"] = "完了" if audio_done and video_done else ("一部" if audio_done or video_done else "未解析")
    cov["detail"] = f"映像: {cov['video']} / 音声: {cov['audio']}"
    res["coverage"] = cov
    return res


def cached_read(m: Material, work: Path, **kw) -> dict:
    """内容ハッシュで読み取り結果を保存・再利用する（途中停止後は未処理の資料から再開）。"""
    key = m.sha256 or m.id
    path = work / "readings" / f"{key[:24]}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    res = read_material(m, work / "readings" / key[:24], **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return res
