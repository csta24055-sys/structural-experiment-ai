"""分類カテゴリ・由来・ファイル種別などの定義。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

# 情報の階層化（10分類）。キーは内部ID、値は表示名。順序は出力順。
CATEGORIES: dict[str, str] = {
    "purpose_overview": "1. 実験の目的・概要",
    "procedure": "2. 実験手順",
    "measurement_data": "3. 測定データ",
    "excel_method": "4. Excel作成方法",
    "report_method": "5. レポート作成方法",
    "formulas_graphs": "6. 必要な計算式・グラフ",
    "submission_requirements": "7. 提出物の要件",
    "instructor_instructions": "8. 先生の指示",
    "cautions_prohibitions": "9. 注意事項・禁止事項",
    "unknowns_contradictions": "10. 不明点・矛盾",
}

# 情報の由来。「資料・発言に明記されていること」と「AIの推測」を区別する。
ORIGINS: dict[str, str] = {
    "document_text": "資料に記載",
    "speech": "音声での発言",
    "screen": "画面に表示",
    "ai_inference": "AIの推測",
    "unreadable": "読み取り不能",
    "system": "処理系による記録",
}

VERIFICATIONS: dict[str, str] = {
    "evidence_matched": "根拠をローカル抽出テキストで確認済み",
    "evidence_not_found": "根拠がローカル抽出テキストに見つからない（要確認）",
    "no_local_text": "照合用のローカルテキストなし（未確認）",
    "not_applicable": "照合対象外",
    "rule_extracted": "ローカル規則で原文から抽出",
}

KIND_BY_EXT: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".bmp": "image",
    ".tif": "image", ".tiff": "image", ".webp": "image", ".heic": "image",
    ".mp4": "video", ".mov": "video", ".m4v": "video", ".avi": "video", ".mkv": "video",
    ".wmv": "video", ".webm": "video", ".mts": "video",
    ".mp3": "audio", ".m4a": "audio", ".wav": "audio", ".aac": "audio",
    ".xlsx": "excel", ".xlsm": "excel", ".xls": "excel_legacy", ".csv": "text",
    ".zip": "zip",
    ".txt": "text", ".md": "text",
    ".docx": "docx", ".pptx": "pptx",
}


def kind_for(path: str) -> tuple[str, str]:
    ext = PurePosixPath(path).suffix.lower()
    return KIND_BY_EXT.get(ext, "other"), ext


@dataclass
class RemoteFile:
    """ソース（Dropbox/ローカル）上のファイル情報。"""
    path: str              # ソースルートからの相対パス（/区切り, 先頭/なし）
    size: int
    server_modified: float  # epoch 秒
    content_hash: str
    rev: str = ""
    remote_id: str = ""


@dataclass
class UnitSpec:
    unit_type: str
    locator: dict[str, Any]
    seq: int


@dataclass
class Item:
    category: str
    statement: str
    origin: str
    evidence: str = ""
    confidence: str = "low"
    is_instructor_instruction: bool = False
    verification: str = "not_applicable"
    page: int | None = None
    t_start: float | None = None
    t_end: float | None = None
    analyzer: str = "local-rules"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category: {self.category}")
        if self.origin not in ORIGINS:
            raise ValueError(f"unknown origin: {self.origin}")
        # AI の推測は「先生の指示」として扱わない
        if self.origin in ("ai_inference", "unreadable", "system"):
            self.is_instructor_instruction = False


def fmt_time(sec: float | None) -> str:
    if sec is None:
        return ""
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
