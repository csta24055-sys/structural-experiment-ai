"""Dropbox API ソース（読み取り専用）と、結果保存用の書き込み先（出力フォルダ限定）。

認証情報は環境変数（GitHub Actions では Secrets から渡す）か、権限600のファイルからだけ読む。値は表示しない:
  DROPBOX_APP_KEY + DROPBOX_REFRESH_TOKEN (+ DROPBOX_APP_SECRET)  … 推奨（自動更新。PKCE で取得した場合は secret 不要）
  DROPBOX_ACCESS_TOKEN                                           … 短期トークン（4時間程度で失効）
  ~/.config/structlab/dropbox_refresh_token, dropbox_app_key      … `structlab dropbox-auth` が保存するファイル
    （場所は DROPBOX_REFRESH_TOKEN_FILE / DROPBOX_APP_KEY_FILE で変更可。環境変数の方が優先）
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

CRED_DIR = Path("~/.config/structlab")
#: 読み取り専用で始めるためのスコープ（解析結果を出力フォルダへ保存するには files.content.write を後で追加する）
READ_SCOPES = ["files.metadata.read", "files.content.read"]
SECRET_ENV_NAMES = ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "DROPBOX_ACCESS_TOKEN",
                    "ANTHROPIC_API_KEY")


def _from_file(env_name: str, default_name: str) -> str | None:
    p = Path(os.environ.get(env_name) or (CRED_DIR / default_name)).expanduser()
    try:
        return p.read_text(encoding="utf-8").strip() or None if p.is_file() else None
    except OSError:
        return None


def _env(name: str, padded: list[str]) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").strip()
    if v and v != raw:
        padded.append(name)  # 貼り付け時に入った空白・改行・引用符（自動で除去）
    return v or None


def dropbox_credentials() -> dict:
    """使える認証情報と、その出どころ（env / file）。値そのものは呼び出し側で表示しないこと。"""
    padded: list[str] = []
    app_key = _env("DROPBOX_APP_KEY", padded)
    refresh = _env("DROPBOX_REFRESH_TOKEN", padded)
    src = {"app_key": "env" if app_key else None, "refresh_token": "env" if refresh else None}
    if not refresh:
        refresh = _from_file("DROPBOX_REFRESH_TOKEN_FILE", "dropbox_refresh_token")
        src["refresh_token"] = "file" if refresh else None
    if not app_key:
        app_key = _from_file("DROPBOX_APP_KEY_FILE", "dropbox_app_key")
        src["app_key"] = "file" if app_key else None
    secret = _env("DROPBOX_APP_SECRET", padded)
    access = _env("DROPBOX_ACCESS_TOKEN", padded)
    src["app_secret"] = "env" if secret else None
    src["access_token"] = "env" if access else None
    return {"app_key": app_key, "refresh_token": refresh, "app_secret": secret, "access_token": access,
            "sources": src, "usable": bool((app_key and refresh) or access), "padded": padded}


def secret_values() -> list[str]:
    """ログ・画面に出してはいけない値（伏せ字にするため）。"""
    vals = [os.environ.get(n) for n in SECRET_ENV_NAMES]
    c = dropbox_credentials()
    vals += [c["refresh_token"], c["app_key"]]
    return sorted({v for v in vals if v and len(v) >= 6}, key=len, reverse=True)


def redact(text: str) -> str:
    for v in secret_values():
        text = text.replace(v, "***")
    return text


def make_client():
    try:
        import dropbox
    except ImportError as e:  # pragma: no cover
        raise SourceError("dropbox ライブラリがありません: pip install dropbox") from e
    c = dropbox_credentials()
    if c["app_key"] and c["refresh_token"]:
        return dropbox.Dropbox(oauth2_refresh_token=c["refresh_token"], app_key=c["app_key"],
                               app_secret=c["app_secret"], timeout=300)
    if c["access_token"]:
        return dropbox.Dropbox(oauth2_access_token=c["access_token"], timeout=300)
    raise SourceError(
        "Dropbox の認証情報がありません。環境変数 DROPBOX_APP_KEY と DROPBOX_REFRESH_TOKEN を設定してください"
        "（GitHub Actions では Secrets。docs/SETUP_DROPBOX.md 参照）"
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
