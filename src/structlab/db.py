"""SQLite による処理状況・解析結果の管理。

テーブル:
  files     … 検出したファイル（ZIP内部のファイルも parent_id 付きで登録）
  units     … 解析単位（PDFページ・動画区間・シート範囲・画像など）。再開・再試行の単位
  items     … 分類済みの知見（出典・時刻・由来・検証状態付き）
  timeline  … 動画の音声・映像の時刻対応
  errors    … 失敗記録
  api_usage … AI API の使用量
  runs      … 実行履歴
  kv        … 任意のキー値
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES files(id),
    remote_id TEXT,
    kind TEXT NOT NULL,
    ext TEXT,
    size INTEGER,
    server_modified TEXT,
    rev TEXT,
    content_hash TEXT,
    first_seen REAL,
    last_seen REAL,
    stable_since REAL,
    status TEXT NOT NULL DEFAULT 'discovered',
    status_detail TEXT,
    processed_hash TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id),
    file_hash TEXT NOT NULL,
    unit_type TEXT NOT NULL,
    locator TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    ai_status TEXT NOT NULL DEFAULT 'not_requested',
    attempts INTEGER NOT NULL DEFAULT 0,
    ai_attempts INTEGER NOT NULL DEFAULT 0,
    ai_error TEXT,
    last_error TEXT,
    extract_json TEXT,
    updated_at REAL,
    UNIQUE(file_id, file_hash, unit_type, locator)
);
CREATE INDEX IF NOT EXISTS idx_units_file ON units(file_id, file_hash, status);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    file_id INTEGER NOT NULL REFERENCES files(id),
    file_hash TEXT NOT NULL,
    category TEXT NOT NULL,
    statement TEXT NOT NULL,
    origin TEXT NOT NULL,
    is_instructor_instruction INTEGER NOT NULL DEFAULT 0,
    evidence TEXT,
    confidence TEXT,
    verification TEXT,
    source_path TEXT NOT NULL,
    page INTEGER,
    t_start REAL,
    t_end REAL,
    analyzer TEXT NOT NULL,
    extra_json TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_items_cat ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_unit ON items(unit_id);

CREATE TABLE IF NOT EXISTS timeline (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    file_id INTEGER NOT NULL REFERENCES files(id),
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    speech TEXT,
    frame_path TEXT,
    frame_time REAL,
    screen_text TEXT,
    important INTEGER NOT NULL DEFAULT 0,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_timeline_unit ON timeline(unit_id);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    file_id INTEGER,
    unit_id INTEGER,
    stage TEXT,
    message TEXT,
    detail TEXT,
    created_at REAL,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    unit_id INTEGER,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    images INTEGER,
    ok INTEGER,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at REAL,
    ended_at REAL,
    status TEXT,
    stats_json TEXT
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.set_kv("schema_version", str(SCHEMA_VERSION))

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def q(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def q1(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, args)

    # --- kv ---
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        r = self.q1("SELECT value FROM kv WHERE key=?", (key,))
        return r["value"] if r else default

    def set_kv(self, key: str, value: str) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))

    # --- runs ---
    def start_run(self) -> int:
        cur = self.x("INSERT INTO runs(started_at,status) VALUES(?, 'running')", (time.time(),))
        return int(cur.lastrowid)

    def end_run(self, run_id: int, status: str, stats: dict) -> None:
        self.x("UPDATE runs SET ended_at=?, status=?, stats_json=? WHERE id=?",
               (time.time(), status, json.dumps(stats, ensure_ascii=False), run_id))

    # --- errors ---
    def log_error(self, *, run_id: int | None, stage: str, message: str, detail: str = "",
                  file_id: int | None = None, unit_id: int | None = None) -> None:
        self.x("INSERT INTO errors(run_id,file_id,unit_id,stage,message,detail,created_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (run_id, file_id, unit_id, stage, message[:2000], detail[:20000], time.time()))

    # --- units ---
    def recover_interrupted(self) -> int:
        """前回の異常終了で running のまま残った単位・ファイルを pending/ready に戻す。"""
        n = self.x("UPDATE units SET status='pending' WHERE status='running'").rowcount
        n += self.x("UPDATE files SET status='ready' WHERE status='processing'").rowcount
        return n

    def add_items(self, rows: list[dict[str, Any]]) -> None:
        now = time.time()
        for r in rows:
            self.x(
                "INSERT INTO items(unit_id,file_id,file_hash,category,statement,origin,"
                "is_instructor_instruction,evidence,confidence,verification,source_path,page,"
                "t_start,t_end,analyzer,extra_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["unit_id"], r["file_id"], r["file_hash"], r["category"], r["statement"],
                 r["origin"], int(bool(r.get("is_instructor_instruction"))), r.get("evidence"),
                 r.get("confidence"), r.get("verification"), r["source_path"], r.get("page"),
                 r.get("t_start"), r.get("t_end"), r["analyzer"],
                 json.dumps(r.get("extra") or {}, ensure_ascii=False), now),
            )

    def usage_totals(self, run_id: int | None = None) -> dict[str, int]:
        where, args = ("WHERE run_id=?", (run_id,)) if run_id is not None else ("", ())
        r = self.q1(f"SELECT COUNT(*) c, COALESCE(SUM(input_tokens),0) i, "
                    f"COALESCE(SUM(output_tokens),0) o, COALESCE(SUM(images),0) im FROM api_usage {where}",
                    args)
        return {"calls": r["c"], "input_tokens": r["i"], "output_tokens": r["o"], "images": r["im"]}

    def backup_to(self, dest: Path) -> None:
        """実行中でも整合性のあるコピーを作る（Dropbox 同期用）。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()
        out = sqlite3.connect(str(tmp))
        with out:
            self.conn.backup(out)
        out.close()
        tmp.replace(dest)
