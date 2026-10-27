"""Box 共有リンクの検査と、許可されている場合だけの動画取得。

共有ページを開けることと、動画本体を取得・解析できることは別に扱う。状態は次の 6 つ:

  A downloaded        動画本体を取得でき、解析処理へ渡せる
  B preview_only      ページ上で再生・プレビューはできるが、ダウンロードは許可されていない
  C metadata_only     ファイル名などのメタデータだけ確認できた（再生・取得の可否は不明）
  D auth_required     ログインまたはパスワードが必要
  E inaccessible      リンク切れ・共有停止・ネットワーク制限・タイムアウトなど
  F unprocessable     取得はできたが壊れている・形式に対応していない

確認できない項目は None のまま残し、推測で埋めない。
ダウンロード禁止の設定・認証・DRM を回避する処理（再生用通信の横取りなど）は実装しない。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .links import classify_url, is_box_host

STATES = {
    "A": "downloaded", "B": "preview_only", "C": "metadata_only",
    "D": "auth_required", "E": "inaccessible", "F": "unprocessable",
}
STATE_LABEL = {
    "A": "動画本体を取得済み", "B": "再生のみ可（ダウンロード不可）", "C": "メタデータのみ確認",
    "D": "認証が必要", "E": "アクセス不可", "F": "取得したが解析不可",
}
VIDEO_EXT = {"mp4", "mov", "m4v", "mkv", "webm", "avi", "wmv"}
API = "https://api.box.com/2.0"
NODE_SCRIPT = Path(__file__).with_name("box_browser.js")

LOGIN_TEXT = re.compile(r"Log in to Box|Sign in to Box|Box にログイン|ログインしてください|Enter your email", re.I)
REMOVED_TEXT = re.compile(
    r"link has been removed|is unavailable to you|no longer available|リンクは削除|利用できなくなりました|"
    r"This shared file or folder link has been removed|Shared link expired|有効期限が切れ", re.I)
PASSWORD_TEXT = re.compile(r"password[- ]protected|パスワードを入力|Enter (the )?password", re.I)
DURATION = re.compile(r"/\s*(\d{1,2}:\d{2}(?::\d{2})?)")


def _json_bool(html: str, key: str) -> bool | None:
    m = re.search(rf'"{key}"\s*:\s*(true|false)', html)
    return None if m is None else m.group(1) == "true"


def _json_int(html: str, key: str) -> int | None:
    m = re.search(rf'"{key}"\s*:\s*(\d+)', html)
    return int(m.group(1)) if m else None


def parse_duration(s: str | None) -> float | None:
    if not s:
        return None
    parts = [int(x) for x in s.split(":")]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return float(sec)


def parse_page(html: str, text: str, title: str = "", final_url: str = "", has_password_input: bool = False) -> dict:
    """描画後の共有ページから、ページ自身が示している情報だけを取り出す。"""
    meta: dict = {"name": None, "item_type": None, "extension": None, "size": None, "duration_text": None,
                  "duration_sec": None, "can_download": None, "can_preview": None, "item_id": None,
                  "page_kind": "unknown", "evidence": []}
    host = (urlsplit(final_url).hostname or "") if final_url else ""
    path = urlsplit(final_url).path if final_url else ""
    if has_password_input or PASSWORD_TEXT.search(text):
        meta["page_kind"] = "password"
        meta["evidence"].append("パスワード入力欄")
        return meta
    if REMOVED_TEXT.search(text) or REMOVED_TEXT.search(title):
        meta["page_kind"] = "removed"
        meta["evidence"].append("リンク削除・期限切れの表示")
        return meta
    m = re.search(r'"typedID"\s*:\s*"([fd])_(\d+)"', html)
    if m:
        meta["item_type"] = "file" if m.group(1) == "f" else "folder"
        meta["item_id"] = m.group(2)
        meta["evidence"].append(f"typedID {m.group(1)}_{m.group(2)}")
        # typedID と同じオブジェクト内の extension / name（ページ利用者の name とは別）
        seg = html[m.start(): m.start() + 600]
        e = re.search(r'"extension"\s*:\s*"([A-Za-z0-9]+)"', seg)
        n = re.search(r'"name"\s*:\s*"((?:[^"\\]|\\.)+)"', seg)
        if e:
            meta["extension"] = e.group(1).lower()
        if n:
            meta["name"] = json.loads(f'"{n.group(1)}"')
        meta["size"] = _json_int(seg, "itemSize") or _json_int(html, "itemSize")
    if meta["name"] is None and title and "|" in title:
        cand = title.split("|")[0].strip()
        if cand and cand.lower() not in ("box", "log in", "sign in"):
            meta["name"] = cand
            meta["evidence"].append("ページタイトル")
    if meta["extension"] is None and meta["name"] and "." in meta["name"]:
        meta["extension"] = meta["name"].rsplit(".", 1)[1].lower()
    cd = [v for v in (_json_bool(html, "canDownload"), _json_bool(html, "can_download")) if v is not None]
    if cd:
        meta["can_download"] = all(cd)  # 片方でも禁止なら禁止として扱う
        meta["evidence"].append(f"can_download={meta['can_download']}")
    meta["can_preview"] = _json_bool(html, "can_preview")
    d = DURATION.search(text)
    if d and meta["extension"] in VIDEO_EXT:
        meta["duration_text"] = d.group(1)
        meta["duration_sec"] = parse_duration(d.group(1))
    login_page = "login" in path.lower() or host.startswith("account.") or (
        LOGIN_TEXT.search(text) and meta["item_id"] is None)
    if login_page:
        meta["page_kind"] = "login"
        meta["evidence"].append("ログイン画面")
    elif meta["item_id"] or meta["name"]:
        meta["page_kind"] = "item"
    return meta


# ---------------- 取得手段 ----------------
def http_probe(url: str, session=None, timeout: float = 20.0, max_redirects: int = 5) -> dict:
    """通常の HTTP 取得。リダイレクトは Box のホストの中だけ追う（認証情報は送らない）。"""
    import requests
    s = session or requests.Session()
    out = {"ok": False, "status": None, "final_url": url, "redirects": [], "error_kind": None}
    cur = url
    try:
        for _ in range(max_redirects + 1):
            r = s.get(cur, allow_redirects=False, timeout=timeout, headers={"User-Agent": "structlab-exam/1.0"})
            out["status"] = r.status_code
            if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
                nxt = urljoin(cur, r.headers.get("Location", ""))
                out["redirects"].append(nxt)
                if not is_box_host(urlsplit(nxt).hostname or ""):
                    out["error_kind"] = "redirect_outside_box"
                    out["final_url"] = nxt
                    return out
                cur = nxt
                continue
            out["final_url"] = cur
            out["ok"] = r.status_code < 400
            out["html_length"] = len(r.text)
            out["text_sample"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:200]
            if r.status_code in (401, 403):
                out["error_kind"] = "auth"
            elif r.status_code == 404 or r.status_code == 410:
                out["error_kind"] = "not_found"
            elif r.status_code >= 400:
                out["error_kind"] = "http_error"
            return out
        out["error_kind"] = "too_many_redirects"
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        out["error_kind"] = "timeout" if "Timeout" in name else "network"
        out["error"] = f"{name}: {str(e)[:200]}"
    return out


def browser_available() -> dict:
    """使えるブラウザ手段を調べる（決めつけない）。"""
    info = {"python_playwright": False, "node_playwright": False, "node": shutil.which("node")}
    try:
        import playwright.sync_api  # noqa: F401
        info["python_playwright"] = True
    except ImportError:
        pass
    if info["node"]:
        try:
            r = subprocess.run([info["node"], "-e", "require('playwright')"], capture_output=True, timeout=30)
            info["node_playwright"] = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    return info


def _browser_python(url: str, timeout_ms: int, download_dir: Path | None) -> dict:
    from playwright.sync_api import sync_playwright
    out: dict = {"ok": False, "backend": "python-playwright", "url": url}
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            page = b.new_page(accept_downloads=bool(download_dir))
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            out["http_status"] = resp.status if resp else None
            try:
                page.wait_for_function(
                    "() => ((document.body && document.body.innerText) || '').trim().length > 40"
                    " || document.querySelector('input[type=password]')", timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                out["render_timeout"] = True
            if page.query_selector("video") or re.search(r"\.(mp4|mov|m4v|mkv|webm)\b", page.title(), re.I):
                try:
                    page.wait_for_function("() => { const v = document.querySelector('video');"
                                           " return v && isFinite(v.duration) && v.duration > 0 }",
                                           timeout=min(timeout_ms, 20000))
                except Exception:  # noqa: BLE001
                    pass
            out["video_duration"] = page.evaluate(
                "() => { const v = document.querySelector('video'); return v && isFinite(v.duration) ? v.duration : null }")
            out.update(final_url=page.url, title=page.title(), html=page.content(),
                       text=page.evaluate("() => (document.body && document.body.innerText) || ''"),
                       has_password_input=page.query_selector("input[type=password]") is not None, ok=True)
            if download_dir:
                btn = page.query_selector('[data-testid=download-preview], button[aria-label="Download"]')
                if not btn:
                    out["download"] = {"ok": False, "reason": "ダウンロードボタンが無い"}
                else:
                    with page.expect_download(timeout=timeout_ms) as dl:
                        btn.click()
                    d = dl.value
                    dest = download_dir / d.suggested_filename
                    d.save_as(str(dest))
                    out["download"] = {"ok": True, "path": str(dest), "name": d.suggested_filename}
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)[:500]
            out["error_kind"] = "timeout" if "Timeout" in type(e).__name__ else "browser"
        finally:
            b.close()
    return out


def browser_probe(url: str, timeout_ms: int = 45000, download_dir: Path | None = None,
                  backend: str = "auto") -> dict:
    avail = browser_available()
    if backend in ("auto", "python") and avail["python_playwright"]:
        return _browser_python(url, timeout_ms, download_dir)
    if backend in ("auto", "node") and avail["node_playwright"]:
        cmd = [avail["node"], str(NODE_SCRIPT), url, "--timeout", str(timeout_ms)]
        if download_dir:
            cmd += ["--download", str(download_dir)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_ms / 1000 * 3 + 30)
            return json.loads(r.stdout or "{}") or {"ok": False, "error_kind": "browser", "error": r.stderr[:500]}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return {"ok": False, "backend": "node-playwright", "error_kind": "timeout", "error": str(e)[:300]}
    return {"ok": False, "backend": None, "error_kind": "browser_unavailable",
            "error": "Playwright（Python 版・Node 版とも）が使えません。"
                     "pip install playwright && python -m playwright install chromium で導入できます",
            "availability": avail}


def api_probe(url: str, token: str | None, session=None, timeout: float = 30.0) -> dict:
    """Box 公式 API（GET /shared_items、BoxApi: shared_link=...）で共有リンクの項目と権限を調べる。"""
    if not token:
        return {"ok": False, "error_kind": "no_token", "error": "BOX_ACCESS_TOKEN が設定されていません"}
    import requests
    s = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "BoxApi": f"shared_link={url}"}
    try:
        r = s.get(f"{API}/shared_items", headers=headers, timeout=timeout,
                  params={"fields": "id,type,name,size,extension,permissions,shared_link,content_duration"})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error_kind": "network", "error": type(e).__name__}
    if r.status_code != 200:
        kind = {401: "auth", 403: "forbidden", 404: "not_found"}.get(r.status_code, "http_error")
        return {"ok": False, "status": r.status_code, "error_kind": kind}
    d = r.json()
    perms = d.get("permissions") or {}
    sl_perms = (d.get("shared_link") or {}).get("permissions") or {}
    can = [v for v in (perms.get("can_download"), sl_perms.get("can_download")) if v is not None]
    return {"ok": True, "id": d.get("id"), "type": d.get("type"), "name": d.get("name"), "size": d.get("size"),
            "extension": (d.get("extension") or "").lower() or None,
            "can_download": all(can) if can else None, "can_preview": perms.get("can_preview")}


def api_download(url: str, token: str, file_id: str, dest: Path, session=None) -> Path:
    """公式 API（GET /files/{id}/content）で取得する。権限が無ければ Box 側が拒否する。"""
    import requests
    s = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "BoxApi": f"shared_link={url}"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with s.get(f"{API}/files/{file_id}/content", headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    part.replace(dest)
    return dest


# ---------------- 判定 ----------------
@dataclass
class BoxCheck:
    url: str
    state: str = "E"
    reason: str = ""
    checked_at: float = field(default_factory=time.time)
    name: str | None = None
    item_type: str | None = None
    extension: str | None = None
    size: int | None = None
    duration_sec: float | None = None
    duration_text: str | None = None
    is_video: bool | None = None
    can_download: bool | None = None
    can_preview: bool | None = None
    local_path: str | None = None
    methods: dict = field(default_factory=dict)  # http / browser / api / download の結果（本文は除く）
    alternatives: list[str] = field(default_factory=list)

    @property
    def analyzable(self) -> bool:
        return self.state == "A" and bool(self.local_path)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["state_name"] = STATES[self.state]
        d["state_label"] = STATE_LABEL[self.state]
        return d


ALTERNATIVES = [
    "教員・大学から動画ファイルの提供を受け、input フォルダ（または Dropbox）に置く",
    "Box 上でダウンロードが許可されている場合は、自分で保存した動画ファイルを置く",
    "正規に提供された字幕・文字起こし（.vtt / .srt / .txt）があれば、動画と同じ名前で置く",
    "配布スライド・課題・過去問だけでも解析は進む（この動画の発言は未解析として扱われる）",
]


def _summary(res: dict) -> dict:
    return {k: v for k, v in res.items() if k not in ("html", "text")}


def check_link(url: str, *, work_dir: Path | None = None, allow_download: bool = False,
               token: str | None = None, use_http: bool = True, use_browser: bool = True,
               browser=browser_probe, http=http_probe, api=api_probe, downloader=api_download) -> BoxCheck:
    """1 つの共有リンクを検査し、許可されていれば取得する。各手段は差し替え可能（テスト用）。"""
    rec = classify_url(url)
    chk = BoxCheck(url=rec.url)
    if rec.kind != "box_shared":
        chk.reason = f"Box の共有リンクではない（{rec.kind}{'、' + rec.problem if rec.problem else ''}）"
        return chk
    token = token if token is not None else os.environ.get("BOX_ACCESS_TOKEN")
    meta: dict = {}
    if use_http:
        h = http(rec.url)
        chk.methods["http"] = _summary(h)
    if token:
        a = api(rec.url, token)
        chk.methods["api"] = a
        if a.get("ok"):
            meta.update({k: a.get(k) for k in ("name", "size", "extension", "can_download", "can_preview")})
            meta["item_type"] = a.get("type")
            meta["item_id"] = a.get("id")
            meta["page_kind"] = "item"
    else:
        chk.methods["api"] = {"ok": False, "error_kind": "no_token"}
    if use_browser and meta.get("page_kind") != "item":
        b = browser(rec.url)
        chk.methods["browser"] = _summary(b)
        if b.get("ok"):
            pm = parse_page(b.get("html", ""), b.get("text", ""), b.get("title", ""), b.get("final_url", ""),
                            b.get("has_password_input", False))
            chk.methods["browser"]["parsed"] = {k: v for k, v in pm.items() if k != "evidence"}
            chk.methods["browser"]["evidence"] = pm["evidence"]
            if pm["duration_sec"] is None and b.get("video_duration") and pm.get("extension") in VIDEO_EXT:
                pm["duration_sec"] = round(float(b["video_duration"]), 2)
                pm["duration_text"] = time.strftime("%H:%M:%S", time.gmtime(pm["duration_sec"]))
            meta = {**pm, **{k: v for k, v in meta.items() if v is not None}}
    for k in ("name", "item_type", "extension", "size", "duration_sec", "duration_text", "can_download", "can_preview"):
        if meta.get(k) is not None:
            setattr(chk, k, meta[k])
    if chk.extension:
        chk.is_video = chk.extension in VIDEO_EXT
    kind = meta.get("page_kind")
    http_err = chk.methods.get("http", {}).get("error_kind")
    br = chk.methods.get("browser", {})
    if kind == "password":
        chk.state, chk.reason = "D", "パスワードの入力が必要"
    elif kind == "login" or http_err == "auth" or chk.methods["api"].get("error_kind") == "forbidden":
        chk.state, chk.reason = "D", "ログイン（大学アカウント等）が必要"
    elif kind == "removed" or http_err == "not_found":
        chk.state, chk.reason = "E", "リンクが削除・期限切れ・共有停止になっている"
    elif kind == "item":
        if chk.can_download is False:
            chk.state = "B" if chk.can_preview is not False else "C"
            chk.reason = "共有設定でダウンロードが許可されていない" + ("（ページ上の再生は可）" if chk.state == "B" else "")
        elif chk.can_download is True:
            chk.state, chk.reason = "C", "ダウンロード可。まだ取得していない（--allow-download で取得）"
            if allow_download and work_dir is not None:
                _download(chk, rec.url, token, meta, work_dir, browser, downloader)
        else:
            chk.state, chk.reason = "C", "ファイル名などは確認できたが、ダウンロード可否は確認できない"
    elif br.get("error_kind") == "browser_unavailable" and not http_err:
        chk.state, chk.reason = "C" if chk.methods.get("http", {}).get("ok") else "E", \
            "ブラウザが無いため共有ページの内容を確認できない（ページの応答のみ確認）"
    else:
        ek = br.get("error_kind") or http_err or "unknown"
        chk.state = "E"
        chk.reason = {"timeout": "タイムアウト", "network": "ネットワークに接続できない（制限の可能性）",
                      "redirect_outside_box": "Box 以外へ転送された", "browser": "ページの描画に失敗"}.get(
            ek, f"アクセスできない（{ek}）")
    if chk.state != "A":
        chk.alternatives = list(ALTERNATIVES)
    return chk


def _download(chk: BoxCheck, url: str, token: str | None, meta: dict, work_dir: Path, browser, downloader) -> None:
    dl_dir = work_dir / "box_downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    try:
        if token and meta.get("item_id"):
            p = downloader(url, token, str(meta["item_id"]), dl_dir / (chk.name or f"{meta['item_id']}.bin"))
            chk.methods["download"] = {"ok": True, "via": "api"}
        else:
            b = browser(url, download_dir=dl_dir)
            d = b.get("download") or {}
            chk.methods["download"] = {"via": "browser", **d}
            if not d.get("ok"):
                chk.reason = f"ダウンロード操作に失敗: {d.get('reason') or b.get('error_kind')}"
                return
            p = Path(d["path"])
    except Exception as e:  # noqa: BLE001
        chk.methods["download"] = {"ok": False, "error": type(e).__name__}
        chk.reason = f"ダウンロードに失敗（{type(e).__name__}）"
        return
    chk.local_path = str(p)
    if chk.is_video:
        try:
            from .. import media
            info = media.probe(p)
            if not info.has_video and not info.has_audio:
                raise ValueError("映像・音声が無い")
            chk.duration_sec = chk.duration_sec or round(info.duration, 2)
            chk.state, chk.reason = "A", "公式の手段でダウンロードし、映像・音声を確認した"
        except Exception as e:  # noqa: BLE001
            chk.state, chk.reason = "F", f"取得したが動画として読めない（{str(e)[:120]}）"
    else:
        chk.state, chk.reason = "A", "公式の手段でダウンロードした"
