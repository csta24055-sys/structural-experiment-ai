"""PDF: ページ単位でテキスト・表・図・数式候補を抽出する。"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import UnitSpec
from .base import Ctx, Extraction, Processor

MATH_CHARS = re.compile(r"[=＝σεΔδπΣ∑∫√×÷±≦≧≤≥∝∞²³⁻¹αβγλμφθ]|\^|/\s*[A-Za-z0-9(]")
MATH_FONT = re.compile(r"math|symbol|cmmi|cmsy|cmex|stix|cambria", re.IGNORECASE)


def _open(path: Path):
    import pymupdf
    return pymupdf.open(str(path))


class PDFProcessor(Processor):
    kind = "pdf"

    def plan(self, path: Path, ctx: Ctx) -> list[UnitSpec]:
        with _open(path) as doc:
            if doc.needs_pass:
                raise ValueError("パスワード付きPDFは解析できません")
            n = doc.page_count
        return [UnitSpec("pdf_page", {"page": i + 1}, i) for i in range(n)]

    def label(self, unit: UnitSpec) -> str:
        return f"p.{unit.locator['page']}"

    def extract(self, path: Path, unit: UnitSpec, ctx: Ctx) -> Extraction:
        import pymupdf

        pno = unit.locator["page"]
        ex = Extraction(page=pno)
        with _open(path) as doc:
            page = doc[pno - 1]
            text = page.get_text("text") or ""
            # 数式候補（記号・数式フォント）
            formulas: list[str] = []
            d = page.get_text("dict")
            for b in d.get("blocks", []):
                for ln in b.get("lines", []):
                    line_text = "".join(s.get("text", "") for s in ln.get("spans", []))
                    if not line_text.strip():
                        continue
                    if any(MATH_FONT.search(s.get("font", "")) for s in ln.get("spans", [])) \
                            or MATH_CHARS.search(line_text):
                        formulas.append(line_text.strip())
            # 表
            tables_md: list[str] = []
            try:
                tabs = page.find_tables()
                for t in tabs.tables:
                    rows = t.extract()
                    if not rows:
                        continue
                    md = ["| " + " | ".join("" if c is None else str(c).replace("\n", " ") for c in r) + " |"
                          for r in rows[:200]]
                    tables_md.append("\n".join(md))
            except Exception as e:  # noqa: BLE001  表検出の失敗はページ全体の失敗にしない
                ex.data["table_error"] = str(e)
            images = page.get_image_info()
            try:
                n_draw = len(page.get_drawings())
            except Exception:  # noqa: BLE001
                n_draw = -1
            ex.data.update({
                "chars": len(text.strip()), "n_images": len(images), "n_drawings": n_draw,
                "n_tables": len(tables_md), "tables": tables_md, "formula_candidates": formulas[:200],
                "page_size": [round(page.rect.width), round(page.rect.height)],
            })
            scanned = len(text.strip()) < 30 and (images or n_draw > 0)
            need_render = ctx.ai_enabled or (scanned and ctx.ocr.available)
            if need_render:
                out = ctx.artifacts_dir / f"page_{pno:04d}.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                dpi = int(ctx.cfg.data["pdf"]["render_dpi"])
                page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72)).save(str(out))
                ex.images.append((out, f"p.{pno} のページ画像"))
                ex.data["render"] = str(out)
                if scanned and ctx.ocr.available:
                    ocr_text = ctx.ocr.image_to_text(out)
                    ex.data["ocr_text"] = ocr_text
                    text = text + "\n" + ocr_text
        if scanned:
            ex.needs_ai.append("文字情報がほぼ無い（スキャン画像の可能性）")
        if images:
            ex.needs_ai.append(f"画像 {len(images)} 個（図・写真の内容はAI解析が必要）")
        if n_draw > 20:
            ex.needs_ai.append(f"ベクター図形 {n_draw} 要素（グラフ・図の可能性）")
        ex.text = text + "\n" + "\n".join(tables_md)
        ex.segments = [{"text": text, "origin": "document_text", "page": pno}]
        parts = [f"### ページ本文\n{text.strip()}"]
        if tables_md:
            parts.append("### 検出した表\n" + "\n\n".join(tables_md))
        if formulas:
            parts.append("### 数式候補（記号を含む行）\n" + "\n".join(formulas[:100]))
        parts.append(f"### 図要素\n画像 {len(images)} 個 / ベクター図形 {n_draw} 要素")
        ex.ai_text = "\n\n".join(parts)
        return ex
