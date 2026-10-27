"""入力の発見: ローカルフォルダ・Dropbox のファイルと、.url / テキスト内の Box 共有リンク。

元ファイルは読み取りだけ（移動・削除・上書きしない）。Dropbox のファイルは作業フォルダへ複製して読む。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import links as L

VIDEO = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv"}
AUDIO = {".mp3", ".m4a", ".wav", ".aac"}
PDF = {".pdf"}
OFFICE = {".pptx", ".ppt", ".docx", ".doc", ".odp", ".odt"}
IMAGE = {".png", ".jpg", ".jpeg", ".heic", ".webp"}
TRANSCRIPT = {".vtt", ".srt"}
LINKFILE = {".url", ".webloc"}
TEXT = {".txt", ".md"}
SKIP = re.compile(r"(^|/)(\.|~\$|desktop\.ini$|Thumbs\.db$)|\.part$|\.tmp$|\.crdownload$", re.I)

#: ファイル名・フォルダ名から資料の役割を推定する語（強い順）
ROLE_WORDS = [
    ("answer", r"模範解答|解答例|解答解説|answer|solution"),
    ("past_exam", r"過去問|定期試験|期末試験|中間試験|本試験|past[_ -]?exam|exam\d{2,4}"),
    ("scope", r"試験範囲|出題範囲|範囲の連絡|scope"),
    ("quiz", r"小テスト|quiz"),
    ("assignment", r"課題|宿題|レポート課題|assignment|homework"),
    ("exercise", r"演習|練習問題|例題|exercise|practice"),
    ("slides", r"スライド|講義資料|配布資料|レジュメ|handout|slide|lecture"),
]


@dataclass
class Material:
    id: str                    # 内容の sha256 の先頭 16 桁（同じ内容の重複投入を 1 件にする）
    name: str
    kind: str                  # video / audio / pdf / office / image / transcript / linkfile / text / box_link
    role: str                  # slides / lecture_video / assignment / exercise / past_exam / answer / scope / quiz / other
    source: str                # 元の場所（local:相対パス / dropbox:/パス / box:URL）
    local_path: str | None = None
    size: int | None = None
    sha256: str | None = None
    folder: str = ""
    year: int | None = None
    also_at: list[str] = field(default_factory=list)  # 同じ内容の別の場所
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def kind_of(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    for kind, exts in (("video", VIDEO), ("audio", AUDIO), ("pdf", PDF), ("office", OFFICE), ("image", IMAGE),
                       ("transcript", TRANSCRIPT), ("linkfile", LINKFILE), ("text", TEXT)):
        if ext in exts:
            return kind
    return None


def role_of(path: str, kind: str) -> str:
    for role, pat in ROLE_WORDS:
        if re.search(pat, path, re.I):
            return role
    return "lecture_video" if kind in ("video", "audio", "box_link") else ("slides" if kind in ("pdf", "office") else "other")


def year_of(path: str) -> int | None:
    m = re.search(r"(20\d{2})(?:年度?|[_\-./ ]|$)", path)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:令和|R)(\d{1,2})(?:年度?)", path)
    return 2018 + int(m.group(1)) if m else None


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Discovery:
    materials: list[Material] = field(default_factory=list)
    links: list[L.LinkRecord] = field(default_factory=list)
    link_logs: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)

    def add(self, m: Material) -> None:
        for o in self.materials:
            if m.sha256 and o.sha256 == m.sha256:
                o.also_at.append(m.source)
                self.duplicates.append({"kept": o.source, "duplicate": m.source})
                return
        self.materials.append(m)


def _register(d: Discovery, local: Path, rel: str, source: str) -> None:
    kind = kind_of(rel)
    if kind is None:
        d.skipped.append({"source": source, "reason": "対応していない形式"})
        return
    folder = str(Path(rel).parent).replace("\\", "/")
    if kind in ("linkfile", "text"):
        data = local.read_bytes()
        recs, log = L.extract_links(data, local.name, source)
        boxes = [r for r in recs if r.kind == "box_shared"]
        d.link_logs.append(log)
        d.links += recs
        if kind == "linkfile" or (boxes and len(data) < 4096 and not _looks_like_document(data)):
            return  # リンクだけのファイル。資料としては扱わない
    sha = sha256_file(local)
    d.add(Material(id=sha[:16], name=Path(rel).name, kind=kind, role=role_of(rel, kind), source=source,
                   local_path=str(local), size=local.stat().st_size, sha256=sha, folder=folder, year=year_of(rel)))


def _looks_like_document(data: bytes) -> bool:
    text, _ = L.decode_bytes(data)
    return len(L.URL_IN_TEXT.sub("", text).strip()) > 200


def discover_local(root: Path, d: Discovery | None = None) -> Discovery:
    d = d or Discovery()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if not p.is_file() or SKIP.search(rel):
            continue
        _register(d, p, rel, f"local:{rel}")
    return d


def discover_dropbox(source, cache_dir: Path, d: Discovery | None = None, max_bytes: int | None = None) -> Discovery:
    """既存の DropboxSource（読み取り専用）で一覧・取得する。取得物は cache_dir に置く。"""
    d = d or Discovery()
    for rf in source.iter_files():
        if SKIP.search(rf.path) or kind_of(rf.path) is None:
            continue
        if max_bytes and rf.size > max_bytes:
            d.skipped.append({"source": f"dropbox:{rf.path}", "reason": f"サイズ上限超過（{rf.size} バイト）"})
            continue
        local = source.fetch(rf, cache_dir / rf.path)
        _register(d, local, rf.path, f"dropbox:{rf.path}")
    return d


def box_materials(d: Discovery) -> list[Material]:
    """Box 共有リンクを資料（未取得）として登録する。取得できたら analyze で動画として扱う。"""
    out = []
    for r in L.dedupe([x for x in d.links if x.kind == "box_shared"]):
        folder = str(Path(r.sources[0].split(":", 1)[-1]).parent) if r.sources else ""
        lid = hashlib.sha256(r.url.encode()).hexdigest()[:16]
        name = Path(r.sources[0]).stem if r.sources else r.shared_id
        out.append(Material(id=lid, name=name, kind="box_link", role=role_of(folder + "/" + name, "box_link"),
                            source=f"box:{r.url}", folder=folder, also_at=r.sources[1:], notes=[]))
    return out
