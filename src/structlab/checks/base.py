"""各工程のチェック結果の記録。

状態は5種類に分け、確認できなかったものを「成功」に含めない:
  ok           成功（照合して一致した）
  mismatch     不一致（照合して食い違った）
  needs_review 要確認（機械的には判断できない疑いがある）
  unverified   未検証（照合の材料・手段がない）
  failed       チェック自体が実行できなかった
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ..db import DB

STATUS_LABEL = {"ok": "成功", "mismatch": "不一致", "needs_review": "要確認", "unverified": "未検証", "failed": "チェック不能"}
STAGE_LABEL = {"extraction": "資料抽出", "understanding": "局所理解・全体理解", "directives": "指示抽出",
               "integration": "複数資料の統合", "deliverable": "提出物（Excel・レポート）", "style": "文章スタイル適用",
               "reverify": "独立した再検証"}


def _s(v: Any) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


def input_hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()[:16]


def record(db: DB, *, stage: str, check_type: str, target_type: str, target_id: Any, status: str,
           expected: Any = None, actual: Any = None, detail: str = "", file_id: int | None = None,
           unit_id: int | None = None, run_id: int | None = None, ihash: str = "") -> None:
    assert status in STATUS_LABEL, status
    db.x("INSERT INTO checks(run_id,stage,check_type,file_id,unit_id,target_type,target_id,status,expected,actual,detail,"
         "input_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stage,check_type,target_type,target_id) "
         "DO UPDATE SET run_id=excluded.run_id, status=excluded.status, expected=excluded.expected, actual=excluded.actual,"
         " detail=excluded.detail, input_hash=excluded.input_hash, file_id=excluded.file_id, unit_id=excluded.unit_id,"
         " created_at=excluded.created_at",
         (run_id, stage, check_type, file_id, unit_id, target_type, str(target_id), status, _s(expected), _s(actual),
          detail[:4000], ihash, time.time()))


def clear(db: DB, stage: str, *, file_id: int | None = None, check_type: str | None = None,
          target_type: str | None = None) -> None:
    sql, args = "DELETE FROM checks WHERE stage=?", [stage]
    if file_id is not None:
        sql += " AND file_id=?"
        args.append(file_id)
    if check_type is not None:
        sql += " AND check_type=?"
        args.append(check_type)
    if target_type is not None:
        sql += " AND target_type=?"
        args.append(target_type)
    db.x(sql, args)


def existing_hash(db: DB, stage: str, check_type: str, target_type: str, target_id: Any) -> str | None:
    r = db.q1("SELECT input_hash FROM checks WHERE stage=? AND check_type=? AND target_type=? AND target_id=?",
              (stage, check_type, target_type, str(target_id)))
    return r["input_hash"] if r else None
