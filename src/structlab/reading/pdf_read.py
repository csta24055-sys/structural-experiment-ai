"""PDF のローカル読み取り（無料）: 文字情報（テキスト層）を位置付きで取り出し、見た目の行・数値・画像領域を記録する。

テキスト層がある PDF では文字そのものは正確に取れるが、取り出し順が見た目と違うことがある
（例: 表の見出しが数値の後に出る）。そのため位置（bbox）を保ち、同じ高さに並ぶ文字を「見た目の行」としてまとめる。
図・グラフ・写真は画像なので文字情報が無く、AIによる画像の読み取りと照合が必要になる。
"""
from __future__ import annotations

import re
from pathlib import Path

NUM = re.compile(r"[-−+]?\d+(?:[.,]\d+)*(?:/\d+)?")


def _lines(page) -> list[dict]:
    """行ごとの文字と位置。ページに回転指定がある場合は、表示される向きの座標に直す。
    同じ行でも大きく離れた文字（表の別のセル）は文字の位置で分ける。"""
    import pymupdf
    m = page.rotation_matrix
    rot = pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)
    out = []
    d = page.get_text("rawdict")
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            chars = [(c, sp) for sp in ln.get("spans", []) for c in sp.get("chars", [])]
            if not any(c["c"].strip() for c, _ in chars):
                continue
            dvec = pymupdf.Point(ln.get("dir") or (1, 0)) * rot
            vertical = abs(dvec.x) < 0.5
            cells: list[list[tuple]] = [[]]
            last = None
            for c, sp in chars:
                if not c["c"].strip():
                    continue
                r = pymupdf.Rect(c["bbox"]) * m
                if last is not None:
                    gap = (r.y0 - last.y1) if vertical else (r.x0 - last.x1)
                    if gap > max(sp.get("size", 10), 6) * 1.0:
                        cells.append([])
                cells[-1].append((c, sp, r))
                last = r
            for cell in cells:
                if not cell:
                    continue
                rr = cell[0][2]
                for _, _, r in cell[1:]:
                    rr = rr | r
                text = "".join(c["c"] for c, _, _ in cell)
                out.append({"text": text, "bbox": [round(rr.x0, 1), round(rr.y0, 1), round(rr.x1, 1), round(rr.y1, 1)],
                            "size": round(max(sp.get("size", 0) for _, sp, _ in cell), 1), "vertical": vertical,
                            "color": cell[0][1].get("color", 0),
                            "bold": any("Bold" in sp.get("font", "") for _, sp, _ in cell)})
    return out


def visual_rows(lines: list[dict], page_width: float) -> list[list[dict]]:
    """縦位置が重なる行をまとめ、左から並べる（表の行・ラベルと値の対応を見た目どおりに保つ）。
    横に大きく離れたもの（ページ幅の12%超）は別のまとまりとして分ける。"""
    items = sorted((ln for ln in lines if not ln["vertical"]), key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))
    rows: list[list[dict]] = []
    for ln in items:
        y0, y1 = ln["bbox"][1], ln["bbox"][3]
        cy = (y0 + y1) / 2
        for row in rows:
            ry0 = min(r["bbox"][1] for r in row)
            ry1 = max(r["bbox"][3] for r in row)
            if ry0 <= cy <= ry1:
                row.append(ln)
                break
        else:
            rows.append([ln])
    out: list[list[dict]] = []
    gap = page_width * 0.12
    for row in rows:
        row.sort(key=lambda r: r["bbox"][0])
        seg = [row[0]]
        for r in row[1:]:
            if r["bbox"][0] - seg[-1]["bbox"][2] > gap:
                out.append(seg)
                seg = [r]
            else:
                seg.append(r)
        out.append(seg)
    out.sort(key=lambda seg: (round(seg[0]["bbox"][1] / 4), seg[0]["bbox"][0]))
    return out


def read_pdf(path: Path, out_dir: Path, dpi: int = 144) -> dict:
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    with pymupdf.open(str(path)) as doc:
        if doc.needs_pass:
            raise ValueError("パスワード付きPDFは読めません")
        for page in doc:
            pno = page.number + 1
            lines = _lines(page)
            rows = visual_rows(lines, page.rect.width)
            img_boxes = []
            for info in page.get_image_info():
                x0, y0, x1, y1 = info["bbox"]
                area = (x1 - x0) * (y1 - y0) / (page.rect.width * page.rect.height)
                if area >= 0.01:  # アイコン程度の小さい画像は除く
                    img_boxes.append({"bbox": [round(v, 1) for v in (x0, y0, x1, y1)], "area_ratio": round(area, 3)})
            try:
                n_draw = len(page.get_drawings())
            except Exception:  # noqa: BLE001
                n_draw = -1
            render = out_dir / f"page_{pno:03d}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72)).save(str(render))
            numbers = [{"value": m.group(0), "line": i} for i, ln in enumerate(lines) for m in NUM.finditer(ln["text"])]
            pages.append({
                "page": pno, "size": [round(page.rect.width), round(page.rect.height)],
                "text_layer": page.get_text("text"), "lines": lines,
                "rows": [" | ".join(c["text"] for c in seg) for seg in rows],
                "numbers": numbers, "images": img_boxes, "n_drawings": n_draw, "render": str(render),
                "image_area_ratio": round(sum(b["area_ratio"] for b in img_boxes), 3),
                "needs_vision": bool(img_boxes) or n_draw > 20 or len(page.get_text("text").strip()) < 30,
            })
    return {"kind": "pdf", "pages": pages, "n_pages": len(pages)}
