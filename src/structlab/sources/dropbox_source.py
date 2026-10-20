"""Dropbox API ソース（読み取り専用）と、結果保存用の書き込み先（出力フォルダ限定）。

認証情報は環境変数からのみ読む:
  DROPBOX_APP_KEY + DROPBOX_REFRESH_TOKEN (+ 任意 DROPBOX_APP_SECRET)  … 推奨（自動更新）
  DROPBOX_ACCESS_TOKEN                                               … 短期トークン（4時間程度で失効）
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from ..models import RemoteFile
from ..safety import CHUNK, DropboxHasher, ensure_free_space
from .base import Source, SourceChanged, SourceError

T = TypeVar("T")


def make_client():
    try:
        import dropbox
    except ImportError as e:  # pragma: no cover
        raise SourceError("dropbox ライブラリがありません: pip install dropbox") from e
    app_key = os.environ.get("DROPBOX_APP_KEY")
    refresh = os.environ.get("DROPBOX_REFRESH_TOKEN")
    secret = os.environ.get("DROPBOX_APP_SECRET") or None
    access = os.environ.get("DROPBOX_ACCESS_TOKEN")
    if app_key and refresh:
        return dropbox.Dropbox(oauth2_refresh_token=refresh, app_key=app_key, app_secret=secret,
                               timeout=300)
    if access:
        return dropbox.Dropbox(oauth2_access_token=access, timeout=300)
    raise SourceError(
        "Dropbox の認証情報がありません。環境変数 DROPBOX_APP_KEY と DROPBOX_REFRESH_TOKEN を設定してください"
        "（docs/SETUP_DROPBOX.md 参照）"
    )


def _with_retry(fn: Callable[[], T], what: str, attempts: int = 5) -> T:
    import dropbox
    from requests.exceptions import RequestException

    delay = 2.0
    for i in range(attempts):
        try:
            return fn()
        except dropbox.exceptions.AuthError as e:
            raise SourceError(f"Dropbox 認証エラー（トークン・権限を確認）: {e}") from e
        except dropbox.exceptions.RateLimitError as e:
            wait = getattr(e, "backoff", None) or delay
            time.sleep(wait)
        except (dropbox.exceptions.InternalServerError, RequestException) as e:
            if i == attempts - 1:
                raise SourceError(f"{what} に失敗しました（通信エラー）: {e}") from e
            time.sleep(delay)
        delay *= 2
    raise SourceError(f"{what} に失敗しました（再試行上限）")


def _norm_root(p: str) -> str:
    p = (p or "").strip()
    if p in ("", "/"):
        return ""
    return "/" + p.strip("/")


class DropboxSource(Source):
    name = "dropbox"

    def __init__(self, root: str, client=None):
        self.root = _norm_root(root)
        self.dbx = client or make_client()

    def _rel(self, path_display: str) -> str:
        if not self.root:
            return path_display.lstrip("/")
        if path_display.lower().startswith(self.root.lower() + "/"):
            return path_display[len(self.root) + 1:]
        return path_display.lstrip("/")

    def iter_files(self) -> Iterator[RemoteFile]:
        import dropbox

        res = _with_retry(lambda: self.dbx.files_list_folder(self.root, recursive=True, limit=2000,
                                                             include_non_downloadable_files=False),
                          "フォルダ一覧の取得")
        while True:
            for e in res.entries:
                if isinstance(e, dropbox.files.FileMetadata):
                    if getattr(e, "is_downloadable", True) is False:
                        continue
                    yield RemoteFile(
                        path=self._rel(e.path_display), size=e.size,
                        server_modified=e.server_modified.timestamp(),
                        content_hash=e.content_hash or "", rev=e.rev, remote_id=e.id,
                    )
            if not res.has_more:
                break
            cursor = res.cursor
            res = _with_retry(lambda: self.dbx.files_list_folder_continue(cursor), "フォルダ一覧の続き取得")

    def fetch(self, rf: RemoteFile, dest: Path, min_free_mb: int = 0) -> Path:
        """ストリーミングでダウンロードし、Dropbox content_hash を照合する。
        取得中に別リビジョンへ更新されても rev 指定で取得するので混ざらない。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size == rf.size:
            from ..safety import dropbox_content_hash
            if dropbox_content_hash(dest) == rf.content_hash:
                return dest  # 前回の途中停止で残ったキャッシュを再利用
        ensure_free_space(dest.parent, rf.size, min_free_mb)
        part = dest.with_name(dest.name + ".part")
        target = f"rev:{rf.rev}" if rf.rev else (rf.remote_id or f"{self.root}/{rf.path}")

        def _download() -> str:
            md, resp = self.dbx.files_download(target)
            hasher = DropboxHasher()
            try:
                with open(part, "wb") as out:
                    for chunk in resp.iter_content(chunk_size=CHUNK):
                        if chunk:
                            hasher.update(chunk)
                            out.write(chunk)
            finally:
                resp.close()
            return hasher.hexdigest()

        got = _with_retry(_download, f"ダウンロード {rf.path}")
        if rf.content_hash and got != rf.content_hash:
            part.unlink(missing_ok=True)
            raise SourceChanged(f"ダウンロード内容のハッシュが一致しません（更新中の可能性）: {rf.path}")
        part.replace(dest)
        return dest


class DropboxOutput:
    """解析結果・状態DBの保存先。指定した出力フォルダの外には書き込まない。"""

    def __init__(self, out_root: str, source_root: str, client=None):
        self.out_root = _norm_root(out_root)
        src = _norm_root(source_root)
        if not self.out_root:
            raise SourceError("output.dropbox_path が空です")
        o, s = self.out_root.lower() + "/", (src.lower() + "/") if src else "/"
        if o.startswith(s) or s.startswith(o):
            raise SourceError("出力フォルダが解析対象フォルダと重なっています（元資料保護のため中止）")
        self.dbx = client or make_client()

    def _target(self, rel: str) -> str:
        rel = rel.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            raise SourceError(f"不正な出力パス: {rel}")
        return f"{self.out_root}/{rel}"

    def upload(self, local: Path, rel: str) -> None:
        import dropbox

        target = self._target(rel)
        size = local.stat().st_size
        mode = dropbox.files.WriteMode.overwrite
        if size <= 100 * 1024 * 1024:
            data = local.read_bytes()
            _with_retry(lambda: self.dbx.files_upload(data, target, mode=mode, mute=True), f"アップロード {rel}")
            return
        with open(local, "rb") as f:  # 大容量はセッションで分割アップロード
            first = f.read(8 * CHUNK)
            sess = _with_retry(lambda: self.dbx.files_upload_session_start(first), "アップロード開始")
            cursor = dropbox.files.UploadSessionCursor(session_id=sess.session_id, offset=len(first))
            while True:
                chunk = f.read(8 * CHUNK)
                if not chunk:
                    break
                _with_retry(lambda: self.dbx.files_upload_session_append_v2(chunk, cursor), "アップロード継続")
                cursor.offset += len(chunk)
            commit = dropbox.files.CommitInfo(path=target, mode=mode, mute=True)
            _with_retry(lambda: self.dbx.files_upload_session_finish(b"", cursor, commit), "アップロード完了")

    def download_if_exists(self, rel: str, local: Path) -> bool:
        import dropbox

        target = self._target(rel)
        try:
            self.dbx.files_get_metadata(target)
        except dropbox.exceptions.ApiError:
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        _with_retry(lambda: self.dbx.files_download_to_file(str(local), target), f"取得 {rel}")
        return True
