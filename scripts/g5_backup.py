#!/usr/bin/env python3
"""G5 の作業フォルダの保全: 作り直せないもの（答え・P-G5 の結果・印）だけを少しずつ束ねて外に保存し、消えたら戻す。

作業環境が回収されると work/ は消える。依頼・画像は前処理の成果物から作り直せるが、答え（資料の内容を含む）と
有料の P-G5 の答えは作り直せない（作り直すと利用枠・費用がかかる）。それらを保存先に「追加だけ」で保存する。

  python3 scripts/g5_backup.py push    [--work work/g5] [--to dropbox|dir:PATH]   新しい・変わったものを束ねて保存（無料）
  python3 scripts/g5_backup.py restore [--work work/g5] [--from dropbox|dir:PATH] 保存先から戻す（既存のファイルは上書きしない）
  python3 scripts/g5_backup.py status  [--work work/g5]                           保存済み・未保存の件数

保存先（docs/G5_BACKUP.md で比較）:
  dropbox … Dropbox の解析結果フォルダ /鋼材実験レポート用_解析結果/g5_backup（元資料のフォルダの外。追加だけで、同じ名前があれば失敗。
            上書き・削除はしない）。認証情報 DROPBOX_* が環境に必要（値は表示しない）
  dir:PATH … 手元のフォルダ（テスト用）
既定の保存先は環境変数 G5_BACKUP（未設定なら dropbox）。

束の形: g5-<通し番号5桁>-<内容の sha256 の先頭12桁>.tar.xz。中に manifest.json（ファイルごとの sha256）。
戻すときは名前と中身の sha256 を確かめ、壊れた・書き換えられた束は使わない。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DROPBOX_ROOT = "/鋼材実験レポート用_解析結果/g5_backup"
SOURCE_ROOT = "/鋼材実験レポート用"
NAME = re.compile(r"^g5-(\d{5})-([0-9a-f]{12})\.tar\.xz$")
#: 作り直せないもの（work/g5 からの相対パスのパターン）
KEEP = ("queue/answers/*.json", "pg5/**/*", "TEST_ONLY_DO_NOT_USE", "g5_status.json")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def targets(work: Path) -> dict[str, Path]:
    out = {}
    for pat in KEEP:
        for p in sorted(work.glob(pat)):
            if p.is_file() and not p.name.endswith(".tmp"):
                out[p.relative_to(work).as_posix()] = p
    return out


def index_path(work: Path) -> Path:
    return work / "backup" / "index.json"


def load_index(work: Path) -> dict:
    p = index_path(work)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"files": {}, "bundles": []}


def save_index(work: Path, idx: dict) -> None:
    p = index_path(work)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


# ---------------- 保存先 ----------------
class DirRemote:
    def __init__(self, root: Path):
        self.root = Path(root)

    def names(self) -> list[str]:
        return sorted(p.name for p in self.root.glob("g5-*.tar.xz")) if self.root.exists() else []

    def put(self, name: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / name
        with open(p, "xb") as f:   # 同じ名前があれば失敗（上書きしない）
            f.write(data)

    def get(self, name: str) -> bytes:
        return (self.root / name).read_bytes()


class DropboxRemote:
    def __init__(self, root: str = DROPBOX_ROOT, client=None):
        from structlab.sources.dropbox_source import DropboxOutput, make_client
        DropboxOutput(root, SOURCE_ROOT, client=client or object())  # 元資料のフォルダと重ならないかの確認
        self.root = root
        self.dbx = client or make_client()

    def names(self) -> list[str]:
        import dropbox
        try:
            r = self.dbx.files_list_folder(self.root)
        except dropbox.exceptions.ApiError:
            return []
        out = [e.name for e in r.entries]
        while r.has_more:
            r = self.dbx.files_list_folder_continue(r.cursor)
            out += [e.name for e in r.entries]
        return sorted(n for n in out if NAME.match(n))

    def put(self, name: str, data: bytes) -> None:
        import dropbox
        # 追加だけ: 同じ名前があれば失敗し、名前を変えて保存もしない
        self.dbx.files_upload(data, f"{self.root}/{name}", mode=dropbox.files.WriteMode.add, autorename=False, mute=True)

    def get(self, name: str) -> bytes:
        _, res = self.dbx.files_download(f"{self.root}/{name}")
        return res.content


def remote(spec: str | None):
    spec = spec or os.environ.get("G5_BACKUP") or "dropbox"
    if spec == "dropbox":
        return DropboxRemote()
    if spec.startswith("dir:"):
        return DirRemote(Path(spec[4:]))
    raise SystemExit(f"保存先の指定が不正です: {spec}（dropbox または dir:PATH）")


# ---------------- 保存 ----------------
def push(work: Path, rem) -> dict:
    idx = load_index(work)
    changed = {}
    for rel, p in targets(work).items():
        b = p.read_bytes()
        h = sha(b)
        if idx["files"].get(rel) != h:
            changed[rel] = (b, h)
    if not changed:
        return {"bundled": 0, "bundle": None}
    seq = max([int(NAME.match(n).group(1)) for n in rem.names()] + [len(idx["bundles"])]) + 1
    manifest = {"seq": seq, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "files": {rel: h for rel, (_, h) in changed.items()}}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for rel, (b, _) in [("manifest.json", (json.dumps(manifest, ensure_ascii=False).encode(), ""))] + sorted(changed.items()):
            ti = tarfile.TarInfo(rel)
            ti.size, ti.mtime, ti.mode = len(b), 0, 0o644
            tf.addfile(ti, io.BytesIO(b))
    data = buf.getvalue()
    name = f"g5-{seq:05d}-{sha(data)[:12]}.tar.xz"
    rem.put(name, data)
    idx["files"].update(manifest["files"])
    idx["bundles"].append(name)
    save_index(work, idx)
    return {"bundled": len(changed), "bundle": name, "bytes": len(data)}


# ---------------- 復元 ----------------
def _open(name: str, data: bytes) -> tuple[dict, dict[str, bytes]]:
    m = NAME.match(name)
    if not m or sha(data)[:12] != m.group(2):
        raise ValueError("束の名前と中身が合わない（壊れた・書き換えられた）")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
        for ti in tf.getmembers():
            parts = ti.name.split("/")
            if not ti.isfile() or ti.name.startswith("/") or ".." in parts:
                raise ValueError(f"不正な項目: {ti.name}")
            files[ti.name] = tf.extractfile(ti).read()
    manifest = json.loads(files.pop("manifest.json"))
    if set(manifest["files"]) != set(files) or any(sha(files[r]) != h for r, h in manifest["files"].items()):
        raise ValueError("中身の sha256 が一覧と合わない")
    return manifest, files


def restore(work: Path, rem) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    pre = set(targets(work))                      # 戻す前からあったファイル（上書きしない）
    written: dict[str, str] = {}
    res = {"bundles": 0, "restored": 0, "same": 0, "conflicts": [], "broken": []}
    idx = load_index(work)
    for name in rem.names():                      # 通し番号の順。後の束が同じファイルの新しい版
        try:
            manifest, files = _open(name, rem.get(name))
        except Exception as e:  # noqa: BLE001
            res["broken"].append(f"{name}: {e}")
            continue
        res["bundles"] += 1
        for rel, b in files.items():
            dest = work / rel
            if rel in pre:
                if sha(dest.read_bytes()) == manifest["files"][rel]:
                    res["same"] += 1
                else:
                    res["conflicts"].append(rel)  # 手元の版を残す（上書きしない）
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".restore.tmp")
            tmp.write_bytes(b)
            tmp.replace(dest)
            written[rel] = manifest["files"][rel]
        idx["files"].update({r: h for r, h in manifest["files"].items() if r not in pre})
        if name not in idx["bundles"]:
            idx["bundles"].append(name)
    res["restored"] = len(written)
    save_index(work, idx)
    return res


def status(work: Path) -> dict:
    idx = load_index(work)
    t = targets(work)
    pending = [r for r, p in t.items() if idx["files"].get(r) != sha(p.read_bytes())]
    return {"saved_files": len(idx["files"]), "bundles": len(idx["bundles"]), "unsaved": len(pending)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["push", "restore", "status"])
    ap.add_argument("--work", default=str(ROOT / "work" / "g5"))
    ap.add_argument("--to", dest="target")
    ap.add_argument("--from", dest="target")
    a = ap.parse_args()
    work = Path(a.work)
    if a.action == "status":
        r = status(work)
    elif a.action == "push":
        r = push(work, remote(a.target))
    else:
        r = restore(work, remote(a.target))
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 1 if r.get("conflicts") or r.get("broken") else 0


if __name__ == "__main__":
    raise SystemExit(main())
