"""結果がどの工程を経て作られたかの追跡（教授などのフィードバックの原因箇所の確認用）。

指示1件や文章調整1件について、工程ごとの材料・判断・チェック結果を順に並べる:
  資料の読み取り（抽出・文字起こし・画面）→ 意味理解（場面の解釈の版・全体理解）→ 指示抽出（分類・照合）
  → 計算（Excelの再計算）→ 文章生成（スタイル適用前後）
原因の判定はしない（どの工程の問題かは、人が追跡結果を見て feedback に記録する）。
"""
from __future__ import annotations

import json

from .db import DB
from .models import fmt_time

STAGES = {"extraction": "資料の読み取り", "understanding": "意味理解", "directives": "指示抽出",
          "calculation": "計算", "writing": "文章生成", "unknown": "不明"}


def _checks(db: DB, where: str, args) -> list[dict]:
    return [{"stage": r["stage"], "type": r["check_type"], "status": r["status"], "detail": r["detail"]}
            for r in db.q(f"SELECT * FROM checks WHERE {where} ORDER BY stage, id", args)]


def trace_directive(db: DB, did: int) -> dict:
    d = db.q1("SELECT * FROM directives WHERE id=?", (did,))
    if d is None:
        raise KeyError(did)
    f = db.q1("SELECT * FROM files WHERE id=?", (d["file_id"],))
    u = db.q1("SELECT * FROM units WHERE id=?", (d["unit_id"],)) if d["unit_id"] else None
    out: dict = {"target": {"type": "directive", "id": did, "content": d["content"], "class": d["directive_class"],
                            "source": d["source_path"], "time": fmt_time(d["t_start"]) if d["t_start"] is not None else None,
                            "page": d["page"]}}
    ext = {"file": f["path"] if f else None, "file_hash": f["content_hash"] if f else None}
    if u:
        rec = json.loads(u["extract_json"] or "{}")
        ext.update({"unit_id": u["id"], "unit_type": u["unit_type"], "locator": json.loads(u["locator"]),
                    "extract_status": u["status"], "extract_summary": {k: v for k, v in (rec.get("data") or {}).items()
                                                                        if k in ("transcriber", "n_speech", "n_keyframes",
                                                                                 "ocr", "chars", "n_tables", "n_cells")},
                    "needs_ai": rec.get("needs_ai")})
        if d["t_start"] is not None:
            ext["timeline_near"] = [{"t": fmt_time(r["t_start"]), "speech": r["speech"], "frame": r["frame_path"],
                                     "frame_time": fmt_time(r["frame_time"]) if r["frame_time"] is not None else None}
                                    for r in db.q("SELECT * FROM timeline WHERE file_id=? AND t_start BETWEEN ? AND ? "
                                                  "ORDER BY t_start", (d["file_id"], d["t_start"] - 15, d["t_start"] + 15))]
        ext["checks"] = _checks(db, "stage='extraction' AND unit_id=?", (u["id"],))
    out["extraction"] = ext
    und: dict = {"doc_id": d["doc_id"]}
    doc = db.q1("SELECT * FROM sem_docs WHERE id=?", (d["doc_id"],))
    if doc:
        und["mode"] = doc["mode"]
        und["scene_passes"] = [{"pass": s["pass"], "meaning": json.loads(s["analysis_json"] or "{}").get("meaning"),
                                "changes": json.loads(s["changes_json"] or "null")}
                               for s in db.q("SELECT * FROM sem_scenes WHERE doc_id=? AND unit_id=? ORDER BY pass",
                                             (d["doc_id"], d["unit_id"]))]
        g = db.q1("SELECT version, data_json FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC"
                  " LIMIT 1", (d["doc_id"],))
        if g:
            gd = json.loads(g["data_json"])
            und["global_version"] = g["version"]
            und["global_changes"] = gd.get("changes")
        und["checks"] = _checks(db, "stage='understanding' AND file_id=?", (d["file_id"],))
    out["understanding"] = und
    links = [dict(r) for r in db.q("SELECT * FROM directive_links WHERE from_id=? OR to_id=?", (did, did))]
    out["directives"] = {"extracted_by": d["extracted_by"], "origin": d["origin"], "speaker": d["speaker"],
                         "evidence": d["speech"] or d["evidence"], "certainty": d["certainty"],
                         "verification": d["verification"], "verification_notes": json.loads(d["verification_notes"] or "[]"),
                         "effective": d["effective"], "links": links,
                         "checks": _checks(db, "stage='directives' AND file_id=?", (d["file_id"],)),
                         "reverify": _checks(db, "stage='reverify' AND target_type='directive' AND target_id=?", (str(did),)),
                         "revisions": [dict(r) for r in db.q("SELECT * FROM revisions WHERE target_type='directive' AND "
                                                              "target_id=?", (str(did),))]}
    if f is not None and f["kind"] == "excel":
        out["calculation"] = {"checks": _checks(db, "stage='extraction' AND file_id=? AND check_type LIKE 'excel%'", (f["id"],))}
    out["feedback"] = [dict(r) for r in db.q("SELECT * FROM feedback WHERE target_type='directive' AND target_id=?", (str(did),))]
    return out


def trace_style(db: DB, rid: int) -> dict:
    r = db.q1("SELECT * FROM style_runs WHERE id=?", (rid,))
    if r is None:
        raise KeyError(rid)
    return {"target": {"type": "style_run", "id": rid, "section": r["section"]},
            "writing": {"status": r["status"], "original": r["original"], "styled": r["styled"], "final": r["final"],
                        "rules": json.loads(r["rules_json"] or "[]"), "verification": json.loads(r["verification_json"] or "{}"),
                        "analyzer": r["analyzer"]},
            "feedback": [dict(x) for x in db.q("SELECT * FROM feedback WHERE target_type='style_run' AND target_id=?", (str(rid),))]}
