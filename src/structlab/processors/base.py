"""ファイル種別ごとの処理器の共通部分。

処理器は (1) plan: ファイルを小さな解析単位に分割 (2) extract: 1単位をローカル抽出
の2段階で動く。AI解析はパイプライン側が Extraction から組み立てる。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..models import UnitSpec


@dataclass
class Extraction:
    #: 根拠照合・規則分類に使う全テキスト
    text: str = ""
    #: 規則分類用のセグメント [{text, origin, page?, t_start?, t_end?}]
    segments: list[dict] = field(default_factory=list)
    #: AIに渡す画像 [(path, label)]
    images: list[tuple[Path, str]] = field(default_factory=list)
    #: AIに渡す整形済みテキスト
    ai_text: str = ""
    #: DB に保存する構造化データ（表・数式・グラフ定義など）
    data: dict[str, Any] = field(default_factory=dict)
    #: ローカル抽出だけでは不十分な理由（図・スキャン画像など）。空なら十分。
    needs_ai: list[str] = field(default_factory=list)
    #: 動画の時刻対応 [{t_start,t_end,speech,frame_path,frame_time,screen_text,important,reason}]
    timeline: list[dict] = field(default_factory=list)
    #: 追加解析する単位（重要場面など）
    followups: list[UnitSpec] = field(default_factory=list)
    page: int | None = None
    t_start: float | None = None
    t_end: float | None = None


@dataclass
class Ctx:
    cfg: Any
    artifacts_dir: Path          # この単位の成果物（キーフレーム等）を保存する場所
    ocr: Any
    transcriber: Any
    ai_enabled: bool
    #: 動画の既存タイムライン取得 (start, end) -> rows
    timeline_lookup: Callable[[float, float], list[dict]] | None = None


class Processor(abc.ABC):
    kind = "base"

    @abc.abstractmethod
    def plan(self, path: Path, ctx: Ctx) -> list[UnitSpec]:
        ...

    @abc.abstractmethod
    def extract(self, path: Path, unit: UnitSpec, ctx: Ctx) -> Extraction:
        ...

    def label(self, unit: UnitSpec) -> str:
        return ""


class UnsupportedFile(Exception):
    pass
