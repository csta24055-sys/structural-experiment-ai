"""複数資料（動画・PDF・Excel説明・レポート説明・事前資料）を横断した実験全体の統合理解。

資料ごとの全体理解（scope='document'）とは別に scope='corpus' として保存する。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from ..ai import AIError, AIRequest
from ..db import DB
from ..models import fmt_time
from . import prompts as P
from .relations import link_corpus
from .rules import DIRECTIVE_CLASSES

log = logging.getLogger("structlab")


def best_docs(db: DB) -> list:
    """ファイルごとに採用する解析結果: AI意味理解が完了していればそれ、なければ規則のみの結果。"""
    rows = db.q("SELECT d.*, f.path, f.kind FROM sem_docs d JOIN files f ON f.id=d.file_id "
                "WHERE d.file_hash=f.content_hash AND d.version=? AND d.status='done' AND f.status IN ('done','partial')",
                (P.SEMANTIC_VERSION,))
    by_file: dict[int, object] = {}
    for r in rows:
        cur = by_file.get(r["file_id"])
        if cur is None or (r["mode"] == "ai" and cur["mode"] != "ai"):
            by_file[r["file_id"]] = r
    return sorted(by_file.values(), key=lambda r: r["path"])


def _dir_line(d) -> str:
    where = f" {fmt_time(d['t_start'])}" if d["t_start"] is not None else (f" p.{d['page']}" if d["page"] else "")
    cond = f"（条件: {d['condition']}）" if d["condition"] else ""
    return f"#{d['id']} [{DIRECTIVE_CLASSES[d['directive_class']]}] {d['content']}{cond} — {d['source_path']}{where}"


def corpus_key(db: DB, docs: list) -> str:
    parts = []
    for d in docs:
        v = db.q1("SELECT MAX(version) v FROM sem_understanding WHERE scope='document' AND doc_id=?", (d["id"],))["v"]
        n = db.q1("SELECT COUNT(*) c, MAX(id) m FROM directives WHERE doc_id=?", (d["id"],))
        parts.append(f"{d['id']}:{d['mode']}:{v}:{n['c']}:{n['m']}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def integrate(engine) -> None:
    db: DB = engine.db
    docs = best_docs(db)
    link_corpus(db, [d["id"] for d in docs])
    if not docs or not engine.ai_active:
        return
    eligible = db.q1("SELECT COUNT(*) c FROM files WHERE status IN ('done','partial') AND kind IN "
                     f"({','.join('?' * len(engine_eligible()))})", engine_eligible())["c"]
    ai_docs = [d for d in docs if d["mode"] == "ai"]
    if len(ai_docs) < eligible and not engine.sc.get("corpus_partial"):
        log.info("資料横断の統合理解は、全資料の意味理解が終わってから実行します（%d/%d）", len(ai_docs), eligible)
        return
    key = corpus_key(db, docs)
    if db.q1("SELECT 1 FROM sem_understanding WHERE scope='corpus' AND corpus_key=?", (key,)):
        return
    engine.checkpoint()
    und = []
    for d in docs:
        r = db.q1("SELECT data_json FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC LIMIT 1",
                  (d["id"],))
        g = json.loads(r["data_json"]) if r else None
        if g:
            und.append({"source": d["path"], **{k: g.get(k) for k in (
                "theme", "purpose", "experiment_purpose", "message", "procedure", "conclusion", "final_requirements",
                "changes", "apparent_contradictions", "action_goals", "open_questions", "explanation")}})
        else:
            und.append({"source": d["path"], "note": "AIによる全体理解なし（規則による指示抽出のみ）"})
    dirs = db.q(f"SELECT * FROM directives WHERE doc_id IN ({','.join('?' * len(docs))}) AND effective IN "
                "('effective','unresolved_conflict','superseded') ORDER BY source_path, t_start, page, id",
                [d["id"] for d in docs])
    links = db.q("SELECT * FROM directive_links WHERE scope='corpus'")
    hints = "\n".join(f"- #{l['from_id']} と #{l['to_id']}: {l['relation']}（{l['note']}）" for l in links) or "（なし）"
    dir_txt = "\n".join(_dir_line(d) + (" ※訂正済み（無効）" if d["effective"] == "superseded" else "") for d in dirs)
    limit = int(engine.sc.get("corpus_max_chars", 300000))
    body_und = json.dumps(und, ensure_ascii=False)
    chunks = [dir_txt]
    if len(body_und) + len(dir_txt) > limit:
        lines = dir_txt.splitlines()
        per = max(50, len(lines) * max(1, limit - len(body_und)) // max(1, len(dir_txt)))
        chunks = ["\n".join(lines[k:k + per]) for k in range(0, len(lines), per)]
    partials = []
    try:
        for ci, ch in enumerate(chunks):
            text = (f"{P.CORPUS_INSTRUCTION}\n## 資料ごとの全体理解\n```json\n{body_und[:limit]}\n```\n"
                    + (f"\n※指示一覧は {len(chunks)} 分割の {ci + 1} 番目です。\n" if len(chunks) > 1 else "")
                    + f"## 指示一覧（#番号で参照）\n{ch}\n## 規則で検出した資料間の関連・矛盾候補\n{hints}\n")
            if partials:
                text += f"\n## ここまでの統合結果\n```json\n{json.dumps(partials[-1], ensure_ascii=False)}\n```\n"
            r = engine._ai(AIRequest(task="corpus_integration", source_label="資料横断", text=text,
                                     system=P.COMMON_SYSTEM, schema=P.CORPUS_SCHEMA), None)
            partials.append(r.data or {})
    except AIError as e:
        db.log_error(run_id=engine.run_id, stage="semantic:corpus", message=str(e))
        log.error("資料横断の統合理解に失敗（次回再試行）: %s", e)
        return
    data = partials[-1]
    valid = {d["id"] for d in dirs}
    dropped = []
    for key_ in ("deliverables", "requirements", "common_directive_groups", "supplements", "corrections", "contradictions"):
        for item in data.get(key_) or []:
            ids = item.get("directive_ids") or []
            bad = [i for i in ids if i not in valid]
            if bad:
                dropped.append({"section": key_, "ids": bad})
            item["directive_ids"] = [i for i in ids if i in valid]
    data["_dropped_invalid_ids"] = dropped
    data["_sources"] = [d["path"] for d in docs]
    data["_modes"] = {d["path"]: d["mode"] for d in docs}
    db.x("INSERT INTO sem_understanding(scope,corpus_key,version,data_json,analyzer,created_at) VALUES('corpus',?,?,?,?,?)",
         (key, (db.q1("SELECT COUNT(*) c FROM sem_understanding WHERE scope='corpus'")["c"] or 0) + 1,
          json.dumps(data, ensure_ascii=False), engine.ai_name, time.time()))
    # AIが資料間の矛盾として挙げたものも関連として記録（どちらが正しいかは決めない）
    for c in data.get("contradictions") or []:
        ids = c.get("directive_ids") or []
        for a, b in zip(ids, ids[1:]):
            db.x("INSERT OR IGNORE INTO directive_links(from_id,to_id,relation,scope,basis,note,status) VALUES"
                 "(?,?,'conflicts_with','corpus','ai',?,'unresolved')", (b, a, str(c.get("description", ""))[:500]))


def engine_eligible() -> list[str]:
    from .engine import ELIGIBLE_KINDS
    return list(ELIGIBLE_KINDS)
