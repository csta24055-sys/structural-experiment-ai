"""ソースを走査し、新規・更新・削除・アップロード途中のファイルを判定して DB に記録する。"""
from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .db import DB
from .models import RemoteFile, kind_for
from .sources import Source


@dataclass
class ScanResult:
    total: int = 0
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    ready: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    total_bytes: int = 0

    def summary(self) -> dict:
        return {"total": self.total, "total_mb": round(self.total_bytes / 1e6, 1), "new": len(self.new),
                "changed": len(self.changed), "deleted": len(self.deleted), "waiting_upload": len(self.waiting),
                "ready": len(self.ready), "ignored": len(self.ignored)}


def is_ignored(path: str, patterns: list[str]) -> bool:
    parts = PurePosixPath(path).parts
    return any(fnmatch.fnmatch(p, pat) for p in parts for pat in patterns)


def remote_file_of(row) -> RemoteFile:
    return RemoteFile(path=row["path"], size=row["size"] or 0, server_modified=0.0,
                      content_hash=row["content_hash"] or "", rev=row["rev"] or "", remote_id=row["remote_id"] or "")


def scan(db: DB, source: Source, cfg, now: float | None = None) -> ScanResult:
    st = cfg.data["stability"]
    min_age = float(st["min_age_seconds"])
    min_stable = float(st["min_stable_seconds"])
    patterns = list(st["ignore_patterns"])
    res = ScanResult()
    scan_start = now if now is not None else time.time()
    seen: set[str] = set()

    for rf in source.iter_files():
        if is_ignored(rf.path, patterns):
            res.ignored.append(rf.path)
            continue
        now_t = now if now is not None else time.time()
        res.total += 1
        res.total_bytes += rf.size
        key = rf.path.lower()
        seen.add(key)
        kind, ext = kind_for(rf.path)
        row = db.q1("SELECT * FROM files WHERE path_key=? AND parent_id IS NULL", (key,))
        if row is None:
            # 新規。Dropbox のリビジョンは原子的に確定するので、サーバー更新時刻から安定とみなす
            db.x("INSERT INTO files(path,path_key,remote_id,kind,ext,size,server_modified,rev,content_hash,"
                 "first_seen,last_seen,stable_since,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'discovered', ?)",
                 (rf.path, key, rf.remote_id, kind, ext, rf.size, str(rf.server_modified), rf.rev, rf.content_hash,
                  now_t, now_t, min(rf.server_modified, now_t), now_t))
            res.new.append(rf.path)
            row = db.q1("SELECT * FROM files WHERE path_key=? AND parent_id IS NULL", (key,))
        elif row["content_hash"] != rf.content_hash or row["size"] != rf.size:
            db.x("UPDATE files SET path=?, remote_id=?, kind=?, ext=?, size=?, server_modified=?, rev=?, content_hash=?,"
                 " last_seen=?, stable_since=?, status='discovered', status_detail='更新を検出（再解析待ち）',"
                 " attempts=0, last_error=NULL, updated_at=? WHERE id=?",
                 (rf.path, rf.remote_id, kind, ext, rf.size, str(rf.server_modified), rf.rev, rf.content_hash,
                  now_t, now_t, now_t, row["id"]))
            res.changed.append(rf.path)
            row = db.q1("SELECT * FROM files WHERE id=?", (row["id"],))
        else:
            upd_status = row["status"]
            if upd_status == "deleted":
                upd_status = "done" if row["processed_hash"] == rf.content_hash else "discovered"
            db.x("UPDATE files SET last_seen=?, rev=?, remote_id=?, status=? WHERE id=?",
                 (now_t, rf.rev, rf.remote_id, upd_status, row["id"]))
            row = db.q1("SELECT * FROM files WHERE id=?", (row["id"],))

        if row["status"] == "discovered":
            age = now_t - rf.server_modified
            stable_for = now_t - (row["stable_since"] or now_t)
            if age >= min_age and stable_for >= min_stable:
                db.x("UPDATE files SET status='ready', status_detail=NULL WHERE id=?", (row["id"],))
                res.ready.append(rf.path)
            else:
                wait = max(min_age - age, min_stable - stable_for)
                db.x("UPDATE files SET status_detail=? WHERE id=?",
                     (f"アップロード完了待ち（あと約{int(wait)}秒）", row["id"]))
                res.waiting.append(rf.path)
        elif row["status"] == "ready":
            res.ready.append(rf.path)

    # 見つからなくなったファイル（ZIP内部ファイルは親に従う）
    for row in db.q("SELECT id, path, path_key FROM files WHERE parent_id IS NULL AND status != 'deleted'"):
        if row["path_key"] not in seen:
            db.x("UPDATE files SET status='deleted', status_detail='ソースから削除された', updated_at=? WHERE id=?",
                 (scan_start, row["id"]))
            res.deleted.append(row["path"])
    db.set_kv("last_scan", str(scan_start))
    db.set_kv("last_scan_summary", str(res.summary()))
    return res
