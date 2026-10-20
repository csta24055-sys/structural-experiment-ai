"""既存の解析単位（動画区間・PDFページ・シート範囲など）と時刻同期タイムラインから「場面」を組み立てる。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..classify import split_sentences
from ..db import DB
from ..models import UnitSpec, fmt_time
from ..processors import get_processor

SCENE_TYPES = ("video_segment", "pdf_page", "image", "excel_workbook", "excel_sheet", "text_chunk", "office_part")


@dataclass
class Line:
    text: str
    origin: str               # speech / screen / document
    t_start: float | None = None
    t_end: float | None = None
    page: int | None = None
    frame: str | None = None  # 対応する画面（キーフレーム）のパス


@dataclass
class Scene:
    unit_id: int
    seq: int
    unit_type: str
    label: str
    t_start: float | None
    t_end: float | None
    page: int | None
    lines: list[Line] = field(default_factory=list)
    material: str = ""
    images: list[tuple[Path, str]] = field(default_factory=list)
    transcribed: bool | None = None   # 音声がある区間で文字起こしされたか（None: 音声なし）
    visual: bool | None = None        # 映像がある区間で画面を取得したか（None: 映像なし）
    needs_ai: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.t_start is not None and self.t_end is not None:
            return max(0.0, self.t_end - self.t_start)
        return 0.0

    def where(self) -> str:
        if self.t_start is not None:
            return f"{fmt_time(self.t_start)}–{fmt_time(self.t_end)}"
        if self.page is not None:
            return f"p.{self.page}"
        return self.label


def is_time_based(kind: str) -> bool:
    return kind in ("video", "audio")


def build_scenes(db: DB, frow) -> list[Scene]:
    proc = get_processor(frow["kind"])
    units = db.q("SELECT * FROM units WHERE file_id=? AND file_hash=? AND status='done' ORDER BY seq, id",
                 (frow["id"], frow["content_hash"] or ""))
    focus = [u for u in units if u["unit_type"] == "video_focus"]
    scenes: list[Scene] = []
    for u in units:
        if u["unit_type"] not in SCENE_TYPES:
            continue
        lo = json.loads(u["locator"])
        rec = json.loads(u["extract_json"] or "{}")
        label = proc.label(UnitSpec(u["unit_type"], lo, u["seq"])) if proc else ""
        sc = Scene(unit_id=u["id"], seq=len(scenes) + 1, unit_type=u["unit_type"], label=label,
                   t_start=lo.get("start"), t_end=lo.get("end"), page=lo.get("page") or rec.get("page"),
                   needs_ai=list(rec.get("needs_ai") or []))
        if u["unit_type"] == "video_segment":
            data = rec.get("data") or {}
            if lo.get("has_audio"):
                sc.transcribed = data.get("transcriber", "none") != "none"
            if lo.get("has_video"):
                sc.visual = bool(data.get("n_sampled_frames") or data.get("n_keyframes"))
            seen_frames: set[str] = set()
            for r in db.q("SELECT * FROM timeline WHERE unit_id=? ORDER BY t_start", (u["id"],)):
                if r["speech"]:
                    sc.lines.append(Line(r["speech"], "speech", r["t_start"], r["t_end"], frame=r["frame_path"]))
                if r["screen_text"] and r["frame_path"] not in seen_frames:
                    seen_frames.add(r["frame_path"])
                    sc.lines.append(Line(r["screen_text"], "screen", r["frame_time"], r["frame_time"],
                                         frame=r["frame_path"]))
            sc.material = rec.get("ai_text", "")
            sc.images = [(Path(p), lbl) for p, lbl in rec.get("images", []) if Path(p).exists()]
            # 重要場面の追加解析（高解像度フレーム）を、その時刻を含む場面に添える
            for f in focus:
                flo = json.loads(f["locator"])
                if sc.t_start <= flo["start"] < sc.t_end:
                    frec = json.loads(f["extract_json"] or "{}")
                    sc.material += f"\n\n### 重要場面の追加解析（{flo.get('reason', '')}）\n" + frec.get("ai_text", "")
                    sc.images += [(Path(p), lbl) for p, lbl in frec.get("images", []) if Path(p).exists()]
        else:
            text = rec.get("text", "")
            for s in split_sentences(text):
                sc.lines.append(Line(s, "document", page=sc.page))
            sc.material = rec.get("ai_text", "") or text
            sc.images = [(Path(p), lbl) for p, lbl in rec.get("images", []) if Path(p).exists()]
        scenes.append(sc)
    return scenes


def lines_text(lines: list[Line]) -> str:
    out = []
    for ln in lines:
        tag = {"speech": "発話", "screen": "画面", "document": "資料"}[ln.origin]
        when = f"[{fmt_time(ln.t_start)}] " if ln.t_start is not None else (f"[p.{ln.page}] " if ln.page else "")
        out.append(f"{when}{tag}: {ln.text}")
    return "\n".join(out)


def window_lines(scenes: list[Scene], t0: float | None, t1: float | None, page: int | None, expand: float) -> list[Line]:
    """照合用に、時刻（またはページ）の前後を広げた範囲の元の発話・画面・資料を集める。"""
    out: list[Line] = []
    for sc in scenes:
        for ln in sc.lines:
            if t0 is not None and ln.t_start is not None:
                if t0 - expand <= ln.t_start <= (t1 if t1 is not None else t0) + expand:
                    out.append(ln)
            elif page is not None and ln.page is not None:
                if abs(ln.page - page) <= 1:
                    out.append(ln)
            elif t0 is None and page is None:
                out.append(ln)
    return out
