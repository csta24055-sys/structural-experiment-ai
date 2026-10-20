"""GitHub Actions で Dropbox の認可コードをリフレッシュトークンに交換する（ショートカット不要）。

交換したトークンはその実行の中だけで使い（権限600の一時ファイル）、終了とともに消える。
平文はチャット・コード・コミット・ログのいずれにも出さない（ログには ::add-mask:: で伏せ字指定もする）。
"""
from __future__ import annotations

import os
from pathlib import Path

from .sources.dropbox_source import CRED_DIR, dropbox_credentials

TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"


class TokenError(Exception):
    pass


def mask(value: str) -> None:
    """GitHub Actions のログで値を伏せ字にする（この行自体はログに表示されない）。"""
    if os.environ.get("GITHUB_ACTIONS") == "true" and value:
        print(f"::add-mask::{value}", flush=True)


def token_file() -> Path:
    return Path(os.environ.get("DROPBOX_REFRESH_TOKEN_FILE") or (CRED_DIR / "dropbox_refresh_token")).expanduser()


def write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.chmod(path, 0o600)


def exchange_code(code: str, app_key: str, app_secret: str, http=None) -> dict:
    """認可コード → リフレッシュトークン。戻り値の refresh_token は表示しないこと。"""
    import requests
    http = http or requests
    code = (code or "").strip().strip('"').strip("'").strip()
    if not code:
        raise TokenError("認可コードがありません（Secret DROPBOX_AUTH_CODE）")
    r = http.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                   "client_id": app_key, "client_secret": app_secret}, timeout=30)
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code == 200 and j.get("refresh_token"):
        mask(j["refresh_token"])
        mask(j.get("access_token", ""))
        return j
    err = j.get("error")
    tag = err.get(".tag") if isinstance(err, dict) else err
    hint = {"invalid_grant": "認可コードが期限切れ・使用済み・別のアプリのもの（許可からやり直し、すぐ登録）",
            "invalid_client": "App key / App secret が違う",
            "invalid_request": "コードの形式が違う（コピーの欠け・余分な文字）"}.get(tag, "")
    raise TokenError(f"コードの交換に失敗: HTTP {r.status_code} {tag or '?'}" + (f" → {hint}" if hint else ""))


def cmd_exchange(http=None) -> dict:
    """環境変数 DROPBOX_AUTH_CODE を交換し、この実行環境の一時ファイル（0600）に保存する。"""
    c = dropbox_credentials()
    if not (c["app_key"] and c["app_secret"]):
        raise TokenError("DROPBOX_APP_KEY と DROPBOX_APP_SECRET が必要です")
    j = exchange_code(os.environ.get("DROPBOX_AUTH_CODE", ""), c["app_key"], c["app_secret"], http=http)
    write_private(token_file(), j["refresh_token"])
    return {"scope": j.get("scope", "")}
