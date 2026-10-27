"""安全性: ZIP爆弾・危険パス対策、ディスク容量確認、ハッシュ計算。"""
from __future__ import annotations

import hashlib
import shutil
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DROPBOX_BLOCK = 4 * 1024 * 1024
CHUNK = 1024 * 1024


class SafetyError(Exception):
    pass


class DiskSpaceError(SafetyError):
    pass


def dropbox_content_hash(path: Path) -> str:
    """Dropbox の content_hash と同じアルゴリズム（4MiBブロックのSHA256を連結してSHA256）。
    ファイル全体をメモリに読み込まない。"""
    outer = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(DROPBOX_BLOCK)
            if not block:
                break
            outer.update(hashlib.sha256(block).digest())
    return outer.hexdigest()


class DropboxHasher:
    """ストリーミングで受け取りながら content_hash を計算する。"""

    def __init__(self) -> None:
        self.outer = hashlib.sha256()
        self.buf = bytearray()

    def update(self, data: bytes) -> None:
        self.buf += data
        while len(self.buf) >= DROPBOX_BLOCK:
            self.outer.update(hashlib.sha256(self.buf[:DROPBOX_BLOCK]).digest())
            del self.buf[:DROPBOX_BLOCK]

    def hexdigest(self) -> str:
        if self.buf:
            self.outer.update(hashlib.sha256(self.buf).digest())
            self.buf = bytearray()
        return self.outer.hexdigest()


def ensure_free_space(directory: Path, need_bytes: int, min_free_mb: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    need = need_bytes + min_free_mb * 1024 * 1024
    if free < need:
        raise DiskSpaceError(
            f"ディスク空き容量不足: 空き {free / 1e6:.0f}MB / 必要 {need / 1e6:.0f}MB（{directory}）"
        )


def safe_member_path(name: str) -> PurePosixPath:
    """ZIP内のパスを検証し、展開先からはみ出さない相対パスを返す。"""
    n = unicodedata.normalize("NFC", name.replace("\\", "/"))
    if "\x00" in n:
        raise SafetyError(f"不正な文字を含むパス: {name!r}")
    p = PurePosixPath(n)
    if p.is_absolute() or n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        raise SafetyError(f"絶対パスを含むZIPエントリ: {name!r}")
    parts = [x for x in p.parts if x not in ("", ".")]
    if any(x == ".." for x in parts):
        raise SafetyError(f"親ディレクトリ参照を含むZIPエントリ: {name!r}")
    if not parts:
        raise SafetyError(f"空のパス: {name!r}")
    return PurePosixPath(*parts)


def decode_zip_name(info: zipfile.ZipInfo) -> str:
    """日本語Windowsで作られたZIP（CP932）の文字化けを補正する。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp932")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


@dataclass
class ZipLimits:
    max_total_bytes: int
    max_files: int
    max_ratio: float


@dataclass
class ExtractedMember:
    name: str           # ZIP内の相対パス
    path: Path          # 展開後のローカルパス
    size: int


def safe_extract_zip(zip_path: Path, dest: Path, limits: ZipLimits, min_free_mb: int = 0) -> tuple[
        list[ExtractedMember], list[str]]:
    """ZIPを安全に展開する。戻り値は (展開したファイル, スキップ理由のリスト)。

    - 絶対パス・`..`・シンボリックリンク・暗号化エントリは展開しない
    - ヘッダの申告サイズを信用せず、実際に書き出したバイト数で上限を判定する
    - 圧縮率が異常に高いエントリ（ZIP爆弾）は中断する
    """
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    extracted: list[ExtractedMember] = []
    skipped: list[str] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        file_infos = [i for i in infos if not i.is_dir()]
        if len(file_infos) > limits.max_files:
            raise SafetyError(f"ZIP内のファイル数が上限を超えています: {len(file_infos)} > {limits.max_files}")
        declared = sum(i.file_size for i in file_infos)
        if declared > limits.max_total_bytes:
            raise SafetyError(f"ZIPの展開後サイズが上限を超えています（申告値 {declared / 1e6:.0f}MB）")
        for info in file_infos:
            name = decode_zip_name(info)
            try:
                rel = safe_member_path(name)
            except SafetyError as e:
                skipped.append(str(e))
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                skipped.append(f"シンボリックリンクは展開しません: {name}")
                continue
            if info.flag_bits & 0x1:
                skipped.append(f"暗号化されたエントリは展開しません: {name}")
                continue
            target = (dest_root / rel).resolve()
            if dest_root not in target.parents:
                skipped.append(f"展開先外へのパス: {name}")
                continue
            if min_free_mb:
                ensure_free_space(dest_root, info.file_size, min_free_mb)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            comp = max(info.compress_size, 1)
            try:
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        total += len(chunk)
                        if total > limits.max_total_bytes:
                            raise SafetyError("ZIP展開中に合計サイズ上限を超えました（ZIP爆弾の可能性）")
                        if written > 10 * CHUNK and written / comp > limits.max_ratio:
                            raise SafetyError(
                                f"圧縮率が異常に高いエントリ（{written / comp:.0f}倍）: {name}（ZIP爆弾の可能性）")
                        out.write(chunk)
            except SafetyError:
                target.unlink(missing_ok=True)
                raise
            extracted.append(ExtractedMember(name=str(rel), path=target, size=written))
    return extracted, skipped


def sanitize_filename(s: str, maxlen: int = 150) -> str:
    bad = '<>:"/\\|?*\x00'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in s)
    return out[:maxlen] or "_"
