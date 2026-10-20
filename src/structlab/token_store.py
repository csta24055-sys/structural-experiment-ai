"""GitHub Actions で Dropbox の認可コードをリフレッシュトークンに交換し、GitHub Secrets に登録する。

  python -m structlab.token_store preflight   … Secrets を書き込めるか事前確認（試験用の Secret を作って消す）
  python -m structlab.token_store exchange    … Secret DROPBOX_AUTH_CODE を交換 →
                                                 DROPBOX_REFRESH_TOKEN に登録 → DROPBOX_AUTH_CODE を削除

GITHUB_TOKEN（Actions の自動トークン）は Secrets を書き込めないため、
このリポジトリの「Secrets: 読み書き」だけを許可した fine-grained PAT（Secret GH_SECRETS_TOKEN）を使う。
値は GitHub の公開鍵で暗号化（libsodium sealed box）して送り、平文はチャット・コード・コミット・ログに出さない
（ログには ::add-mask:: で伏せ字指定もする）。コードは数分で失効するので、依存ライブラリの準備より先に
標準ライブラリだけで交換できるようにしてある（暗号化に使う PyNaCl は交換の後でよい）。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
GITHUB_API = "https://api.github.com"
SECRET_NAME = "DROPBOX_REFRESH_TOKEN"
CODE_SECRET = "DROPBOX_AUTH_CODE"
TEST_SECRET = "STRUCTLAB_SECRET_WRITE_TEST"


class TokenError(Exception):
    pass


class _Resp:
    def __init__(self, status: int, body: bytes):
        self.status_code, self.body = status, body

    def json(self):
        return json.loads(self.body or b"{}")


class UrllibHttp:
    """requests が無い段階でも使える最小の HTTP（標準ライブラリのみ）。"""

    def request(self, method: str, url: str, *, data: dict | None = None, json_body=None, headers=None) -> _Resp:
        hdrs = dict(headers or {})
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return _Resp(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Resp(e.code, e.read())


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip().strip('"').strip("'").strip()


def mask(value: str) -> None:
    """GitHub Actions のログで値を伏せ字にする（この行自体はログに表示されない）。"""
    if os.environ.get("GITHUB_ACTIONS") == "true" and value:
        print(f"::add-mask::{value}", flush=True)


def token_file() -> Path:
    return Path(os.environ.get("DROPBOX_REFRESH_TOKEN_FILE") or "~/.config/structlab/dropbox_refresh_token").expanduser()


def write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.chmod(path, 0o600)


# ---------------- Dropbox ----------------
def exchange_code(code: str, app_key: str, app_secret: str, http=None) -> dict:
    """認可コード → リフレッシュトークン。戻り値の refresh_token は表示しないこと。"""
    http = http or UrllibHttp()
    code = (code or "").strip().strip('"').strip("'").strip()
    if not code:
        raise TokenError(f"認可コードがありません（Secret {CODE_SECRET}）")
    r = http.request("POST", TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                              "client_id": app_key, "client_secret": app_secret})
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


# ---------------- GitHub Secrets ----------------
class SecretsWriter:
    def __init__(self, token: str, repo: str, http=None):
        if not token:
            raise TokenError("Secret GH_SECRETS_TOKEN がありません（このリポジトリの Secrets 読み書きだけを許可した PAT）")
        if not repo or "/" not in repo:
            raise TokenError("リポジトリ名（GITHUB_REPOSITORY）が分かりません")
        self.repo, self.http = repo, http or UrllibHttp()
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "structlab"}

    def _call(self, method: str, path: str, body=None) -> _Resp:
        return self.http.request(method, f"{GITHUB_API}/repos/{self.repo}/actions/secrets{path}",
                                 json_body=body, headers=self.headers)

    def _fail(self, what: str, r: _Resp) -> TokenError:
        hint = {401: "PAT が無効・期限切れ", 403: "PAT に Secrets の読み書き権限が無い、または対象リポジトリが違う",
                404: "PAT の対象リポジトリにこのリポジトリが入っていない（または Secrets 権限なし）"}.get(r.status_code, "")
        return TokenError(f"{what}に失敗: HTTP {r.status_code}" + (f" → {hint}" if hint else ""))

    def public_key(self) -> tuple[str, str]:
        r = self._call("GET", "/public-key")
        if r.status_code != 200:
            raise self._fail("公開鍵の取得", r)
        j = r.json()
        return j["key_id"], j["key"]

    def put(self, name: str, value: str) -> str:
        from nacl.encoding import Base64Encoder
        from nacl.public import PublicKey, SealedBox
        key_id, key = self.public_key()
        enc = SealedBox(PublicKey(key.encode(), Base64Encoder)).encrypt(value.encode())
        r = self._call("PUT", f"/{name}", {"encrypted_value": base64.b64encode(enc).decode(), "key_id": key_id})
        if r.status_code not in (201, 204):
            raise self._fail(f"Secret {name} の登録", r)
        return "created" if r.status_code == 201 else "updated"

    def delete(self, name: str) -> bool:
        r = self._call("DELETE", f"/{name}")
        if r.status_code not in (204, 404):
            raise self._fail(f"Secret {name} の削除", r)
        return r.status_code == 204

    def exists(self, name: str) -> bool:
        r = self._call("GET", f"/{name}")
        if r.status_code not in (200, 404):
            raise self._fail(f"Secret {name} の確認", r)
        return r.status_code == 200


def _writer(http=None) -> SecretsWriter:
    return SecretsWriter(_env("GH_SECRETS_TOKEN"), os.environ.get("GITHUB_REPOSITORY", ""), http=http)


def cmd_preflight(http=None, out=print) -> bool:
    """本人認証の前に、交換と登録に必要なものがそろっているかを確かめる（Dropbox のコードは使わない）。"""
    ok = True
    for n in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "GH_SECRETS_TOKEN"):
        has = bool(_env(n))
        ok &= has
        out(f"[{'OK ' if has else 'NG '}] Secret {n}: {'あり' if has else 'なし'}")
    if not ok:
        return False
    try:
        w = _writer(http)
        w.public_key()
        out("[OK ] PAT でこのリポジトリの Secrets の公開鍵を取得できた")
        w.put(TEST_SECRET, "structlab-write-test")
        w.delete(TEST_SECRET)
        out(f"[OK ] PAT で Secret を書き込み・削除できた（試験用 {TEST_SECRET} を作って消した）")
        out(f"-- 既存の {SECRET_NAME}: {'あり（交換後に上書き）' if w.exists(SECRET_NAME) else 'なし'}")
    except TokenError as e:
        out(f"[NG ] {e}")
        return False
    except ImportError:
        out("[NG ] PyNaCl がありません（pip install pynacl）")
        return False
    return True


def cmd_exchange(http=None, out=print) -> dict:
    """DROPBOX_AUTH_CODE を交換し、権限600の一時ファイルに保存する（次の store で Secrets に登録）。"""
    key, secret = _env("DROPBOX_APP_KEY"), _env("DROPBOX_APP_SECRET")
    if not (key and secret):
        raise TokenError("DROPBOX_APP_KEY と DROPBOX_APP_SECRET が必要です")
    j = exchange_code(os.environ.get(CODE_SECRET, ""), key, secret, http=http)
    write_private(token_file(), j["refresh_token"])
    out("[OK ] 認可コードをリフレッシュトークンに交換した（値は表示しません）。許可された権限: " + (j.get("scope") or "不明"))
    if "files.content.write" in (j.get("scope") or ""):
        out("-- 書き込み権限も含まれています（読み取り専用で始める場合は許可URLの scope を確認）")
    return {"scope": j.get("scope", "")}


def cmd_store(http=None, out=print) -> str:
    """一時ファイルのトークンを Secret DROPBOX_REFRESH_TOKEN に登録し、使い終わった DROPBOX_AUTH_CODE を削除する。"""
    p = token_file()
    if not p.is_file():
        raise TokenError("交換済みのトークンがありません（exchange が失敗しています）")
    token = p.read_text().strip()
    mask(token)
    w = _writer(http)
    res = w.put(SECRET_NAME, token)
    out(f"[OK ] Secret {SECRET_NAME} を{'登録' if res == 'created' else '更新'}した（GitHub の公開鍵で暗号化して送信）")
    if w.delete(CODE_SECRET):
        out(f"[OK ] 使い終わった Secret {CODE_SECRET} を削除した")
    return res


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    step = argv[0] if argv else ""
    try:
        if step == "preflight":
            return 0 if cmd_preflight() else 1
        if step == "exchange":
            cmd_exchange()
            return 0
        if step == "store":
            cmd_store()
            return 0
    except TokenError as e:
        print(f"[NG ] {e}")
        return 1
    print("使い方: python -m structlab.token_store preflight|exchange|store")
    return 2


if __name__ == "__main__":
    sys.exit(main())
