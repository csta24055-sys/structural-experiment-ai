"""ファイル取得元の共通インターフェース。元ファイルは読み取り専用で扱う。"""
from __future__ import annotations

import abc
from pathlib import Path
from typing import Iterator

from ..models import RemoteFile


class SourceError(Exception):
    pass


class SourceChanged(SourceError):
    """取得中・取得前にファイルが更新された（アップロード途中の可能性）。"""


class Source(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def iter_files(self) -> Iterator[RemoteFile]:
        """ルート以下のファイルを再帰的に列挙する（ページングはこの中で処理）。"""

    @abc.abstractmethod
    def fetch(self, rf: RemoteFile, dest: Path, min_free_mb: int = 0) -> Path:
        """ファイルをローカルに用意してパスを返す。大容量でもメモリに一括で載せない。
        取得後に content_hash を照合し、一致しなければ SourceError。"""

    def is_local_passthrough(self) -> bool:
        """fetch がコピーせず元パスを返す（削除してはいけない）なら True。"""
        return False
