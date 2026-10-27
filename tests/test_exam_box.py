"""Box 共有リンク: .url からの抽出・共有ページの判定（状態 A〜F）・取得の可否。

実際の Box には接続しない（ページと API の応答は模擬データ）。アクセス制限を回避する動作はテストしない。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from structlab.exam import box as B
from structlab.exam import links as L

ITEM_HTML = ('<script>Box.prefetchedData = {"item":{"typedID":"f_2540093107114","type":"file","id":2540093107114,'
             '"description":"","extension":"mp4","name":"04.mp4","itemSize":648927349,"canDownload":%s,'
             '"isDownloadBlockedByShieldAccessPolicy":false},"permissions":{"can_download":%s,"can_preview":true}},'
             '"user":{"name":"","email":""}</script>')
TEXT = "Skip to main content\n04.mp4\nSign up\nLog in\n0:00\n/\n1:24:41\n"
URL = "https://nihon-u.app.box.com/s/xwdemsrtud01kdv4w7iqrxv2ocdxea3u"


def page(can="false", text=TEXT, title="04.mp4 | Powered by Box", final=URL, **kw):
    return lambda url, **k: {"ok": True, "html": ITEM_HTML % (can, can), "text": text, "title": title,
                             "final_url": final, **kw}


def ok_http(url, **k):
    return {"ok": True, "status": 200, "error_kind": None}


# ---------------- .url からの抽出 ----------------
@pytest.mark.parametrize("enc,data", [
    ("utf-8", "[InternetShortcut]\nURL=%s?utm_source=chatgpt.com\n" % URL),
    ("cp932", "[InternetShortcut]\r\nURL=%s\r\n" % URL),
    ("utf-16", "[InternetShortcut]\r\nURL=%s\r\n" % URL),
    ("utf-8-sig", "[InternetShortcut]\nURL=%s\n" % URL),
])
def test_url_file_any_encoding(enc, data):
    recs, log = L.extract_links(data.encode(enc), "第04回.url", "dropbox:/授業動画/第04回.url")
    assert [r.kind for r in recs] == ["box_shared"]
    assert recs[0].url == URL  # 追跡用の utm_* は除く
    assert recs[0].shared_id == "xwdemsrtud01kdv4w7iqrxv2ocdxea3u"
    assert recs[0].sources == ["dropbox:/授業動画/第04回.url"]


def test_broken_url_file_is_reported():
    recs, log = L.extract_links(b"[InternetShortcut]\nIconIndex=0\n", "x.url", "local:x.url")
    assert recs == [] and "壊れて" in log["problem"]


def test_non_box_and_unsafe_links_are_not_box():
    assert L.classify_url("https://example.com/s/abc").kind == "other"
    assert L.classify_url("https://box.com.evil.example/s/abc").kind == "other"
    assert L.classify_url("https://user:pw@nihon-u.app.box.com/s/abc").kind == "invalid"
    assert L.classify_url("ftp://nihon-u.app.box.com/s/abc").kind == "invalid"
    assert L.classify_url("https://nihon-u.box.com/folder/123").kind == "box_other"
    assert L.classify_url("http://nihon-u.box.com/s/abc").url.startswith("https://")


def test_links_in_text_and_dedupe():
    text = f"第4回の動画: {URL}\n同じ: https://nihon-u.box.com/s/xwdemsrtud01kdv4w7iqrxv2ocdxea3u。\n資料 https://example.com/a"
    recs, _ = L.extract_links(text.encode(), "案内.txt", "local:案内.txt")
    recs2, _ = L.extract_links(f"URL={URL}".encode(), "b.url", "local:b.url")
    d = L.dedupe(recs + recs2)
    boxes = [r for r in d if r.kind == "box_shared"]
    assert len(boxes) == 1 and set(boxes[0].sources) == {"local:案内.txt", "local:b.url"}


# ---------------- 共有ページの判定 ----------------
def test_parse_page_reads_only_what_page_shows():
    m = B.parse_page(ITEM_HTML % ("false", "false"), TEXT, "04.mp4 | Powered by Box", URL)
    assert m["name"] == "04.mp4" and m["extension"] == "mp4" and m["size"] == 648927349
    assert m["can_download"] is False and m["can_preview"] is True
    assert m["duration_sec"] == 5081.0 and m["page_kind"] == "item"
    # ページ利用者の空の name で上書きしない
    assert B.parse_page(ITEM_HTML % ("true", "true"), "", "", URL)["name"] == "04.mp4"


def test_state_b_preview_only_is_not_reported_as_analyzed(tmp_path):
    calls = []

    def downloader(*a, **k):
        calls.append(a)
        raise AssertionError("ダウンロード禁止なのに取得しようとした")

    c = B.check_link(URL, work_dir=tmp_path, allow_download=True, token="", browser=page("false"), http=ok_http,
                     downloader=downloader)
    assert c.state == "B" and not c.analyzable and c.local_path is None and calls == []
    assert c.name == "04.mp4" and c.duration_text == "1:24:41" and c.can_download is False
    assert c.alternatives  # 正規の代替手段を示す


def test_state_c_metadata_only_when_permission_unknown(tmp_path):
    html = '<script>{"typedID":"f_1","type":"file","id":1,"description":"","extension":"mp4","name":"a.mp4"}</script>'
    c = B.check_link(URL, token="", browser=lambda u, **k: {"ok": True, "html": html, "text": "a.mp4", "title": "",
                                                            "final_url": URL}, http=ok_http)
    assert c.state == "C" and c.can_download is None and "確認できない" in c.reason


def test_state_d_login_and_password(tmp_path):
    login = lambda u, **k: {"ok": True, "html": "", "text": "Log in to Box\nEmail", "title": "Box | Login",  # noqa: E731
                            "final_url": "https://account.box.com/login?redirect_url=/s/x"}
    assert B.check_link(URL, token="", browser=login, http=ok_http).state == "D"
    pw = lambda u, **k: {"ok": True, "html": "", "text": "This shared link is password-protected", "title": "Box",  # noqa: E731
                         "final_url": URL, "has_password_input": True}
    c = B.check_link(URL, token="", browser=pw, http=ok_http)
    assert c.state == "D" and "パスワード" in c.reason


def test_state_e_removed_timeout_network():
    removed = lambda u, **k: {"ok": True, "html": "", "title": "Box", "final_url": URL,  # noqa: E731
                              "text": "This shared file or folder link has been removed or is unavailable to you."}
    assert B.check_link(URL, token="", browser=removed, http=ok_http).state == "E"
    nf = B.check_link(URL, token="", browser=removed, http=lambda u, **k: {"ok": False, "status": 404,
                                                                           "error_kind": "not_found"})
    assert nf.state == "E" and "期限切れ" in nf.reason
    to = B.check_link(URL, token="", browser=lambda u, **k: {"ok": False, "error_kind": "timeout"},
                      http=lambda u, **k: {"ok": False, "error_kind": "timeout"})
    assert to.state == "E" and to.reason == "タイムアウト"
    net = B.check_link(URL, token="", browser=lambda u, **k: {"ok": False, "error_kind": "network"},
                       http=lambda u, **k: {"ok": False, "error_kind": "network"})
    assert "ネットワーク" in net.reason


def test_without_playwright_falls_back_without_crashing(monkeypatch):
    monkeypatch.setattr(B, "browser_available", lambda: {"python_playwright": False, "node_playwright": False,
                                                         "node": None})
    r = B.browser_probe(URL)
    assert r["error_kind"] == "browser_unavailable" and "pip install playwright" in r["error"]
    c = B.check_link(URL, token="", http=ok_http)  # 既定のブラウザ手段 → 使えない
    assert c.state == "C" and "ブラウザが無い" in c.reason


def test_http_probe_does_not_follow_redirect_outside_box():
    class Resp:
        def __init__(self, status, loc=None):
            self.status_code, self.headers, self.text = status, {"Location": loc} if loc else {}, ""
            self.is_redirect = loc is not None

    class Sess:
        def __init__(self):
            self.urls = []

        def get(self, url, **k):
            self.urls.append(url)
            return Resp(302, "https://evil.example/collect") if "box.com" in url else Resp(200)

    s = Sess()
    r = B.http_probe(URL, session=s)
    assert r["error_kind"] == "redirect_outside_box" and s.urls == [URL]


def _tiny_mp4(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=64x48:d=2",
                    "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(path)], check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg が必要")
def test_state_a_download_when_allowed_via_api(tmp_path):
    src = _tiny_mp4(tmp_path / "src.mp4")
    api = lambda u, t, **k: {"ok": True, "id": "99", "type": "file", "name": "05.mp4", "size": 10,  # noqa: E731
                             "extension": "mp4", "can_download": True, "can_preview": True}

    def dl(url, token, fid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        return dest

    c = B.check_link(URL, work_dir=tmp_path / "w", allow_download=True, token="tkn", api=api, downloader=dl,
                     http=ok_http, browser=lambda *a, **k: pytest.fail("API で分かるならブラウザは使わない"))
    assert c.state == "A" and c.analyzable and Path(c.local_path).exists()
    # 許可されていても --allow-download が無ければ取得しない
    c2 = B.check_link(URL, work_dir=tmp_path / "w2", allow_download=False, token="tkn", api=api,
                      downloader=lambda *a: pytest.fail("取得しない"), http=ok_http)
    assert c2.state == "C" and "--allow-download" in c2.reason


def test_state_f_downloaded_but_broken(tmp_path):
    def dl(url, token, fid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"not a video")
        return dest

    api = lambda u, t, **k: {"ok": True, "id": "1", "type": "file", "name": "x.mp4", "extension": "mp4",  # noqa: E731
                             "can_download": True}
    c = B.check_link(URL, work_dir=tmp_path, allow_download=True, token="t", api=api, downloader=dl, http=ok_http)
    assert c.state == "F" and not c.analyzable


def test_api_forbidden_means_auth_required():
    api = lambda u, t, **k: {"ok": False, "status": 403, "error_kind": "forbidden"}  # noqa: E731
    c = B.check_link(URL, token="t", api=api, http=ok_http, browser=lambda u, **k: {"ok": False, "error_kind": "browser"})
    assert c.state == "D"
