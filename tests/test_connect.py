"""接続確認・認証情報の扱い・ワークフロー（いずれも通信なしの模擬で確認）。秘密の値が出力に出ないことも確認する。"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from types import SimpleNamespace

import dropbox
import pytest
import yaml

from structlab import cli
from structlab.connect import run_connect_check
from structlab.safety import dropbox_content_hash
from structlab.sources import dropbox_source
from structlab.sources.dropbox_source import dropbox_credentials, make_client, redact

from conftest import make_cfg

ROOT = Path(__file__).resolve().parents[1]
SECRETS = {"DROPBOX_APP_KEY": "appkey-AAAA1111", "DROPBOX_APP_SECRET": "appsecret-BBBB2222",
           "DROPBOX_REFRESH_TOKEN": "refresh-CCCC3333", "ANTHROPIC_API_KEY": "sk-ant-DDDD4444"}


@pytest.fixture(autouse=True)
def _isolated_creds(tmp_path, monkeypatch):
    monkeypatch.setattr(dropbox_source, "CRED_DIR", tmp_path / "creds")
    for n in list(SECRETS) + ["DROPBOX_ACCESS_TOKEN", "DROPBOX_REFRESH_TOKEN_FILE", "DROPBOX_APP_KEY_FILE"]:
        monkeypatch.delenv(n, raising=False)
    for k in [k for k in os.environ if k.startswith("SECRET_SET_")]:
        monkeypatch.delenv(k)


# ---------- 認証情報 ----------
def test_credentials_from_env_and_file(tmp_path, monkeypatch):
    assert dropbox_credentials()["usable"] is False
    d = tmp_path / "creds"
    d.mkdir()
    (d / "dropbox_refresh_token").write_text("file-token-xyz\n")
    (d / "dropbox_app_key").write_text("file-key-xyz")
    c = dropbox_credentials()
    assert c["usable"] and c["refresh_token"] == "file-token-xyz" and c["sources"]["refresh_token"] == "file"
    monkeypatch.setenv("DROPBOX_REFRESH_TOKEN", "env-token-xyz")
    assert dropbox_credentials()["refresh_token"] == "env-token-xyz"  # 環境変数が優先

    seen = {}
    monkeypatch.setattr(dropbox, "Dropbox", lambda **kw: seen.update(kw) or "client")
    assert make_client() == "client"
    assert seen["oauth2_refresh_token"] == "env-token-xyz" and seen["app_key"] == "file-key-xyz"
    assert seen["app_secret"] is None


def test_redact_hides_all_secret_values(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    s = redact("error " + " ".join(SECRETS.values()))
    assert not any(v in s for v in SECRETS.values()) and s.count("***") == 4


# ---------- 接続確認（模擬 Dropbox / Claude） ----------
def _fm(rel, local: Path):
    path = "/鋼材実験レポート用/" + rel
    return dropbox.files.FileMetadata(
        name=local.name, id="id:" + rel, path_display=path, path_lower=path.lower(), size=local.stat().st_size,
        content_hash=dropbox_content_hash(local), rev="0123456789abc",
        server_modified=dt.datetime(2026, 10, 1), client_modified=dt.datetime(2026, 10, 1))


class FakeDbx:
    def __init__(self, src: Path, *, missing=False):
        self.src, self.missing = src, missing
        self.by_rev, self.calls = {}, []
        self.entries = []
        for p in sorted(src.rglob("*")):
            if p.is_file():
                rel = p.relative_to(src).as_posix()
                fm = _fm(rel, p)
                fm.rev = f"{len(self.entries):09x}abc"
                self.by_rev[f"rev:{fm.rev}"] = p
                self.entries.append(fm)
        self.entries.append(_fm("作業中/~$lock.xlsx", src / "第1回/測定データ.xlsx"))

    def files_get_metadata(self, path):
        self.calls.append(("meta", path))
        if self.missing:
            raise dropbox.exceptions.ApiError("r", dropbox.files.GetMetadataError.path(
                dropbox.files.LookupError.not_found), "not found", "")
        return dropbox.files.FolderMetadata(name="x", id="id:root", path_display=path, path_lower=path.lower())

    def files_list_folder(self, path, recursive, limit, include_non_downloadable_files=False):
        self.calls.append(("list", path))
        if path == "":
            return SimpleNamespace(entries=[dropbox.files.FolderMetadata(name="鋼材実験レポート", id="id:a",
                                                                        path_display="/鋼材実験レポート",
                                                                        path_lower="/鋼材実験レポート")],
                                   has_more=False)
        return SimpleNamespace(entries=self.entries, has_more=False, cursor="c")

    def files_download(self, target):
        self.calls.append(("download", target))
        data = self.by_rev[target].read_bytes()
        return None, SimpleNamespace(iter_content=lambda chunk_size: iter([data]), close=lambda: None)

    def __getattr__(self, name):  # 書き込み系の API は存在しない（呼ばれたら失敗させる）
        raise AssertionError(f"呼んではいけない API: {name}")


class FakeModels:
    def __init__(self, fail=None):
        self.fail = fail

    def list(self, limit):
        if self.fail:
            raise self.fail
        return SimpleNamespace(data=[SimpleNamespace(id="claude-opus-5-5"), SimpleNamespace(id="claude-haiku-5-5")])

    def retrieve(self, model):
        return SimpleNamespace(id=model)


def _cfg(tmp_path, src):
    return make_cfg(tmp_path, src, source={"type": "dropbox", "path": "/鋼材実験レポート用"},
                    stability={"min_age_seconds": 600, "min_stable_seconds": 600},
                    ai={"provider": "anthropic", "model": "claude-opus-5-5"}, limits={"min_free_disk_mb": 0})


@pytest.mark.slow
def test_connect_check_lists_downloads_and_never_prints_secrets(tmp_path, src_dir, monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SECRET_SET_CLAUDE_API_KEY", "true")
    lines: list[str] = []
    summary = tmp_path / "summary.md"
    fake = FakeDbx(src_dir)
    res = run_connect_check(_cfg(tmp_path, src_dir), client=fake, out=lines.append, summary_path=str(summary),
                            ai_client=SimpleNamespace(models=FakeModels()))
    text = "\n".join(lines)
    assert res["ok"], text
    assert res["dropbox"]["files"] == len(fake.entries) and res["dropbox"]["ignored"] == 1
    assert res["dropbox"]["kinds"]["pdf"] == 2 and res["dropbox"]["kinds"]["excel"] == 1
    assert res["scan"]["waiting_upload"] + res["scan"]["ready"] == len(fake.entries) - 1
    for kind in ("pdf", "excel"):
        assert res["samples"][kind]["units"] >= 1
    if res["samples"]["video"] not in ("none",):
        assert res["samples"]["video"] == "error" or res["samples"]["video"]["units"] >= 1
    assert res["claude"]["status"] == "ok" and res["claude"]["model_available"]
    assert "CLAUDE_API_KEY=あり" in text and "DROPBOX_REFRESH_TOKEN: あり（環境変数）" in text
    full = text + summary.read_text(encoding="utf-8")
    assert not any(v in full for v in SECRETS.values())
    assert {c[0] for c in fake.calls} <= {"meta", "list", "download"}
    assert not list(Path(os.environ.get("TMPDIR", "/tmp")).glob("structlab_check_*/dl/*"))


def test_connect_check_folder_missing_and_bad_key(tmp_path, src_dir):
    class AuthenticationError(Exception):  # anthropic.AuthenticationError と同じ名前で判定される
        pass

    lines: list[str] = []
    err = AuthenticationError("invalid x-api-key")
    res = run_connect_check(_cfg(tmp_path, src_dir), client=FakeDbx(src_dir, missing=True), out=lines.append,
                            ai_client=SimpleNamespace(models=FakeModels(fail=err)))
    text = "\n".join(lines)
    assert not res["ok"] and res["dropbox"]["folder_found"] is False
    assert "似た名前: 鋼材実験レポート" in text and "キーが無効" in text


def test_connect_check_without_credentials(tmp_path, src_dir):
    lines: list[str] = []
    res = run_connect_check(_cfg(tmp_path, src_dir), out=lines.append)
    text = "\n".join(lines)
    assert not res["ok"] and "認証情報がありません" in text and res["claude"]["status"] == "missing"


# ---------- dropbox-auth（PKCE）: トークンを表示しない ----------
def test_dropbox_auth_never_prints_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DROPBOX_APP_KEY", "appkey-AAAA1111")
    created = []

    class Flow:
        def __init__(self, key, use_pkce, token_access_type, scope):
            assert use_pkce and token_access_type == "offline"
            created.append(scope)
            self.code_verifier, self.code_challenge = "v", "c"

        def start(self):
            return "https://www.dropbox.com/oauth2/authorize?x"

        def finish(self, code):
            return SimpleNamespace(refresh_token="refresh-SECRET-9999")

    monkeypatch.setattr(dropbox, "DropboxOAuth2FlowNoRedirect", Flow)
    monkeypatch.setattr("structlab.connect.run_connect_check", lambda *a, **k: {"ok": True})
    assert cli.main(["-c", str(ROOT / "configs/dropbox.yaml"), "dropbox-auth", "start"]) == 0
    assert created[0] == ["files.metadata.read", "files.content.read"]  # 既定は読み取り専用
    assert cli.main(["-c", str(ROOT / "configs/dropbox.yaml"), "dropbox-auth", "finish", "code123"]) == 0
    out = capsys.readouterr().out
    tok = tmp_path / "creds" / "dropbox_refresh_token"
    assert tok.read_text() == "refresh-SECRET-9999" and oct(tok.stat().st_mode & 0o777) == "0o600"
    assert "refresh-SECRET-9999" not in out and "appkey-AAAA1111" not in out and "cat " not in out
    assert not (tmp_path / "creds" / "pkce.json").exists()


# ---------- ワークフロー ----------
def _wf(name):
    d = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
    return d, d.get("on", d.get(True))


def test_connection_check_workflow_is_readonly_and_masks():
    d, on = _wf("connection-check.yml")
    assert on["push"]["paths"] == ["ops/requests/connection-check.txt"] and "workflow_dispatch" in on
    assert d["permissions"] == {"contents": "read"}
    job = d["jobs"]["check"]
    env = job["env"]
    for n in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"):
        assert env[n] == f"${{{{ secrets.{n} }}}}"
    assert env["ANTHROPIC_API_KEY"].startswith("${{ secrets.ANTHROPIC_API_KEY ||")
    assert all(v.endswith("!= '' }}") for k, v in env.items() if k.startswith("SECRET_SET_"))
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "connect-check" in runs and "secrets." not in runs and "echo $" not in runs
    assert "upload-artifact" not in (ROOT / ".github/workflows/connection-check.yml").read_text()


def test_analyze_workflow_ai_needs_explicit_request():
    d, on = _wf("analyze.yml")
    assert on["push"]["paths"] == ["ops/requests/analyze.txt"]
    assert on["workflow_dispatch"]["inputs"]["allow_external_ai"]["default"] is False
    run = d["jobs"]["analyze"]["steps"][-1]["run"]
    assert 'if [ "$AI" = "true" ]' in run and "allow_external_ai:" in run


# ---------- OAuth 診断（通信なし） ----------
class FakeHttp:
    def __init__(self, *, client_ok=True, refresh_ok_with_secret=False, refresh_ok_pkce=False, authorize=200):
        self.client_ok, self.rs, self.rp, self.authorize = client_ok, refresh_ok_with_secret, refresh_ok_pkce, authorize
        self.posts = []

    def post(self, url, data, timeout):
        self.posts.append(dict(data))
        if data["grant_type"] == "authorization_code":
            j = {"error": "invalid_grant"} if self.client_ok else {"error": "invalid_client"}
            return SimpleNamespace(status_code=400, json=lambda: j)
        ok = self.rs if "client_secret" in data else self.rp
        j = {"access_token": "sl.ACCESS-5555", "scope": "files.content.read files.metadata.read"} if ok else \
            {"error": "invalid_grant", "error_description": "refresh token is malformed"}
        return SimpleNamespace(status_code=200 if ok else 400, json=lambda: j)

    def get(self, url, params, timeout, allow_redirects):
        return SimpleNamespace(status_code=self.authorize, text="")


def test_oauth_diagnosis_pinpoints_cause(monkeypatch):
    from structlab.connect import diagnose_oauth
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v + "\n")  # 貼り付け時の改行
    cred = dropbox_credentials()
    assert cred["refresh_token"] == SECRETS["DROPBOX_REFRESH_TOKEN"] and "DROPBOX_APP_SECRET" in cred["padded"]
    lines: list[str] = []
    out = diagnose_oauth(cred, lines.append, http=FakeHttp(refresh_ok_pkce=True))
    assert out == {"app_credentials": "ok", "authorize_url": "accepted", "refresh": "App secret なし（PKCE）"}
    lines2: list[str] = []
    out = diagnose_oauth(cred, lines2.append, http=FakeHttp(client_ok=False, authorize=400))
    assert out["app_credentials"] == "invalid" and out["authorize_url"] == "rejected" and out["refresh"] == "failed"
    text = "\n".join(lines + lines2)
    assert not any(v in text for v in SECRETS.values()) and "sl.ACCESS-5555" not in text
