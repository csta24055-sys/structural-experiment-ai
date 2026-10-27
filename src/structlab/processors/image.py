"""画像: 文字（OCR）・図・数式・表はAI解析、ローカルでは寸法とOCRのみ。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from ..models import UnitSpec
from .base import Ctx, Extraction, Processor

Image.MAX_IMAGE_PIXELS = 200_000_000  # 画像爆弾対策（約2億画素まで）

try:  # iPhone の写真（HEIC）に対応
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pillow_heif = None


class ImageProcessor(Processor):
    kind = "image"

    def plan(self, path: Path, ctx: Ctx) -> list[UnitSpec]:
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
        # マルチページTIFF等はフレームごとに分割
        return [UnitSpec("image", {"frame": i}, i) for i in range(min(n, 500))]

    def label(self, unit: UnitSpec) -> str:
        return f"画像{'' if unit.locator.get('frame', 0) == 0 else ' frame ' + str(unit.locator['frame'])}"

    def extract(self, path: Path, unit: UnitSpec, ctx: Ctx) -> Extraction:
        ex = Extraction()
        frame = unit.locator.get("frame", 0)
        with Image.open(path) as im:
            if frame:
                im.seek(frame)
            im = ImageOps.exif_transpose(im)
            w, h = im.size
            out = ctx.artifacts_dir / f"image_{frame:03d}.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            cp = im.convert("RGB")
            cp.thumbnail((2400, 2400))
            cp.save(out, quality=88)
        ocr_text = ctx.ocr.image_to_text(out) if ctx.ocr.available else ""
        ex.data = {"width": w, "height": h, "ocr": ctx.ocr.label, "ocr_chars": len(ocr_text)}
        ex.images.append((out, "画像"))
        ex.text = ocr_text
        ex.segments = [{"text": ocr_text, "origin": "document_text"}] if ocr_text else []
        ex.ai_text = f"### 画像サイズ\n{w}x{h}\n\n### ローカルOCR結果\n{ocr_text or '（OCRなし）'}"
        ex.needs_ai.append("画像内の図・数式・表の解釈にはAI解析が必要")
        return ex
