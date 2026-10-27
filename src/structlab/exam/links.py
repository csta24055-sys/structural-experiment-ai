"""Dropbox の .url ファイル・テキストから Box 共有リンクを取り出して検証する。

Box 以外のリンクを Box として扱わない。許可したホスト名（*.box.com）以外には接続しない。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Box 共有リンクとして扱うホスト（サブドメインを含む）
BOX_HOST_SUFFIXES = ("box.com",)
#: 共有リンクのパス: /s/<識別子>（共有リンク） /v/<名前>（独自 URL）
SHARE_PATH = re.compile(r"^/(s|v)/([A-Za-z0-9_\-]+)(?:/|$)")
URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>\]\)）」]+")
#: 追跡用の問い合わせ（utm_* など）は除く。リンクの同一性の判定に使わない
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$)")


@dataclass
class LinkRecord:
    url: str                      # 正規化した URL
    raw: str                      # 元の文字列
    kind: str                     # box_shared | box_other | other | invalid
    host: str = ""
    shared_id: str = ""
    sources: list[str] = field(default_factory=list)  # 見つけた場所（Dropbox のパスなど）
    problem: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def decode_bytes(data: bytes) -> tuple[str, str]:
    """.url / テキストの文字コードを判定する。戻り値: (本文, 文字コード)"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace"), "utf-16"
    for enc in ("utf-8", "cp932", "euc_jp"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(置換あり)"


def is_box_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in BOX_HOST_SUFFIXES)


def normalize(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", query, ""))


def classify_url(raw: str) -> LinkRecord:
    raw = raw.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return LinkRecord(url=raw, raw=raw, kind="invalid", problem="URL として解釈できない")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return LinkRecord(url=raw, raw=raw, kind="invalid", problem="http(s) の URL ではない")
    if parts.username or parts.password:
        return LinkRecord(url=raw, raw=raw, kind="invalid", problem="URL に認証情報が含まれている")
    host = (parts.hostname or "").lower()
    url = normalize(raw)
    if not is_box_host(host):
        return LinkRecord(url=url, raw=raw, kind="other", host=host)
    if parts.scheme != "https":
        url = "https" + url[len("http"):]
    m = SHARE_PATH.match(parts.path)
    if m:
        return LinkRecord(url=url, raw=raw, kind="box_shared", host=host, shared_id=m.group(2))
    return LinkRecord(url=url, raw=raw, kind="box_other", host=host, problem="Box の共有リンク形式（/s/ /v/）ではない")


def parse_internet_shortcut(text: str) -> list[str]:
    """Windows の .url（[InternetShortcut] / URL=...）から URL を取り出す。無ければ本文中の URL。"""
    urls = []
    in_section = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = s.lower() == "[internetshortcut]"
            continue
        if in_section and s.lower().startswith("url="):
            urls.append(s[4:].strip())
    return urls or find_urls(text)


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;、。") for u in URL_IN_TEXT.findall(text)]


def extract_links(data: bytes, name: str, source: str) -> tuple[list[LinkRecord], dict]:
    """1 ファイル分。戻り値: (リンク, 読み取り記録)"""
    text, enc = decode_bytes(data)
    raws = parse_internet_shortcut(text) if name.lower().endswith(".url") else find_urls(text)
    recs = []
    for r in raws:
        rec = classify_url(r)
        rec.sources.append(source)
        recs.append(rec)
    log = {"source": source, "encoding": enc, "n_urls": len(recs)}
    if not recs:
        log["problem"] = "URL が見つからない（.url が壊れている可能性）"
    return recs, log


def dedupe(records: list[LinkRecord]) -> list[LinkRecord]:
    """同じリンク（正規化後の URL が同じ）を 1 件にまとめ、見つけた場所をすべて残す。"""
    out: dict[str, LinkRecord] = {}
    for r in records:
        key = f"box:{r.host.split('.')[0] if r.host.endswith('.box.com') else r.host}:{r.shared_id}" \
            if r.kind == "box_shared" else r.url
        if key in out:
            out[key].sources += [s for s in r.sources if s not in out[key].sources]
        else:
            out[key] = LinkRecord(**{**r.__dict__, "sources": list(r.sources)})
    return list(out.values())
