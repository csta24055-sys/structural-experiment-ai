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
    # 可逆（WebP lossless）のまま送る（Excel・レポートの作り方の画面。JPEG に変換して文字の輪郭を崩さない）
    lossless: bool = False


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
    #: text の先頭からこの文字数までは、同じ資料の他の依頼と完全に同じ（指示文・資料全体の理解など）。
    #: その境目で文字を2つのブロックに分け、前半を API のキャッシュに置く（送る内容・順序は同じ。料金だけが変わる）
    cache_prefix_chars: int = 0
    #: True なら、キャッシュする先頭部分（text[:cache_prefix_chars]）を画像より前に置く（画像の前に共通の文脈を置き、
    #: 画像ごとに違う依頼でも先頭部分をキャッシュできるようにする。候補の並べ方。既定は従来どおり画像が先）
    prefix_before_images: bool = False
    #: キャッシュの有効期間（"" は既定の 5 分、"1h" は 1 時間）。順番に送る依頼が同じ先頭部分を使い直す場合など
    cache_ttl: str = ""


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
