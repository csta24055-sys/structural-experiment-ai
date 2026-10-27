from .base import Ctx, Extraction, Processor, UnsupportedFile
from .excel import ExcelProcessor
from .image import ImageProcessor
from .pdf import PDFProcessor
from .text import OfficeProcessor, TextProcessor
from .video import VideoProcessor

_REGISTRY: dict[str, Processor] = {
    "pdf": PDFProcessor(),
    "image": ImageProcessor(),
    "excel": ExcelProcessor(),
    "video": VideoProcessor(),
    "audio": VideoProcessor(),
    "text": TextProcessor(),
    "docx": OfficeProcessor(),
    "pptx": OfficeProcessor(),
}

UNSUPPORTED_HINTS = {
    "excel_legacy": "旧形式 .xls は未対応です。Excel で .xlsx に保存し直してください。",
    "other": "未対応の形式です（一覧には記録済み）。",
}


def get_processor(kind: str) -> Processor | None:
    return _REGISTRY.get(kind)


__all__ = ["Ctx", "Extraction", "Processor", "UnsupportedFile", "get_processor", "UNSUPPORTED_HINTS"]
