#!/usr/bin/env python3
"""Write the answers into the blanks of the original worksheet PDF.

Usage
    python3 fill_assignment.py                 # uses materials/06_保有水平耐力計算とは_課題.pdf
    python3 fill_assignment.py path/to/課題.pdf  # explicit input
    python3 fill_assignment.py --demo          # pipeline self-test on a SYNTHETIC mock sheet

Outputs (git-ignored, never commit: they contain the distributed worksheet)
    output/filled_assignment.pdf
    output/filled_assignment_p1.png  (150 dpi render for visual inspection)

How placement works
    The script does not hard-code coordinates.  It finds the section headings
    ("2. 剛心の算定" ...) with pdfplumber, treats the space between one heading
    and the next as that section's blank, skips any printed text already in
    that space, and shrinks the font (9pt -> 5.5pt) until every line fits.
    It prints a report and refuses to finish silently if a block does not fit
    or would overlap printed text.  ALWAYS look at the PNG afterwards.
"""
import re
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "materials" / "06_保有水平耐力計算とは_課題.pdf"
OUT_DIR = HERE / "output"
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msgothic.ttc",
]
INK = (0.05, 0.15, 0.55)  # dark blue "pen" colour, distinguishable from print

# Concise answers per section (K kept symbolic, units retained).
ANSWERS = {
    "重心": [
        "ΣN=100+200+100+100+200+100=800 kN",
        "GX=Σ(N·X)/ΣN=(100×0+200×6+100×12)×2/800=4800/800=6.0 m",
        "GY=Σ(N·Y)/ΣN=(400×0+400×8)/800=3200/800=4.0 m　∴G=(6.0, 4.0) m",
    ],
    "剛心": [
        "RX=Σ(KY·X)/ΣKY=(2K×0+2K×6+8K×12)/(2K+2K+8K)=108K/12K=9.0 m",
        "RY=Σ(KX·Y)/ΣKX=(3K×0+3K×8)/(3K+3K)=24K/6K=4.0 m",
        "∴R=(9.0, 4.0) m　（耐震壁KY=8Kは柱＋壁で1回のみ計上）",
    ],
    "偏心距離": [
        "eX=|RX−GX|=|9.0−6.0|=3.0 m",
        "eY=|RY−GY|=|4.0−4.0|=0 m",
    ],
    "ねじり剛性": [
        "Σ{KX(Y−RY)²}=3K(0−4)²+3K(8−4)²=48K+48K=96K m²",
        "Σ{KY(X−RX)²}=2K(0−9)²+2K(6−9)²+8K(12−9)²=162K+18K+72K=252K m²",
        "KR=96K+252K=348K m²",
    ],
    "弾力半径": [
        "reX=√(KR/ΣKX)=√(348K/6K)=√58=7.62 m",
        "reY=√(KR/ΣKY)=√(348K/12K)=√29=5.39 m",
    ],
    "偏心率": [
        "ReX=eY/reX=0/7.62=0 ≦0.15 OK",
        "ReY=eX/reY=3.0/5.39=0.557 ＞0.15 NG",
        "→Y方向が0.15を超える（Feによる割増しを検討）",
    ],
}
ORDER = ["重心", "剛心", "偏心距離", "ねじり剛性", "弾力半径", "偏心率"]
HEADING_RE = re.compile(r"^\s*[（(]?([1-6１-６])[)）\.．、:：]?\s*(.*)$")


def register_font():
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            pdfmetrics.registerFont(TTFont("JP", p))
            return p
    sys.exit("No Japanese-capable font found; edit FONT_CANDIDATES.")


def find_headings(page):
    """Return {keyword: line} for numbered section headings."""
    lines = page.extract_text_lines(layout=False, strip=True)
    found = {}
    for ln in lines:
        text = ln["text"].replace(" ", "")
        m = HEADING_RE.match(text)
        if not m:
            continue
        rest = m.group(2)
        for kw in ORDER:
            # 偏心率 must not capture "偏心距離"; require 'kw' then の/算定 or end
            if rest.startswith(kw) and kw not in found:
                found[kw] = ln
                break
    return found, lines


def plan_blocks(page, headings, lines):
    """For each heading, the free rectangle below it (pdfplumber top-down coords)."""
    keys = sorted(headings, key=lambda k: headings[k]["top"])
    blocks = []
    for i, kw in enumerate(keys):
        h = headings[kw]
        top = h["bottom"] + 2
        bottom = (headings[keys[i + 1]]["top"] - 3) if i + 1 < len(keys) else page.height - 28
        x0 = h["x0"] + 8
        x1 = page.width - 28
        # printed text already inside the region (sub-prompts, formulas, figure labels)
        inside = [ln for ln in lines if ln is not h and ln["top"] >= top - 1
                  and ln["bottom"] <= bottom + 1 and ln["x1"] > x0 and ln["x0"] < x1]
        if inside:
            top = max(ln["bottom"] for ln in inside) + 2  # write below printed text
        blocks.append(dict(kw=kw, x0=x0, x1=x1, top=top, bottom=bottom,
                           printed=[ln["text"] for ln in inside]))
    return blocks


def fit(block, text_lines, max_size=9.0, min_size=5.5):
    width = block["x1"] - block["x0"]
    height = block["bottom"] - block["top"]
    size = max_size
    while size >= min_size:
        lead = size * 1.35
        w = max(pdfmetrics.stringWidth(t, "JP", size) for t in text_lines)
        if w <= width and lead * len(text_lines) <= height:
            return size, lead, w, height
        size -= 0.25
    return None, None, None, height


def build_overlay(page_w, page_h, placements):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setFillColorRGB(*INK)
    for blk, size, lead, text_lines in placements:
        c.setFont("JP", size)
        y = page_h - blk["top"] - size  # convert top-down -> PDF bottom-up
        for t in text_lines:
            c.drawString(blk["x0"], y, t)
            y -= lead
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def fill(src: Path, dst: Path):
    register_font()
    with pdfplumber.open(src) as pdf:
        page = pdf.pages[0]
        headings, lines = find_headings(page)
        missing = [k for k in ORDER[1:] if k not in headings]
        if missing:
            print("!! headings not found:", missing)
            print("   page text lines were:")
            for ln in lines:
                print("   ", round(ln["top"]), ln["text"])
            sys.exit("Stop: inspect the worksheet and adjust HEADING_RE / ORDER.")
        blocks = plan_blocks(page, headings, lines)
        pw, ph = float(page.width), float(page.height)

    placements, problems = [], []
    for blk in blocks:
        size, lead, w, h = fit(blk, ANSWERS[blk["kw"]])
        tag = f"[{blk['kw']}] area {blk['x1']-blk['x0']:.0f}x{h:.0f}pt"
        if blk["printed"]:
            tag += f", below printed text {blk['printed']}"
        if size is None:
            problems.append(tag + " -> DOES NOT FIT")
            continue
        print(f"{tag} -> {size:.2f}pt, {len(ANSWERS[blk['kw']])} lines, width {w:.0f}pt")
        if size < 6.5:
            problems.append(tag + f" -> small font {size:.2f}pt, check legibility")
        placements.append((blk, size, lead, ANSWERS[blk["kw"]]))

    reader = PdfReader(str(src))
    writer = PdfWriter()
    writer.append(reader)
    writer.pages[0].merge_page(build_overlay(pw, ph, placements))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as fh:
        writer.write(fh)
    png_base = dst.with_name(dst.stem + "_p1")
    subprocess.run(["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "1",
                    "-singlefile", str(dst), str(png_base)], check=True)
    print("wrote", dst, "and", png_base.with_suffix(".png"))
    if problems:
        print("!! CHECK:", *problems, sep="\n   ")
        return 1
    return 0


def make_demo_sheet(path: Path):
    """A SYNTHETIC stand-in worksheet (not the distributed one) for self-test."""
    register_font()
    from reportlab.lib.pagesizes import A4
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("JP", 13)
    c.drawString(50, h - 50, "【デモ用の模擬シート】偏心率の算定（原本ではありません）")
    c.rect(50, h - 300, w - 100, 230)
    c.setFont("JP", 9)
    c.drawString(60, h - 85, "（ここに課題の平面図が入る想定）")
    titles = ["1. 重心の算定", "2. 剛心の算定", "3. 偏心距離の算定",
              "4. ねじり剛性の算定", "5. 弾力半径の算定", "6. 偏心率の算定"]
    y = h - 325
    for t in titles:
        c.setFont("JP", 11)
        c.drawString(50, y, t)
        y -= 82
    c.save()


def main(argv):
    if argv and argv[0] == "--demo":
        demo = OUT_DIR / "demo_mock_sheet.pdf"
        OUT_DIR.mkdir(exist_ok=True)
        make_demo_sheet(demo)
        return fill(demo, OUT_DIR / "demo_filled.pdf")
    src = Path(argv[0]) if argv else DEFAULT_INPUT
    if not src.exists():
        sys.exit(f"Worksheet PDF not found: {src}\n"
                 "Place the distributed PDF under materials/ (git-ignored) and rerun.")
    return fill(src, OUT_DIR / "filled_assignment.pdf")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
