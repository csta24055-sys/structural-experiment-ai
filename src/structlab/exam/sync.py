"""Dropbox の出力フォルダとの同期（クラウド実行で、教材と処理状態を GitHub に残さないため）。

  <出力フォルダ>/試験対策システム/...   生成した教材・Box 検査結果（output と同じ構成）
  <出力フォルダ>/_state/exam_state.tar.gz  処理状態（読み取り結果・Box 確認結果・判定台帳・履歴）。次回はここから再開
出力フォルダが資料フォルダと重なる場合は、元資料保護のため既存の DropboxOutput が中止する。
"""
from __future__ import annotations

import tarfile
from pathlib import Path

STATE = "_state/exam_state.tar.gz"
#: 再開に必要な状態だけ（画像・動画のコマは容量が大きいので持ち越さない）
KEEP = ("readings/*.json", "box_checks.json", "ledger/*.json", "history.jsonl", "render_fp/*.txt")


def restore_state(dst, work: Path) -> bool:
    work.mkdir(parents=True, exist_ok=True)
    arc = work / "_restore.tar.gz"
    if not dst.download_if_exists(STATE, arc):
        return False
    with tarfile.open(arc) as t:
        for m in t.getmembers():
            if m.isfile() and not m.name.startswith(("/", "..")) and ".." not in Path(m.name).parts:
                t.extract(m, work, filter="data")
    arc.unlink()
    return True


def save_state(dst, work: Path) -> int:
    files = sorted({p for pat in KEEP for p in work.glob(pat) if p.is_file()})
    arc = work / "_state.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        for p in files:
            t.add(p, arcname=p.relative_to(work).as_posix())
    dst.upload(arc, STATE)
    arc.unlink()
    return len(files)


def upload_outputs(dst, out: Path, prefix: str = "試験対策システム") -> int:
    n = 0
    for p in sorted(out.rglob("*")):
        if p.is_file():
            dst.upload(p, f"{prefix}/{p.relative_to(out).as_posix()}")
            n += 1
    return n
