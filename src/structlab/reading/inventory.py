"""資料の棚卸し: 全ファイルを「解析する資料」「内容が同一の重複」「資料ではない補助ファイル」などに分ける。

重複は Dropbox の content_hash（内容のハッシュ）が同じものだけを重複とみなす（名前が似ているだけでは重複にしない）。
同じ内容のファイルが複数あるときは、名前に " (1)" などの付かないもの・浅い階層のもの・先に置かれたものを代表にする。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

from ..models import RemoteFile, kind_for
from ..scanner import is_ignored

COPY_SUFFIX = re.compile(r"\s*\((\d+)\)$|\s+(\d+)$|\s*のコピー$|\s*copy$", re.IGNORECASE)
#: 資料ではない補助ファイル（スクリプト・設定など）
HELPER_EXT = {".sh", ".bat", ".command", ".ps1", ".py", ".js", ".json", ".yaml", ".yml", ".ini", ".plist"}
READABLE_KINDS = {"pdf", "video", "audio", "image", "excel", "text", "docx", "pptx", "zip"}


@dataclass
class InventoryItem:
    path: str
    kind: str
    size: int
    content_hash: str
    role: str                      # material / duplicate / helper / unsupported / ignored
    canonical: str | None = None   # role=duplicate のとき代表ファイル
    duplicates: list[str] = field(default_factory=list)
    note: str = ""


def _stem_key(path: str) -> str:
    p = PurePosixPath(path)
    return COPY_SUFFIX.sub("", p.stem)


def _rank(rf: RemoteFile) -> tuple:
    p = PurePosixPath(rf.path)
    copy_marked = bool(COPY_SUFFIX.search(p.stem))
    return (copy_marked, len(p.parts), rf.server_modified, rf.path)


def build_inventory(files: list[RemoteFile], ignore_patterns: list[str] | None = None) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    groups: dict[str, list[RemoteFile]] = defaultdict(list)
    for rf in files:
        kind, ext = kind_for(rf.path)
        if ignore_patterns and is_ignored(rf.path, ignore_patterns):
            items.append(InventoryItem(rf.path, kind, rf.size, rf.content_hash, "ignored", note="一時ファイル等"))
            continue
        if ext in HELPER_EXT:
            items.append(InventoryItem(rf.path, kind, rf.size, rf.content_hash, "helper",
                                       note="資料ではない補助ファイル（スクリプト等）。内容は解析しない"))
            continue
        if kind not in READABLE_KINDS:
            items.append(InventoryItem(rf.path, kind, rf.size, rf.content_hash, "unsupported",
                                       note=f"未対応の形式（{ext or '拡張子なし'}）"))
            continue
        groups[rf.content_hash or f"nohash:{rf.path}"].append(rf)
    for h, members in groups.items():
        members.sort(key=_rank)
        head, rest = members[0], members[1:]
        kind = kind_for(head.path)[0]
        items.append(InventoryItem(head.path, kind, head.size, head.content_hash, "material",
                                   duplicates=[m.path for m in rest]))
        for m in rest:
            same_name = _stem_key(m.path) == _stem_key(head.path)
            items.append(InventoryItem(m.path, kind, m.size, m.content_hash, "duplicate", canonical=head.path,
                                       note="内容が完全に同一" + ("（名前違いのコピー）" if same_name else "（別名・別フォルダ）")))
    order = {"material": 0, "duplicate": 1, "helper": 2, "unsupported": 3, "ignored": 4}
    items.sort(key=lambda i: (order[i.role], i.path))
    # 同一内容の helper はまとめて注記
    hh: dict[str, list[InventoryItem]] = defaultdict(list)
    for i in items:
        if i.role == "helper":
            hh[i.content_hash].append(i)
    for same in hh.values():
        if len(same) > 1:
            for i in same:
                i.note += f"（同じ内容が {len(same)} か所）"
    return items


def inventory_markdown(items: list[InventoryItem]) -> str:
    label = {"material": "解析する資料", "duplicate": "重複（解析しない）", "helper": "補助ファイル（解析しない）",
             "unsupported": "未対応形式", "ignored": "対象外"}
    n = defaultdict(int)
    for i in items:
        n[i.role] += 1
    lines = ["# 資料の棚卸し", "",
             "、".join(f"{label[k]} {n[k]} 件" for k in label if n[k]) + f"（合計 {len(items)} 件）", "",
             "重複は内容のハッシュが完全に一致したものだけ。代表ファイルだけを読み取り、重複は代表の結果を参照する。", "",
             "| 区分 | ファイル | 種類 | サイズ(MB) | 備考 |", "|---|---|---|---:|---|"]
    for i in items:
        note = i.note
        if i.role == "material" and i.duplicates:
            note = f"同一内容 {len(i.duplicates)} 件: " + "、".join(i.duplicates)
        elif i.role == "duplicate":
            note = f"{i.note}。代表: {i.canonical}"
        lines.append(f"| {label[i.role]} | {i.path} | {i.kind} | {i.size / 1e6:,.1f} | {note} |")
    return "\n".join(lines) + "\n"


def as_dicts(items: list[InventoryItem]) -> list[dict]:
    return [asdict(i) for i in items]
