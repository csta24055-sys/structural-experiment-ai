"""テキスト（.txt/.md/.csv）と Word/PowerPoint（.docx/.pptx）の本文抽出。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ..models import UnitSpec
from .base import Ctx, Extraction, Processor

MAX_XML = 50 * 1024 * 1024


def detect_encoding(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(65536)
    for enc in ("utf-8-sig", "cp932"):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


class TextProcessor(Processor):
    kind = "text"

    def plan(self, path: Path, ctx: Ctx) -> list[UnitSpec]:
        maxc = int(ctx.cfg.data["pdf"]["max_chars_per_unit"])
        enc = detect_encoding(path)
        units, start, chars, n = [], 1, 0, 0
        with open(path, encoding=enc, errors="replace") as f:
            for n, line in enumerate(f, 1):
                chars += len(line)
                if chars >= maxc:
                    units.append(UnitSpec("text_chunk", {"line_start": start, "line_end": n, "enc": enc}, len(units)))
                    start, chars = n + 1, 0
        if start <= n or not units:
            units.append(UnitSpec("text_chunk", {"line_start": start, "line_end": max(n, start), "enc": enc}, len(units)))
        return units

    def label(self, unit: UnitSpec) -> str:
        return f"{unit.locator['line_start']}〜{unit.locator['line_end']}行"

    def extract(self, path: Path, unit: UnitSpec, ctx: Ctx) -> Extraction:
        lo = unit.locator
        buf = []
        with open(path, encoding=lo["enc"], errors="replace") as f:
            for n, line in enumerate(f, 1):
                if n > lo["line_end"]:
                    break
                if n >= lo["line_start"]:
                    buf.append(line)
        text = "".join(buf)
        return Extraction(text=text, ai_text=text, segments=[{"text": text, "origin": "document_text"}],
                          data={"chars": len(text)})


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def _xml(zf: zipfile.ZipFile, name: str):
    if zf.getinfo(name).file_size > MAX_XML:
        raise ValueError(f"XMLが大きすぎます: {name}")
    return ET.fromstring(zf.read(name))


def _maths(root) -> list[str]:
    return ["".join(t.text or "" for t in m.iter(f"{M}t")) for m in root.iter(f"{M}oMath")]


class OfficeProcessor(Processor):
    kind = "office"

    def plan(self, path: Path, ctx: Ctx) -> list[UnitSpec]:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if path.suffix.lower() == ".pptx":
                slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                key=lambda n: int(re.findall(r"\d+", n)[-1]))
                return [UnitSpec("office_part", {"part": s, "slide": i + 1}, i) for i, s in enumerate(slides)]
            root = _xml(zf, "word/document.xml")
            n_par = sum(1 for _ in root.iter(f"{W}p"))
        per = 150
        return [UnitSpec("office_part", {"part": "word/document.xml", "p_start": s, "p_end": min(s + per, n_par)}, i)
                for i, s in enumerate(range(0, max(n_par, 1), per))]

    def label(self, unit: UnitSpec) -> str:
        lo = unit.locator
        return f"スライド{lo['slide']}" if "slide" in lo else f"段落{lo['p_start'] + 1}〜{lo['p_end']}"

    def extract(self, path: Path, unit: UnitSpec, ctx: Ctx) -> Extraction:
        lo = unit.locator
        with zipfile.ZipFile(path) as zf:
            root = _xml(zf, lo["part"])
            maths = _maths(root)
            if "slide" in lo:
                paras = ["".join(t.text or "" for t in p.iter(f"{A}t")) for p in root.iter(f"{A}p")]
                n_pics = sum(1 for el in root.iter() if el.tag.endswith("}pic"))
                notes_name = f"ppt/notesSlides/notesSlide{lo['slide']}.xml"
                notes = ""
                if notes_name in zf.namelist():
                    nr = _xml(zf, notes_name)
                    notes = "\n".join("".join(t.text or "" for t in p.iter(f"{A}t")) for p in nr.iter(f"{A}p"))
            else:
                ps = list(root.iter(f"{W}p"))[lo["p_start"]:lo["p_end"]]
                paras = ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in ps]
                maths = [m for p in ps for m in _maths(p)]
                n_pics = sum(1 for p in ps for el in p.iter() if el.tag.endswith("}drawing"))
                notes = ""
        body = "\n".join(p for p in paras if p.strip())
        ex = Extraction(text=body + "\n" + notes + "\n" + "\n".join(maths),
                        segments=[{"text": body + "\n" + notes, "origin": "document_text"}],
                        data={"n_pictures": n_pics, "equations": maths, "notes": notes})
        ex.ai_text = f"### 本文\n{body}\n\n### 数式\n" + ("\n".join(maths) or "（なし）") + \
            (f"\n\n### ノート\n{notes}" if notes else "")
        if n_pics:
            ex.needs_ai.append(f"図・画像 {n_pics} 個（内容の解釈にはAI解析が必要。画像は未送信）")
        return ex
