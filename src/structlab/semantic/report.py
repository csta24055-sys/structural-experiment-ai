"""意味理解層の出力（results/semantic/）と解析網羅率。

出力一覧:
  scenes/*.md|json        1. 資料別の各場面の詳細な意味解析
  understanding/*.md|json 2-3. 資料全体の意味理解・説明内容・作業手順
  directives.md           4. 指示・禁止事項・注意事項の一覧（網羅性重視・分類別）
  directives.json|csv     5. 各指示の分類・条件・根拠（全項目）
  requirements.md         6. 複数資料を統合した要件一覧
  experiment.md|json      7. 実験全体の目的・手順・提出物の統合理解
  unconfirmed.md          8. 未確認事項
  conflicts.md            9. 矛盾する指示
  changes.md             10. 訂正・補足・例外・条件変更
  failures.md            11. 解析に失敗した箇所
  coverage.md|json       12. 解析網羅率と検証状況
"""
from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

from ..db import DB
from ..models import fmt_time
from ..safety import sanitize_filename
from .corpus import best_docs
from .material import build_scenes, is_time_based
from .rules import DIRECTIVE_CLASSES

VER_LABEL = {
    "rule_extracted": "規則で原文から抽出（文脈未考慮）", "candidate": "規則候補", "corroborated": "AI抽出と一致",
    "omission_candidate": "見落とし候補（AI未抽出・未照合）", "verified_local": "根拠を原文で機械照合済み",
    "verified": "第3回照合で確認済み", "revised": "第3回照合で修正済み", "rejected": "第3回照合で指示ではないと判断",
    "class_mismatch_unresolved": "分類が原文と食い違い（未解決）", "needs_review": "要確認",
    "found_in_verify": "第3回照合で新たに発見（未照合）", "unverified": "未照合",
}
EFF_LABEL = {"effective": "有効", "superseded": "訂正により無効", "unresolved_conflict": "未解決の矛盾あり",
             "merged": "AI抽出に統合", "dismissed": "指示ではないと判断", "rejected": "照合で否定"}
REL_LABEL = {"correction_of": "訂正", "supplement_to": "補足", "exception_to": "例外", "condition_change_of": "条件変更",
             "conflicts_with": "矛盾", "same_as": "同一・繰り返し", "corroborated_by": "規則候補と一致"}
ORIGIN_LABEL = {"explicit": "明示", "ai_inference": "AIの推論"}
SPEAKER_LABEL = {"teacher_confirmed": "先生（利用者が確認済み）", "teacher_inferred": "先生とAIが推定（未確認）",
                 "unknown": "発言者未確認（先生の指示と断定していない）", "document": "資料の記述", "screen": "画面の表示",
                 "student": "学生", "other": "その他"}
OPEN_VER = ("needs_review", "omission_candidate", "class_mismatch_unresolved", "found_in_verify", "unverified")


def _name(path: str, fid: int) -> str:
    return f"{fid}_{sanitize_filename(path.replace('/', '__'), 120)}"


def where(d) -> str:
    if d["t_start"] is not None:
        return fmt_time(d["t_start"]) + (f"–{fmt_time(d['t_end'])}" if d["t_end"] not in (None, d["t_start"]) else "")
    if d["page"]:
        return f"p.{d['page']}"
    return ""


def directive_record(db: DB, d) -> dict:
    links = []
    for l in db.q("SELECT * FROM directive_links WHERE from_id=? OR to_id=?", (d["id"], d["id"])):
        other = l["to_id"] if l["from_id"] == d["id"] else l["from_id"]
        links.append({"relation": l["relation"], "direction": "to" if l["from_id"] == d["id"] else "from",
                      "other_id": other, "scope": l["scope"], "basis": l["basis"], "status": l["status"], "note": l["note"]})
    extra = json.loads(d["extra_json"] or "{}")
    return {
        "id": d["id"], "content": d["content"], "class": d["directive_class"],
        "class_label": DIRECTIVE_CLASSES[d["directive_class"]],
        "base_strength": d["base_strength"], "condition": d["condition"],
        "target_task": d["target_task"], "deliverable": d["deliverable"], "audience": d["audience"],
        "source": d["source_path"], "time_start": d["t_start"], "time_end": d["t_end"],
        "time_label": where(d), "page": d["page"],
        "screen": d["screen"], "operation": d["operation"], "speech": d["speech"], "evidence": d["evidence"],
        "context_before": d["context_before"], "context_after": d["context_after"],
        "certainty": d["certainty"], "unconfirmed": d["unconfirmed"],
        "origin": d["origin"], "origin_label": ORIGIN_LABEL.get(d["origin"], d["origin"]),
        "speaker": d["speaker"] or "unknown",
        "speaker_label": SPEAKER_LABEL.get(d["speaker"] or "unknown", d["speaker"]),
        "speaker_basis": extra.get("speaker_basis", ""),
        "extracted_by": d["extracted_by"], "marker": d["marker"], "discourse": json.loads(d["discourse"] or "[]"),
        "verification": d["verification"], "verification_label": VER_LABEL.get(d["verification"], d["verification"]),
        "verification_notes": json.loads(d["verification_notes"] or "[]"),
        "effective": d["effective"], "effective_label": EFF_LABEL.get(d["effective"], d["effective"]),
        "relations": links, "relation_unresolved": extra.get("relation_unresolved", []),
    }


def _fmt_dir(r: dict) -> str:
    tags = [f"出典: {r['source']} {r['time_label']}".rstrip(), r["origin_label"], f"発言者: {r['speaker_label']}",
            f"確実性: {r['certainty']}", r["verification_label"]]
    if r["effective"] != "effective":
        tags.append(r["effective_label"])
    s = f"- **#{r['id']}** {r['content']}"
    if r["class"] == "conditional":
        s += f"  〔条件: {r['condition'] or '不明'} → {DIRECTIVE_CLASSES.get(r['base_strength'] or '', '強さ不明')}〕"
    elif r["condition"]:
        s += f"  〔条件: {r['condition']}〕"
    s += f"\n  - {' / '.join(tags)}"
    if r["target_task"]:
        s += f"\n  - 対象作業: {r['target_task']}" + (f" ／ 提出物: {r['deliverable']}" if r["deliverable"] else "")
    if r["speech"] or r["evidence"]:
        s += f"\n  - 根拠: 「{(r['speech'] or r['evidence'])[:300]}」"
    if r["screen"]:
        s += f"\n  - 画面: {r['screen'][:200]}"
    if r["operation"]:
        s += f"\n  - 操作: {r['operation'][:200]}"
    if r["context_before"] or r["context_after"]:
        s += f"\n  - 前後: 〈前〉{r['context_before'][:150]} 〈後〉{r['context_after'][:150]}"
    if r["relations"]:
        s += "\n  - 関連: " + "、".join(f"{REL_LABEL.get(x['relation'], x['relation'])}"
                                       f"{'→' if x['direction'] == 'to' else '←'}#{x['other_id']}"
                                       f"{'（未解決）' if x['status'] == 'unresolved' else ''}" for x in r["relations"])
    if r["unconfirmed"]:
        s += f"\n  - 未確認: {r['unconfirmed'][:300]}"
    return s


# ---------------- 網羅率 ----------------
def _intervals(scenes, ok_units: set[int]) -> list[tuple[float, float]]:
    gaps = []
    for sc in scenes:
        if sc.unit_id not in ok_units and sc.t_start is not None:
            if gaps and abs(gaps[-1][1] - sc.t_start) < 1e-6:
                gaps[-1] = (gaps[-1][0], sc.t_end)
            else:
                gaps.append((sc.t_start, sc.t_end))
    return gaps


def coverage_for(db: DB, frow, doc) -> dict:
    scenes = build_scenes(db, frow)
    tb = is_time_based(frow["kind"])
    steps = {}
    if doc is not None:
        for r in db.q("SELECT unit_id, step, status FROM sem_steps WHERE doc_id=?", (doc["id"],)):
            key = "reinterpret" if r["step"].startswith("reinterpret") else r["step"]
            if key.startswith("verify:"):
                continue
            steps.setdefault(key, {}).setdefault(r["unit_id"], set()).add(r["status"])
    total = sum(sc.duration for sc in scenes) if tb else len(scenes)

    def measure(units_ok: set[int]) -> dict:
        if tb:
            done = sum(sc.duration for sc in scenes if sc.unit_id in units_ok)
            return {"done": round(done, 1), "total": round(total, 1), "unit": "秒",
                    "ratio": round(done / total, 4) if total else None,
                    "missing": [f"{fmt_time(a)}–{fmt_time(b)}" for a, b in _intervals(scenes, units_ok)]}
        done = sum(1 for sc in scenes if sc.unit_id in units_ok)
        return {"done": done, "total": total, "unit": "単位", "ratio": round(done / total, 4) if total else None,
                "missing": [sc.label for sc in scenes if sc.unit_id not in units_ok]}

    out: dict = {"file": frow["path"], "kind": frow["kind"], "mode": doc["mode"] if doc else None,
                 "doc_status": doc["status"] if doc else "未実施", "scenes": len(scenes)}
    m = {}
    if tb:
        audio = [sc for sc in scenes if sc.transcribed is not None]
        video = [sc for sc in scenes if sc.visual is not None]
        m["文字起こし"] = {**measure({sc.unit_id for sc in audio if sc.transcribed}),
                         "total": round(sum(sc.duration for sc in audio), 1)}
        if audio:
            d = sum(sc.duration for sc in audio)
            m["文字起こし"]["ratio"] = round(m["文字起こし"]["done"] / d, 4) if d else None
        else:
            m["文字起こし"]["note"] = "音声なし"
        m["画面取得"] = measure({sc.unit_id for sc in video if sc.visual})
    for key, label in (("rules", "規則による指示抽出"), ("scene1", "第1回 局所理解"), ("reinterpret", "相互照合（再解釈）"),
                       ("directives", "第2回 指示の重点抽出"), ("verify", "第3回 照合")):
        if doc is None or (doc["mode"] == "local" and key not in ("rules", "verify")):
            continue
        ok = {u for u, st in steps.get(key, {}).items() if st & {"ok", "skipped"}}
        failed = {u for u, st in steps.get(key, {}).items() if st & {"failed", "failed_final"} and u not in ok}
        m[label] = {**measure(ok), "failed_units": len(failed)}
    out["metrics"] = m
    if doc is not None:
        vc = {r["verification"]: r["c"] for r in db.q(
            "SELECT verification, COUNT(*) c FROM directives WHERE doc_id=? AND effective NOT IN ('merged') "
            "GROUP BY verification", (doc["id"],))}
        out["directive_verification"] = vc
        out["directives_open"] = sum(v for k, v in vc.items() if k in OPEN_VER)
        out["rounds"] = doc["round"]
        out["converged"] = doc["converged"]
        out["note"] = doc["note"]
    return out


def collect_coverage(db: DB) -> list[dict]:
    from .engine import ELIGIBLE_KINDS
    res = []
    best = {d["file_id"]: d for d in best_docs(db)}
    for f in db.q("SELECT * FROM files WHERE status NOT IN ('deleted','duplicate') ORDER BY path"):
        if f["kind"] not in ELIGIBLE_KINDS:
            continue
        doc = best.get(f["id"])
        c = coverage_for(db, f, doc)
        ai = db.q1("SELECT * FROM sem_docs WHERE file_id=? AND file_hash=? AND mode='ai' ORDER BY id DESC LIMIT 1",
                   (f["id"], f["content_hash"] or ""))
        c["ai_progress"] = {"step": ai["step"], "status": ai["status"], "note": ai["note"]} if ai else None
        c["file_status"] = f["status"]
        res.append(c)
    return res


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


# ---------------- 出力 ----------------
def write_semantic_reports(db: DB, cfg) -> dict[str, Path]:
    out = cfg.results_dir / "semantic"
    if out.exists():
        shutil.rmtree(out)
    (out / "scenes").mkdir(parents=True)
    (out / "understanding").mkdir()
    docs = best_docs(db)
    doc_ids = [d["id"] for d in docs]
    qs = ",".join("?" * len(doc_ids)) or "NULL"
    dirs = db.q(f"SELECT * FROM directives WHERE doc_id IN ({qs}) ORDER BY source_path, t_start, page, id", doc_ids)
    recs = [directive_record(db, d) for d in dirs]
    by_id = {r["id"]: r for r in recs}

    # 1-3. 資料別
    for d in docs:
        frow = db.q1("SELECT * FROM files WHERE id=?", (d["file_id"],))
        name = _name(d["path"], d["file_id"])
        scenes = build_scenes(db, frow)
        S = [f"# 場面ごとの意味解析: {d['path']}", "", f"解析モード: {'AI意味理解' if d['mode'] == 'ai' else '規則のみ（AI未使用）'}", ""]
        sj = []
        for sc in scenes:
            hist = db.q("SELECT * FROM sem_scenes WHERE doc_id=? AND unit_id=? ORDER BY pass", (d["id"], sc.unit_id))
            final = json.loads(hist[-1]["analysis_json"]) if hist else None
            changes = [json.loads(h["changes_json"]) for h in hist[1:] if h["changes_json"] and json.loads(h["changes_json"])]
            sj.append({"scene": sc.seq, "where": sc.where(), "unit_id": sc.unit_id, "passes": len(hist),
                       "analysis": final, "reinterpret_changes": changes,
                       "transcribed": sc.transcribed, "visual": sc.visual,
                       "source_lines": [{"origin": ln.origin, "t": ln.t_start, "page": ln.page, "text": ln.text}
                                        for ln in sc.lines]})
            S.append(f"## 場面 #{sc.seq} {sc.where()}")
            if final:
                for k, lbl in (("speech", "話している内容"), ("screen", "画面"), ("operations", "操作・実験"),
                               ("meaning", "意味"), ("purpose", "目的（なぜ）"), ("relation_prev", "直前との関係"),
                               ("relation_next", "直後との関係"), ("audience", "対象者"), ("task", "対象の作業")):
                    S.append(f"- **{lbl}**: {final.get(k, '')}")
                if final.get("deliverables"):
                    S.append(f"- **関係する提出物**: {'、'.join(final['deliverables'])}")
                for kp in final.get("key_points", []):
                    S.append(f"  - 要点: {kp.get('text')}（{fmt_time(kp['time_sec']) if kp.get('time_sec') is not None else '-'}／{kp.get('basis')}）")
                for x in final.get("discourse", []):
                    S.append(f"  - {x.get('type')}: {x.get('description')}「{x.get('evidence', '')[:120]}」")
                for x in final.get("uncertain", []):
                    S.append(f"  - 不確か: {x}")
                for x in final.get("unreadable", []):
                    S.append(f"  - 読み取り不能: {x}")
                if changes:
                    S.append(f"- **全体理解による再解釈**: {json.dumps(changes, ensure_ascii=False)[:800]}")
            else:
                S.append("- 意味解析: 未実施" + ("（AI未使用）" if d["mode"] == "local" else "（失敗または未処理。failures.md 参照）"))
            S.append("- 元の発話・画面（時刻同期）:")
            for ln in sc.lines[:200]:
                t = f"[{fmt_time(ln.t_start)}] " if ln.t_start is not None else ""
                S.append(f"  - {t}{ {'speech': '発話', 'screen': '画面', 'document': '資料'}[ln.origin]}: {ln.text[:200]}")
            S.append("")
        (out / "scenes" / f"{name}.md").write_text("\n".join(S), encoding="utf-8")
        (out / "scenes" / f"{name}.json").write_text(json.dumps(sj, ensure_ascii=False, indent=1), encoding="utf-8")
        vers = db.q("SELECT * FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version", (d["id"],))
        U = [f"# 資料全体の意味理解: {d['path']}", ""]
        if not vers:
            U += ["AIによる全体理解は未生成です（AI未使用、または未完了）。規則による指示一覧は directives.md を参照してください。"]
        else:
            g = json.loads(vers[-1]["data_json"])
            U += [f"版: {vers[-1]['version']}（全 {len(vers)} 版）／ 再解釈ラウンド: {d['round']} ／ 収束: "
                  f"{'はい' if d['converged'] == 1 else ('いいえ（要確認）' if d['converged'] == 0 else '—')}", ""]
            for k, lbl in (("theme", "テーマ"), ("purpose", "動画・資料の目的"), ("experiment_purpose", "実験・提出物の目的"),
                           ("message", "伝えようとしている内容"), ("logic_structure", "説明の論理構造"), ("conclusion", "結論")):
                U += [f"## {lbl}", str(g.get(k, "")), ""]
            U += ["## 詳しい説明（資料を見ていない人向け）", str(g.get("explanation", "")), "", "## 説明の流れ"]
            U += [f"{x.get('order')}. {x.get('description')}（場面 {x.get('scene_seqs')}）" for x in g.get("flow", [])]
            U += ["", "## 作業手順と依存関係"]
            U += [f"{x.get('step')}. {x.get('action')} — 目的: {x.get('purpose')}（前提: {x.get('depends_on')}／場面 {x.get('scene_seqs')}）"
                  for x in g.get("procedure", [])]
            U += ["", "## 各場面の役割"] + [f"- 場面 {x.get('scene_seq')}: {x.get('role')}" for x in g.get("scene_roles", [])]
            for k, lbl in (("important_points", "重要事項"), ("repeated_cautions", "繰り返し強調された注意"),
                           ("final_requirements", "最終的に守るべき要件")):
                U += ["", f"## {lbl}"] + [f"- {x.get('text')}（場面 {x.get('scene_seqs')}）" for x in g.get(k, [])]
            U += ["", "## 訂正・補足・例外・条件変更"] + [
                f"- [{x.get('type')}] {x.get('description')}（前: 場面 {x.get('earlier_scene')} → 後: 場面 {x.get('later_scene')}）"
                f"「{x.get('evidence', '')[:150]}」" for x in g.get("changes", [])]
            U += ["", "## 一見矛盾する説明"] + [
                f"- {x.get('description')}（場面 {x.get('scene_seqs')}）: "
                + (f"整合の説明（AIの解釈・要確認）: {x.get('resolution')}「{x.get('resolution_evidence', '')[:150]}」"
                   if x.get("resolved") else "**未解決**") for x in g.get("apparent_contradictions", [])]
            for k, lbl in (("understand_goals", "学生に理解してほしいこと"), ("action_goals", "学生に実行してほしいこと"),
                           ("open_questions", "未解決の疑問")):
                U += ["", f"## {lbl}"] + [f"- {x}" for x in g.get(k, [])]
            if g.get("_failed_scenes"):
                U += ["", f"## 注意: 局所理解に失敗した場面 {g['_failed_scenes']} を含まない理解です"]
        (out / "understanding" / f"{name}.md").write_text("\n".join(U) + "\n", encoding="utf-8")
        (out / "understanding" / f"{name}.json").write_text(json.dumps(
            [{"version": v["version"], "analyzer": v["analyzer"], "data": json.loads(v["data_json"])} for v in vers],
            ensure_ascii=False, indent=1), encoding="utf-8")

    # 4-5. 指示一覧
    shown = [r for r in recs if r["effective"] not in ("merged", "dismissed", "rejected")]
    D = ["# 指示・禁止事項・注意事項の一覧", "",
         "> 網羅性を重視し、規則で拾った候補も含めて掲載しています。「要確認」「見落とし候補」「AIの推論」は必ず元の資料・動画の該当時刻で確認してください。",
         f"> 掲載 {len(shown)} 件（AI抽出と一致して統合した規則候補・照合で否定されたものは除外。directives.json には全件）",
         "> 発言者: 話者識別は行っていません。「先生（利用者が確認済み）」以外の音声の指示は、先生の指示と断定していません。", ""]
    for cls, lbl in DIRECTIVE_CLASSES.items():
        rs = [r for r in shown if r["class"] == cls]
        D += [f"## {lbl}（{len(rs)}件）", *([_fmt_dir(r) for r in rs] or ["（該当なし）"]), ""]
    excluded = [r for r in recs if r["effective"] in ("dismissed", "rejected")]
    if excluded:
        D += [f"## 参考: 指示ではないと判断したもの（{len(excluded)}件）"] + [_fmt_dir(r) for r in excluded]
    (out / "directives.md").write_text("\n".join(D) + "\n", encoding="utf-8")
    (out / "directives.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["id", "class_label", "content", "condition", "base_strength", "target_task", "deliverable", "audience", "source",
            "time_label", "page", "speech", "screen", "operation", "context_before", "context_after", "certainty",
            "origin_label", "extracted_by", "verification_label", "effective_label", "unconfirmed"]
    with open(out / "directives.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in recs:
            w.writerow([r[c] for c in cols])

    # 6-7. 統合理解
    cr = db.q1("SELECT * FROM sem_understanding WHERE scope='corpus' ORDER BY id DESC LIMIT 1")
    corpus = json.loads(cr["data_json"]) if cr else None
    E = ["# 実験全体の統合理解（複数資料を横断）", ""]
    if corpus:
        E += [f"対象資料: {', '.join(corpus.get('_sources', []))}", ""]
        for k, lbl in (("experiment_purpose", "実験全体の目的"), ("experiment_content", "実験全体の内容"),
                       ("data_processing", "データの処理方法"), ("excel_method", "Excelの作成方法"),
                       ("report_method", "レポートの作成方法"), ("explanation", "詳しい説明")):
            E += [f"## {lbl}", str(corpus.get(k, "")), ""]
        E += ["## 手順"] + [f"{x.get('step')}. {x.get('action')}（根拠: {', '.join(x.get('sources', []))}／前提: {x.get('depends_on')}）"
                          for x in corpus.get("procedure", [])]
        E += ["", "## 作業の順序"] + [f"{i}. {x}" for i, x in enumerate(corpus.get("work_order", []), 1)]
        E += ["", "## 作業の依存関係"] + [f"- {x.get('before')} → {x.get('after')}（{x.get('reason')}）" for x in corpus.get("dependencies", [])]
        E += ["", "## 最終的に提出するもの"]
        for x in corpus.get("deliverables", []):
            E += [f"- **{x.get('name')}**（根拠: {', '.join(x.get('sources', []))}／指示 {['#' + str(i) for i in x.get('directive_ids', [])]}）"]
            E += [f"  - {q}" for q in x.get("requirements", [])]
        E += ["", "## 未解決の疑問"] + [f"- {x}" for x in corpus.get("open_questions", [])]
        if corpus.get("_dropped_invalid_ids"):
            E += ["", f"※AIが存在しない指示番号を参照したため除外: {corpus['_dropped_invalid_ids']}"]
    else:
        E += ["AIによる資料横断の統合理解は未生成です（AI未使用、または全資料の意味理解が未完了）。",
              "以下は規則による資料横断の集計です（意味の統合理解ではありません）。", ""]
    groups = defaultdict(set)
    for l in db.q("SELECT * FROM directive_links WHERE scope='corpus' AND relation='same_as'"):
        groups[l["to_id"]].add(l["from_id"])
    (out / "experiment.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "experiment.md").write_text("\n".join(E) + "\n", encoding="utf-8")

    R = ["# 複数資料を統合した要件一覧", ""]
    if corpus:
        R += ["## 要件（AIによる統合・根拠の指示番号付き）"]
        for x in corpus.get("requirements", []):
            R += [f"- {x.get('text')}（根拠: {', '.join(x.get('sources', []))}）"]
            R += [f"  - #{i} {by_id[i]['content']}（{by_id[i]['source']} {by_id[i]['time_label']}）" for i in x.get("directive_ids", [])
                  if i in by_id]
        R += ["", "## 複数資料に共通する指示（AI）"] + [f"- {x.get('summary')}: {['#' + str(i) for i in x.get('directive_ids', [])]}"
                                               for x in corpus.get("common_directive_groups", [])]
        R += [""]
    R += ["## 必須・禁止・条件付きの指示（全資料・規則とAIの抽出結果）"]
    for cls in ("mandatory", "prohibited", "conditional"):
        rs = [r for r in shown if r["class"] == cls and r["effective"] in ("effective", "unresolved_conflict")]
        R += [f"### {DIRECTIVE_CLASSES[cls]}（{len(rs)}件）"] + [_fmt_dir(r) for r in rs]
    R += ["", "## 複数資料に共通する指示（規則による類似検出）"]
    for k, v in groups.items():
        if k in by_id:
            R += [f"- #{k} {by_id[k]['content']}（{by_id[k]['source']}）"] + [
                f"  - 同じ指示: #{i} {by_id[i]['content']}（{by_id[i]['source']} {by_id[i]['time_label']}）" for i in v if i in by_id]
    (out / "requirements.md").write_text("\n".join(R) + "\n", encoding="utf-8")

    # 8. 未確認事項
    N = ["# 未確認事項", "", "## 照合で確定していない指示"]
    N += [_fmt_dir(r) for r in shown if r["verification"] in OPEN_VER] or ["（なし）"]
    N += ["", "## AIの推論による指示（明示されていない）"] + ([_fmt_dir(r) for r in shown if r["origin"] == "ai_inference"] or ["（なし）"])
    N += ["", "## 確実性が低い・判断不能な指示"] + ([_fmt_dir(r) for r in shown if r["certainty"] == "low" or r["class"] == "other"] or ["（なし）"])
    N += ["", "## 関係先を特定できない訂正・補足・例外・条件変更"]
    N += [f"- #{r['id']} {r['content']}: {'、'.join(r['relation_unresolved'])}" for r in shown if r["relation_unresolved"]] or ["（なし）"]
    for d in docs:
        g = db.q1("SELECT data_json FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC LIMIT 1", (d["id"],))
        if g:
            g = json.loads(g["data_json"])
            if g.get("open_questions"):
                N += ["", f"## 資料全体の疑問: {d['path']}"] + [f"- {x}" for x in g["open_questions"]]
        for s in db.q("SELECT s.* FROM sem_scenes s WHERE doc_id=? AND pass=(SELECT MAX(pass) FROM sem_scenes t WHERE "
                      "t.doc_id=s.doc_id AND t.unit_id=s.unit_id)", (d["id"],)):
            a = json.loads(s["analysis_json"] or "{}")
            for x in (a.get("uncertain") or []) + [f"読み取り不能: {y}" for y in a.get("unreadable") or []]:
                N.append(f"- {d['path']} {fmt_time(s['t_start']) if s['t_start'] is not None else ('p.' + str(s['page']) if s['page'] else '')}: {x}")
    if corpus and corpus.get("open_questions"):
        N += ["", "## 資料横断の疑問"] + [f"- {x}" for x in corpus["open_questions"]]
    (out / "unconfirmed.md").write_text("\n".join(N) + "\n", encoding="utf-8")

    # 9. 矛盾
    from ..report import find_contradictions
    C = ["# 矛盾する指示・説明", "", "> どちらが正しいかはシステムでは決めていません。双方の根拠を元資料で確認してください。", ""]
    for scope, lbl in (("document", "同じ資料の中"), ("corpus", "資料間")):
        pairs: dict[frozenset, list] = {}
        for l in db.q("SELECT * FROM directive_links WHERE relation='conflicts_with' AND scope=?", (scope,)):
            pairs.setdefault(frozenset((l["from_id"], l["to_id"])), []).append(l)
        C += [f"## {lbl}（{len(pairs)}件）"]
        for ls in pairs.values():
            l = ls[0]
            a, b = by_id.get(l["to_id"]), by_id.get(l["from_id"])
            if a and b:
                C += [f"- {' ／ '.join(x['note'] for x in ls)}（判定: {'・'.join(sorted({x['basis'] for x in ls}))}）", "  " + _fmt_dir(a).replace("\n", "\n  "), "  " + _fmt_dir(b).replace("\n", "\n  ")]
        C.append("")
    C += ["## 資料全体の理解で未解決とされた説明の食い違い（AI）"]
    for d in docs:
        g = db.q1("SELECT data_json FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC LIMIT 1", (d["id"],))
        for x in (json.loads(g["data_json"]).get("apparent_contradictions", []) if g else []):
            if not x.get("resolved"):
                C.append(f"- {d['path']}: {x.get('description')}（場面 {x.get('scene_seqs')}）")
    if corpus:
        C += ["", "## 資料横断の矛盾（AI）"] + [f"- {x.get('description')}: {['#' + str(i) for i in x.get('directive_ids', [])]}"
                                          for x in corpus.get("contradictions", [])]
    C += ["", "## 値の食い違い（提出期限・枚数など、ローカル規則）"]
    C += [f"- {c['key']}: " + " / ".join(f"「{v['value']}」({', '.join(v['sources'][:5])})" for v in c["values"])
          for c in find_contradictions(db)] or ["（なし）"]
    (out / "conflicts.md").write_text("\n".join(C) + "\n", encoding="utf-8")

    # 10. 訂正・補足・例外・条件変更
    H = ["# 訂正・補足・例外・条件変更", ""]
    for rel in ("correction_of", "supplement_to", "exception_to", "condition_change_of"):
        ls = db.q(f"SELECT * FROM directive_links WHERE relation=? AND from_id IN (SELECT id FROM directives WHERE doc_id IN ({qs}))",
                  [rel, *doc_ids])
        H += [f"## {REL_LABEL[rel]}（{len(ls)}件）"]
        for l in ls:
            a, b = by_id.get(l["from_id"]), by_id.get(l["to_id"])
            if a and b:
                st = {"resolved_explicit": "原文に明示（前の指示は無効）", "unresolved": "要確認（前の指示は無効にしていない）"}.get(l["status"], "")
                H += [f"- 後: #{a['id']} {a['content']}（{a['source']} {a['time_label']}）",
                      f"  - 前: #{b['id']} {b['content']}（{b['time_label']}／{b['effective_label']}）",
                      f"  - {st} {l['note']}"]
        H.append("")
    for d in docs:
        g = db.q1("SELECT data_json FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC LIMIT 1", (d["id"],))
        if g and json.loads(g["data_json"]).get("changes"):
            H += [f"## 全体理解で把握した変更（AI）: {d['path']}"] + [
                f"- [{x.get('type')}] {x.get('description')}（場面 {x.get('earlier_scene')}→{x.get('later_scene')}）「{x.get('evidence', '')[:150]}」"
                for x in json.loads(g["data_json"])["changes"]]
    (out / "changes.md").write_text("\n".join(H) + "\n", encoding="utf-8")

    # 11. 失敗
    F = ["# 解析に失敗した箇所", ""]
    rows = db.q("SELECT s.*, f.path, d.mode FROM sem_steps s JOIN sem_docs d ON d.id=s.doc_id JOIN files f ON f.id=d.file_id "
                "WHERE s.status IN ('failed','failed_final') ORDER BY f.path, s.step")
    F += ["## 意味理解の段階"] + [f"- {r['path']} [{r['step']}] 単位/指示 {r['unit_id']}: {r['detail']}"
                               f"（{'再試行上限' if r['status'] == 'failed_final' else '次回再試行'}）" for r in rows] or ["（なし）"]
    rows = db.q("SELECT u.*, f.path FROM units u JOIN files f ON f.id=u.file_id WHERE u.file_hash=f.content_hash AND u.status='failed'")
    F += ["", "## 抽出（文字起こし・フレーム・ページ等）"] + [f"- {r['path']} {r['locator']}: {r['last_error']}" for r in rows] or ["（なし）"]
    rows = db.q("SELECT * FROM files WHERE status IN ('failed','partial','skipped','unsupported')")
    F += ["", "## ファイル単位"] + [f"- {r['path']}: {r['status']} {r['status_detail'] or ''}" for r in rows] or ["（なし）"]
    rows = db.q("SELECT e.*, f.path FROM errors e LEFT JOIN files f ON f.id=e.file_id WHERE e.stage LIKE 'semantic%' ORDER BY e.id DESC LIMIT 100")
    F += ["", "## エラー記録（意味理解）"] + [f"- [{r['stage']}] {r['path'] or ''}: {r['message']}" for r in rows] or ["（なし）"]
    (out / "failures.md").write_text("\n".join(F) + "\n", encoding="utf-8")

    # 12. 網羅率
    cov = collect_coverage(db)
    V = ["# 解析網羅率と検証状況", "",
         "> **解析網羅率**は、動画の時間（または資料の単位数）のうち、各段階の処理が完了した割合です。",
         "> **すべての指示を漏れなく理解できたことを意味しません。** 指示の検出率（実際の指示のうち何割を見つけたか）は、",
         "> 正解データが無いため算出していません。代わりに、規則候補とAI抽出の突き合わせ結果（見落とし候補の数）と照合状況を示します。", ""]
    for c in cov:
        V += [f"## {c['file']}（{c['kind']}・場面 {c['scenes']}・採用結果: {c['mode'] or '未生成'}）"]
        if c.get("ai_progress"):
            V.append(f"- AI意味理解の進行: {c['ai_progress']['step']} / {c['ai_progress']['status']} {c['ai_progress']['note'] or ''}")
        V += ["| 段階 | 完了 | 全体 | 網羅率 | 未処理の範囲 |", "|---|---:|---:|---:|---|"]
        for lbl, m in c["metrics"].items():
            V.append(f"| {lbl} | {m['done']} | {m['total']} {m['unit']} | {_pct(m['ratio'])} | "
                     f"{'、'.join(m['missing'][:20]) or '—'}{m.get('note', '')} |")
        if c.get("directive_verification") is not None:
            V.append("- 指示の照合状況: " + "、".join(f"{VER_LABEL.get(k, k)} {v}" for k, v in sorted(c["directive_verification"].items())))
            V.append(f"- 未確定の指示: {c['directives_open']} 件")
            if c["mode"] == "ai":
                V.append(f"- 再解釈ラウンド {c['rounds']}・収束 {'はい' if c['converged'] == 1 else 'いいえ' if c['converged'] == 0 else '—'}")
        V.append("")
    (out / "coverage.md").write_text("\n".join(V) + "\n", encoding="utf-8")
    (out / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"directives": out / "directives.md", "coverage": out / "coverage.md", "experiment": out / "experiment.md"}


def search(db: DB, keyword: str, limit: int = 50) -> list[dict]:
    """指示・場面の理解・全体理解をキーワードで検索し、出典（資料・時刻）付きで返す。"""
    like = f"%{keyword}%"
    docs = {d["id"]: d for d in best_docs(db)}
    out = []
    if docs:
        qs = ",".join("?" * len(docs))
        for d in db.q(f"SELECT * FROM directives WHERE doc_id IN ({qs}) AND (content LIKE ? OR speech LIKE ? OR target_task LIKE ?"
                      f" OR condition LIKE ? OR evidence LIKE ?) ORDER BY source_path, t_start LIMIT ?",
                      [*docs, like, like, like, like, like, limit]):
            out.append({"type": "directive", "id": d["id"], "source": d["source_path"], "where": where(d),
                        "class": DIRECTIVE_CLASSES[d["directive_class"]], "text": d["content"],
                        "verification": VER_LABEL.get(d["verification"], d["verification"]),
                        "effective": EFF_LABEL.get(d["effective"], d["effective"])})
        for s in db.q(f"SELECT s.*, f.path FROM sem_scenes s JOIN sem_docs d ON d.id=s.doc_id JOIN files f ON f.id=d.file_id "
                      f"WHERE s.doc_id IN ({qs}) AND s.analysis_json LIKE ? ORDER BY f.path, s.seq, s.pass DESC LIMIT ?",
                      [*docs, like, limit]):
            out.append({"type": "scene", "source": s["path"], "pass": s["pass"],
                        "where": fmt_time(s["t_start"]) if s["t_start"] is not None else (f"p.{s['page']}" if s["page"] else ""),
                        "text": json.loads(s["analysis_json"]).get("meaning", "")})
    return out
