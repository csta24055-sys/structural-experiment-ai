"""AI解析プロバイダの共通インターフェース。モデル・APIはここを実装すれば交換できる。"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AIImage:
    path: Path
    label: str  # 例: "p.3" / "00:12:34 のフレーム"
    # この画像までを API のキャッシュに置く（同じ資料の依頼で毎回先頭に付く基準画像など）。
    # 送る内容は変わらず、2件目以降はキャッシュから読まれて入力が安くなる
    cache: bool = False


@dataclass
class AIRequest:
    task: str            # pdf_page / image / video_segment / video_focus / excel_sheet / ...
    source_label: str    # 例: "資料/第1回.pdf p.3"
    text: str            # ローカル抽出結果（テキスト・表・数式候補・文字起こし等）
    images: list[AIImage] = field(default_factory=list)
    t_start: float | None = None
    t_end: float | None = None
    #: 意味理解層など、独自の指示文・出力スキーマを使う場合に指定（None なら従来の抽出用）
    system: str | None = None
    schema: dict | None = None
    max_tokens: int | None = None


@dataclass
class AIResponse:
    data: dict[str, Any]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw_text: str = ""


class AIError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class AIProvider(abc.ABC):
    name = "base"
    #: 外部サービスへデータを送信するか（True の場合は明示的な許可が必要）
    external = True
    model = ""

    @abc.abstractmethod
    def analyze(self, req: AIRequest) -> AIResponse:
        ...
