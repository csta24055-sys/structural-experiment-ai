"""ローカルディレクトリをソースとして扱う（テスト・PC上での実行用）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from ..models import RemoteFile
from ..safety import dropbox_content_hash
from .base import Source, SourceChanged, SourceError


class LocalSource(Source):
    name = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise SourceError(f"フォルダが見つかりません: {self.root}")
        # ハッシュ計算の再利用（mtime,size が同じなら再計算しない）
        self._hash_cache: dict[str, tuple[float, int, str]] = {}

    def iter_files(self) -> Iterator[RemoteFile]:
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames.sort()
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if p.is_symlink() or not p.is_file():
                    continue
                st = p.stat()
                rel = p.relative_to(self.root).as_posix()
                cached = self._hash_cache.get(rel)
                if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                    h = cached[2]
                else:
                    h = dropbox_content_hash(p)
                    self._hash_cache[rel] = (st.st_mtime, st.st_size, h)
                yield RemoteFile(path=rel, size=st.st_size, server_modified=st.st_mtime,
                                 content_hash=h, rev=str(st.st_mtime_ns))

    def fetch(self, rf: RemoteFile, dest: Path, min_free_mb: int = 0) -> Path:
        p = (self.root / rf.path).resolve()
        if self.root not in p.parents:
            raise SourceError(f"ルート外のパス: {rf.path}")
        if not p.exists():
            raise SourceError(f"ファイルが消えています: {rf.path}")
        if p.stat().st_size != rf.size or dropbox_content_hash(p) != rf.content_hash:
            raise SourceChanged(f"スキャン後にファイルが変更されました（アップロード中の可能性）: {rf.path}")
        return p

    def is_local_passthrough(self) -> bool:
        return True
