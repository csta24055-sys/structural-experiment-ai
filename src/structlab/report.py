"""解析結果の出力（Markdown / JSON）と処理状況の集計。"""
from __future__ import annotations

import json
import shutil
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classify import keyed_facts
from .db import DB
from .models import CATEGORIES, ORIGINS, VERIFICATIONS, fmt_time
from .safety import sanitize_filename

TZ = timezone.utc  # 日時は UTC で表示


def _iso(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=TZ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError):
        return ""


def cite(r) -> str:
    """出典表記: ファイル名 + ページ番号 / 動画の時刻"""
    parts = [r["source_path"]]
    if r["page"] is not None:
        parts.append(f"p.{r['page']}")
    if r["t_start"] is not None:
        if r["t_end"] is not None and abs((r["t_end"] or 0) - r["t_start"]) >= 1:
            parts.append(f"{fmt_time(r['t_start'])}–{fmt_time(r['t_end'])}")
        else:
            parts.append(fmt_time(r["t_start"]))
    return " ".join(parts)


def current_items(db: DB) -> list[dict]:
    rows = db.q(
        "SELECT i.*, u.unit_type FROM items i JOIN files f ON f.id=i.file_id JOIN units u ON u.id=i.unit_id"
        " WHERE i.file_hash=f.content_hash AND f.status NOT IN ('deleted') AND u.status!='superseded'"
        " ORDER BY i.source_path, i.page, i.t_start, i.id")
    return [dict(r) for r in rows]


def item_json(r: dict) -> dict:
    return {
        "category": r["category"], "statement": r["statement"], "origin": r["origin"],
        "origin_label": ORIGINS.get(r["origin"], r["origin"]),
        "is_instructor_instruction": bool(r["is_instructor_instruction"]),
        "evidence": r["evidence"], "confidence": r["confidence"], "verification": r["verification"],
        "source": {"file": r["source_path"], "page": r["page"], "time_start": r["t_start"], "time_end": r["t_end"],
                   "time_label": fmt_time(r["t_start"]) if r["t_start"] is not None else None,
                   "unit_type": r.get("unit_type")},
        "analyzer": r["analyzer"],
    }


def find_contradictions(db: DB) -> list[dict]:
    facts: dict[str, list[tuple[str, str]]] = defaultdict(list)
    rows = db.q("SELECT u.*, f.path FROM units u JOIN files f ON f.id=u.file_id WHERE u.file_hash=f.content_hash"
                " AND u.status='done' AND f.status!='deleted'")
    for u in rows:
        rec = json.loads(u["extract_json"] or "{}")
        lo = json.loads(u["locator"])
        where = u["path"] + (f" p.{lo['page']}" if "page" in lo else "") + \
            (f" {fmt_time(lo['start'])}" if "start" in lo else "")
        for key, val in keyed_facts(rec.get("text", "")):
            facts[key].append((val, where))
    out = []
    for key, vals in facts.items():
        distinct = sorted({v for v, _ in vals})
        if len(distinct) > 1:
            out.append({"key": key, "values": [{"value": v, "sources": sorted({w for x, w in vals if x == v})}
                                               for v in distinct]})
    return out


def collect_status(db: DB, cfg=None) -> dict:
    files = {r["status"]: r["c"] for r in db.q("SELECT status, COUNT(*) c FROM files GROUP BY status")}
    kinds = {r["kind"]: r["c"] for r in db.q("SELECT kind, COUNT(*) c FROM files WHERE status!='deleted' GROUP BY kind")}
    units = {r["status"]: r["c"] for r in db.q("SELECT u.status, COUNT(*) c FROM units u JOIN files f ON f.id=u.file_id"
                                                " WHERE u.file_hash=f.content_hash GROUP BY u.status")}
    ai = {r["ai_status"]: r["c"] for r in db.q("SELECT u.ai_status, COUNT(*) c FROM units u JOIN files f ON f.id=u.file_id"
                                                " WHERE u.file_hash=f.content_hash GROUP BY u.ai_status")}
    size = db.q1("SELECT COALESCE(SUM(size),0) s, COUNT(*) n FROM files WHERE parent_id IS NULL AND status!='deleted'")
    last_run = db.q1("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    waiting = [dict(r) for r in db.q("SELECT path, status_detail FROM files WHERE status='discovered' ORDER BY path LIMIT 50")]
    problems = [dict(r) for r in db.q(
        "SELECT path, status, status_detail, last_error FROM files WHERE status IN ('failed','partial','skipped',"
        "'unsupported') ORDER BY path LIMIT 200")]
    errors = [dict(r) for r in db.q("SELECT e.*, f.path FROM errors e LEFT JOIN files f ON f.id=e.file_id"
                                    " WHERE resolved=0 ORDER BY e.id DESC LIMIT 50")]
    st = {
        "generated_at": _iso(time.time()),
        "files_by_status": files, "files_by_kind": kinds, "units_by_status": units, "units_by_ai_status": ai,
        "source_files": size["n"], "source_total_mb": round(size["s"] / 1e6, 1),
        "api_usage_total": db.usage_totals(),
        "last_scan": _iso(db.get_kv("last_scan")) if db.get_kv("last_scan") else None,
        "last_run": dict(last_run) if last_run else None,
        "waiting_upload": waiting, "problems": problems,
        "recent_errors": [{k: e[k] for k in ("created_at", "stage", "path", "message")} for e in errors],
    }
    try:
        from .checks.report import summary as _check_summary
        st["checks"] = _check_summary(db)
    except Exception:  # noqa: BLE001
        st["checks"] = {}
    if st["last_run"]:
        st["last_run"]["started_at"] = _iso(st["last_run"]["started_at"])
        st["last_run"]["ended_at"] = _iso(st["last_run"]["ended_at"]) if st["last_run"]["ended_at"] else None
    for e in st["recent_errors"]:
        e["created_at"] = _iso(e["created_at"])
    if cfg is not None:
        try:
            st["disk_free_mb"] = round(shutil.disk_usage(cfg.work_dir).free / 1e6)
        except OSError:
            pass
    return st


STATUS_LABELS = {
    "discovered": "アップロード完了待ち", "ready": "解析待ち/再開待ち", "processing": "処理中", "done": "完了",
    "partial": "一部失敗", "failed": "失敗", "skipped": "スキップ（上限）", "unsupported": "未対応形式",
    "duplicate": "重複（同一内容は解析済み）", "deleted": "削除済み",
}


def status_markdown(st: dict) -> str:
    L = [f"# 処理状況（{st['generated_at']}）", ""]
    L.append(f"- 対象ファイル数: {st['source_files']}（合計 {st['source_total_mb']} MB）")
    L.append(f"- 最終スキャン: {st['last_scan'] or '未実施'}")
    if st.get("last_run"):
        lr = st["last_run"]
        L.append(f"- 最終実行: #{lr['id']} {lr['started_at']} 〜 {lr['ended_at'] or '実行中/異常終了'}（{lr['status']}）")
    if "disk_free_mb" in st:
        L.append(f"- 作業ディスク空き: {st['disk_free_mb']} MB")
    u = st["api_usage_total"]
    L.append(f"- AI使用量（累計）: 呼び出し {u['calls']} 回 / 入力 {u['input_tokens']} / 出力 {u['output_tokens']} トークン / 画像 {u['images']}")
    L += ["", "## ファイル", "| 状態 | 件数 |", "|---|---|"]
    L += [f"| {STATUS_LABELS.get(k, k)} | {v} |" for k, v in sorted(st["files_by_status"].items())]
    L += ["", "## 種類", "| 種類 | 件数 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(st["files_by_kind"].items())]
    L += ["", "## 解析単位（ページ・区間・シート等）", "| 状態 | 件数 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(st["units_by_status"].items())]
    L += ["", "## AI解析", "| 状態 | 件数 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(st["units_by_ai_status"].items())]
    if st["waiting_upload"]:
        L += ["", "## アップロード完了待ち（先頭50件）"] + [f"- {w['path']}: {w['status_detail'] or ''}" for w in st["waiting_upload"]]
    if st["problems"]:
        L += ["", "## 要対応"] + [f"- [{STATUS_LABELS.get(p['status'], p['status'])}] {p['path']}: {p['status_detail'] or p['last_error'] or ''}"
                                  for p in st["problems"]]
    if st.get("checks"):
        from .checks.base import STAGE_LABEL, STATUS_LABEL
        L += ["", "## 各工程のチェック（詳細は checks/checks.md）"]
        for stg, c in st["checks"].items():
            L.append(f"- {STAGE_LABEL.get(stg, stg)}: " + "、".join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in sorted(c.items())))
    if st["recent_errors"]:
        L += ["", "## 最近のエラー（未解決・新しい順）"] + [f"- {e['created_at']} [{e['stage']}] {e['path'] or ''}: {e['message']}"
                                                  for e in st["recent_errors"]]
    return "\n".join(L) + "\n"


def _fmt_item(r: dict) -> str:
    tags = [f"出典: {cite(r)}", ORIGINS.get(r["origin"], r["origin"])]
    if r["verification"] and r["verification"] not in ("not_applicable", "rule_extracted"):
        tags.append(VERIFICATIONS.get(r["verification"], r["verification"]))
    if r["analyzer"] != "local-rules":
        tags.append(f"解析: {r['analyzer']}")
    s = f"- {r['statement']}  〔{' / '.join(tags)}〕"
    if r["evidence"] and r["evidence"] != r["statement"] and r["origin"] != "ai_inference":
        s += f"\n  - 根拠: 「{r['evidence'][:300]}」"
    return s


def write_reports(db: DB, cfg) -> dict[str, Path]:
    out = cfg.results_dir
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    items = current_items(db)

    # ---- 一覧（フォルダ構造・名前・容量・更新日時・形式）----
    inv = [{"path": r["path"], "in_archive": r["parent_id"] is not None, "kind": r["kind"], "ext": r["ext"],
            "size": r["size"], "modified": _iso(r["server_modified"]), "status": r["status"],
            "detail": r["status_detail"]} for r in db.q("SELECT * FROM files ORDER BY path")]
    (out / "inventory.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# ファイル一覧", "", "| パス | 形式 | 容量(MB) | 更新日時 | 状態 |", "|---|---|---:|---|---|"]
    for r in inv:
        md.append(f"| {r['path']} | {r['kind']} | {(r['size'] or 0) / 1e6:.2f} | {r['modified']} | "
                  f"{STATUS_LABELS.get(r['status'], r['status'])}{(' — ' + r['detail']) if r['detail'] else ''} |")
    (out / "inventory.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    paths["inventory"] = out / "inventory.md"

    # ---- 10分類 ----
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in items:
        by_cat[r["category"]].append(r)
    contradictions = find_contradictions(db)
    failed_units = [dict(r) for r in db.q(
        "SELECT u.*, f.path FROM units u JOIN files f ON f.id=u.file_id WHERE u.file_hash=f.content_hash"
        " AND (u.status='failed' OR u.ai_status IN ('failed','failed_final')) AND f.status!='deleted'")]
    gaps = []
    for u in db.q("SELECT u.*, f.path FROM units u JOIN files f ON f.id=u.file_id WHERE u.file_hash=f.content_hash"
                  " AND u.status='done' AND u.ai_status!='done' AND f.status!='deleted' ORDER BY f.path, u.seq"):
        rec = json.loads(u["extract_json"] or "{}")
        if rec.get("needs_ai"):
            lo = json.loads(u["locator"])
            where = u["path"] + (f" p.{lo['page']}" if "page" in lo else "") + \
                (f" {fmt_time(lo['start'])}–{fmt_time(lo['end'])}" if "start" in lo else "")
            gaps.append({"where": where, "reasons": rec["needs_ai"], "ai_status": u["ai_status"]})

    kj: dict[str, Any] = {"generated_at": _iso(time.time()), "categories": {}, "contradictions": contradictions,
                          "unverified_evidence": [], "failed_units": [], "not_ai_analyzed": gaps}
    M = ["# 実験資料の解析結果（階層化）", "",
         f"生成: {_iso(time.time())}", "",
         "> この文書は資料から機械的に抽出・整理したものです。「AIの推測」と「照合できなかった根拠」は必ず原資料で確認してください。",
         "> 読み取れなかった箇所は推測で補っていません（10. 不明点・矛盾 を参照）。", ""]
    for cat, title in CATEGORIES.items():
        rs = by_cat.get(cat, [])
        kj["categories"][cat] = {"title": title, "items": [item_json(r) for r in rs]}
        M += [f"## {title}", ""]
        groups = [
            ("先生の指示として明記されているもの", [r for r in rs if r["is_instructor_instruction"] and r["analyzer"] != "local-rules"]),
            ("資料・発言・画面に明記されている内容（AI整理）",
             [r for r in rs if not r["is_instructor_instruction"] and r["origin"] in ("document_text", "speech", "screen")
              and r["analyzer"] != "local-rules"]),
            ("AIの推測（原資料で未確認）", [r for r in rs if r["origin"] == "ai_inference"]),
            ("原文抜粋（ローカル規則で抽出した候補・AI未使用）", [r for r in rs if r["analyzer"] == "local-rules"
                                               and r["origin"] not in ("unreadable", "system")]),
            ("読み取り不能・処理上の記録", [r for r in rs if r["origin"] in ("unreadable", "system")]),
        ]
        any_written = False
        for gtitle, grs in groups:
            if not grs:
                continue
            any_written = True
            seen, lines = set(), []
            for r in grs:
                k = (r["statement"], cite(r))
                if k in seen:
                    continue
                seen.add(k)
                lines.append(_fmt_item(r))
            limit = 300
            M += [f"### {gtitle}（{len(lines)}件）", *lines[:limit]]
            if len(lines) > limit:
                M.append(f"- …ほか {len(lines) - limit} 件（knowledge.json を参照）")
            M.append("")
        if cat == "unknowns_contradictions":
            unv = [r for r in items if r["verification"] == "evidence_not_found"]
            kj["unverified_evidence"] = [item_json(r) for r in unv]
            kj["failed_units"] = [{"file": u["path"], "locator": json.loads(u["locator"]), "error": u["last_error"] or u["ai_error"],
                                   "status": u["status"], "ai_status": u["ai_status"]} for u in failed_units]
            if contradictions:
                any_written = True
                M.append("### 資料間で値が食い違う可能性（ローカル規則で検出）")
                for c in contradictions:
                    M.append(f"- {c['key']}: " + " / ".join(f"「{v['value']}」({', '.join(v['sources'][:5])})" for v in c["values"]))
                M.append("")
            if unv:
                any_written = True
                M.append(f"### AIが示した根拠を原文で確認できなかった項目（{len(unv)}件・要確認）")
                M += [f"- {r['statement']}  〔出典: {cite(r)} / 根拠とされた文: 「{(r['evidence'] or '')[:150]}」〕" for r in unv[:300]]
                M.append("")
            if failed_units:
                any_written = True
                M.append(f"### 解析に失敗した部分（{len(failed_units)}件・`structlab retry` で再試行可）")
                for u in failed_units[:300]:
                    M.append(f"- {u['path']} {u['locator']}: {u['last_error'] or u['ai_error']}")
                M.append("")
            if gaps:
                any_written = True
                M.append(f"### AI未解析のため内容が未確認の図・画面（{len(gaps)}単位）")
                for g in gaps[:300]:
                    M.append(f"- {g['where']}: {'; '.join(g['reasons'])}（AI: {g['ai_status']}）")
                M.append("")
        if not any_written:
            M += ["（該当なし）", ""]
    (out / "knowledge.json").write_text(json.dumps(kj, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "knowledge.md").write_text("\n".join(M), encoding="utf-8")
    paths["knowledge"] = out / "knowledge.md"

    # ---- ファイル別 JSON と 動画タイムライン ----
    fdir, tdir = out / "files", out / "timelines"
    for d in (fdir, tdir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    per_file: dict[int, list[dict]] = defaultdict(list)
    for r in items:
        per_file[r["file_id"]].append(item_json(r))
    for f in db.q("SELECT * FROM files WHERE status NOT IN ('deleted') ORDER BY path"):
        units = db.q("SELECT * FROM units WHERE file_id=? AND file_hash=? ORDER BY seq, id", (f["id"], f["content_hash"] or ""))
        if not units and not per_file.get(f["id"]):
            continue
        name = f"{f['id']}_{sanitize_filename(f['path'].replace('/', '__'), 120)}"
        doc = {"file": f["path"], "kind": f["kind"], "size": f["size"], "modified": _iso(f["server_modified"]),
               "status": f["status"], "content_hash": f["content_hash"],
               "units": [{"type": u["unit_type"], "locator": json.loads(u["locator"]), "status": u["status"],
                          "ai_status": u["ai_status"], "error": u["last_error"] or u["ai_error"],
                          "extract": {k: v for k, v in json.loads(u["extract_json"] or "{}").items() if k != "ai_text"}}
                         for u in units],
               "items": per_file.get(f["id"], [])}
        (fdir / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tl = db.q("SELECT * FROM timeline WHERE file_id=? AND unit_id IN (SELECT id FROM units WHERE file_id=? AND file_hash=?)"
                  " ORDER BY t_start", (f["id"], f["id"], f["content_hash"] or ""))
        if tl:
            T = [f"# タイムライン: {f['path']}", "", "| 時刻 | 発話 | 対応する画面 | 画面の文字(OCR) | 重要 |", "|---|---|---|---|---|"]
            for t in tl:
                T.append(f"| {fmt_time(t['t_start'])}–{fmt_time(t['t_end'])} | {(t['speech'] or '').replace('|', '／')} | "
                         f"{fmt_time(t['frame_time']) if t['frame_time'] is not None else '-'} "
                         f"{('`' + Path(t['frame_path']).name + '`') if t['frame_path'] else ''} | "
                         f"{(t['screen_text'] or '')[:120].replace('|', '／').replace(chr(10), ' ')} | "
                         f"{('★ ' + (t['reason'] or '')) if t['important'] else ''} |")
            (tdir / f"{name}.md").write_text("\n".join(T) + "\n", encoding="utf-8")

    if cfg.data.get("semantic", {}).get("enabled"):
        from .semantic.report import write_semantic_reports
        paths.update({f"semantic_{k}": v for k, v in write_semantic_reports(db, cfg).items()})
    if cfg.data.get("checks", {}).get("enabled", True):
        from .checks.report import write_checks_report
        paths["checks"] = write_checks_report(db, cfg)

    st = collect_status(db, cfg)
    (out / "status.md").write_text(status_markdown(st), encoding="utf-8")
    (out / "status.json").write_text(json.dumps(st, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    paths["status"] = out / "status.md"
    return paths
