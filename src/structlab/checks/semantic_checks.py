"""局所理解・全体理解時と、指示抽出時のチェック。

既存の検証（根拠の引用照合・分類の向き/強さの検査・見落とし候補・第3回照合・訂正/矛盾の関連付け）はそのまま使い、
ここでは次の不足分だけを行う:
  理解: 時刻・場面番号の参照の妥当性、訂正/補足/例外/条件変更や整合説明の根拠が原文にあるか、
        規則が原文から検出した訂正と全体理解の訂正の一致、相互照合の収束
  指示: 発言者（先生と断定してよいか）、対象作業、条件、および既存の照合状態の集計
"""
from __future__ import annotations

import json

from ..classify import verify_evidence
from ..db import DB
from ..models import fmt_time
from ..semantic.material import Scene
from ..semantic.rules import discourse_markers
from .base import clear, record

ST_U, ST_D = "understanding", "directives"
_EV_STATUS = {"evidence_matched": "ok", "evidence_not_found": "needs_review", "no_local_text": "unverified"}


def check_understanding(db: DB, doc, frow, scenes: list[Scene], run_id: int | None = None) -> None:
    fid, did = frow["id"], doc["id"]
    clear(db, ST_U, file_id=fid)
    kw = dict(file_id=fid, run_id=run_id)
    if doc["mode"] != "ai":
        record(db, stage=ST_U, check_type="understanding", target_type="doc", target_id=did, status="unverified",
               detail="AIによる局所理解・全体理解は未実施（規則による指示抽出のみ）", **kw)
        return
    seqs = {sc.seq for sc in scenes}
    all_text = "\n".join(ln.text for sc in scenes for ln in sc.lines)
    with db.tx():
        for sc in scenes:
            r = db.q1("SELECT * FROM sem_scenes WHERE doc_id=? AND unit_id=? AND status='ok' ORDER BY pass DESC LIMIT 1",
                      (did, sc.unit_id))
            if r is None:
                record(db, stage=ST_U, check_type="scene_grounding", target_type="scene", target_id=f"{did}:{sc.seq}",
                       status="unverified", detail=f"場面 #{sc.seq} {sc.where()} の局所理解がない（失敗または未処理）",
                       unit_id=sc.unit_id, **kw)
                continue
            a = json.loads(r["analysis_json"] or "{}")
            problems, unsure = [], []
            text = "\n".join(ln.text for ln in sc.lines)
            for kp in a.get("key_points") or []:
                t = kp.get("time_sec")
                if t is not None and sc.t_start is not None and not (sc.t_start - 1 <= t <= sc.t_end + 1):
                    problems.append(f"要点の時刻 {fmt_time(t)} が場面の範囲外")
            for x in a.get("discourse") or []:
                st = _EV_STATUS[verify_evidence(x.get("evidence", ""), text)]
                if st != "ok":
                    unsure.append(f"{x.get('type')} の根拠「{x.get('evidence', '')[:40]}」を場面の原文で確認できない")
            if not sc.lines:
                st, d = "unverified", "場面に照合できる元の発話・資料テキストがない"
            elif problems:
                st, d = "mismatch", "；".join(problems)
            elif unsure:
                st, d = "needs_review", "；".join(unsure)
            else:
                st, d = "ok", "時刻・根拠の引用を原文で確認"
            record(db, stage=ST_U, check_type="scene_grounding", target_type="scene", target_id=f"{did}:{sc.seq}",
                   status=st, detail=f"場面 #{sc.seq} {sc.where()}: {d}", unit_id=sc.unit_id, **kw)
        _scene_level(db, did, scenes, kw)
        g = db.q1("SELECT * FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC LIMIT 1", (did,))
        if g is None:
            record(db, stage=ST_U, check_type="global_refs", target_type="doc", target_id=did, status="unverified",
                   detail="全体理解が未生成", **kw)
            return
        g = json.loads(g["data_json"])
        bad = []
        refs = []
        for key in ("flow", "procedure", "important_points", "repeated_cautions", "final_requirements", "apparent_contradictions"):
            for x in g.get(key) or []:
                refs += [(key, s) for s in x.get("scene_seqs") or []]
        refs += [("scene_roles", x.get("scene_seq")) for x in g.get("scene_roles") or []]
        for x in g.get("changes") or []:
            refs += [("changes", x.get("earlier_scene")), ("changes", x.get("later_scene"))]
        bad += [f"{k} が存在しない場面 #{s} を参照" for k, s in refs if s is not None and s not in seqs]
        steps = {x.get("step") for x in g.get("procedure") or []}
        bad += [f"手順 {x.get('step')} が存在しない手順 {d} に依存" for x in g.get("procedure") or []
                for d in x.get("depends_on") or [] if d not in steps]
        record(db, stage=ST_U, check_type="global_refs", target_type="doc", target_id=did,
               status="mismatch" if bad else "ok", detail="；".join(bad[:20]) or "場面番号・手順番号の参照は妥当", **kw)
        # 訂正・補足・例外・条件変更、整合説明の根拠
        unsure = []
        for x in g.get("changes") or []:
            if _EV_STATUS[verify_evidence(x.get("evidence", ""), all_text)] != "ok":
                unsure.append(f"{x.get('type')}「{x.get('description', '')[:30]}」の根拠を原文で確認できない")
        for x in g.get("apparent_contradictions") or []:
            if x.get("resolved") and _EV_STATUS[verify_evidence(x.get("resolution_evidence", ""), all_text)] != "ok":
                unsure.append(f"矛盾を解消済みとした根拠「{x.get('resolution_evidence', '')[:30]}」を原文で確認できない")
        record(db, stage=ST_U, check_type="change_evidence", target_type="doc", target_id=did,
               status="needs_review" if unsure else "ok", detail="；".join(unsure[:20]) or "訂正・補足・整合説明の根拠を原文で確認", **kw)
        # 規則（原文の言い回し）と全体理解の突き合わせ（訂正の見落とし・過剰認定）
        rule_corr = [ln for sc in scenes for ln in sc.lines if "correction" in discourse_markers(ln.text)]
        ai_corr = [x for x in g.get("changes") or [] if x.get("type") == "correction"]
        if rule_corr and not ai_corr:
            st, d = "needs_review", ("原文に訂正の言い回しがあるが全体理解に訂正が反映されていない: "
                                     + " / ".join(f"[{fmt_time(ln.t_start)}] {ln.text[:30]}" if ln.t_start is not None
                                                  else ln.text[:30] for ln in rule_corr[:5]))
        elif ai_corr and not rule_corr:
            st, d = "needs_review", "全体理解が訂正とした箇所に、原文の訂正の言い回しが見つからない（AIの解釈）"
        else:
            st, d = "ok", f"訂正の言い回し {len(rule_corr)} 件／全体理解の訂正 {len(ai_corr)} 件"
        record(db, stage=ST_U, check_type="correction_consistency", target_type="doc", target_id=did, status=st, detail=d, **kw)
        conv = doc["converged"]
        record(db, stage=ST_U, check_type="reconciliation", target_type="doc", target_id=did,
               status="ok" if conv == 1 else "needs_review" if conv == 0 else "unverified",
               detail=f"相互照合 {doc['round']} 回・" + ("収束" if conv == 1 else "上限まで変更が続いた（解釈が安定していない）" if conv == 0 else "未完了"),
               **kw)
        unres = [x for x in g.get("apparent_contradictions") or [] if not x.get("resolved")]
        record(db, stage=ST_U, check_type="apparent_contradictions", target_type="doc", target_id=did,
               status="needs_review" if unres else "ok",
               detail=("整合できなかった説明の食い違い（未解決として記録）: " + " / ".join(x.get("description", "")[:40] for x in unres[:5]))
               if unres else f"一見矛盾する説明 {len(g.get('apparent_contradictions') or [])} 件はすべて根拠付きで整合", **kw)
        roles = {x.get("scene_seq") for x in g.get("scene_roles") or []}
        flow = {s for x in g.get("flow") or [] for s in x.get("scene_seqs") or []}
        analysed = {sc.seq for sc in scenes if db.q1("SELECT 1 FROM sem_scenes WHERE doc_id=? AND unit_id=? AND status='ok'",
                                                     (did, sc.unit_id))}
        miss = sorted(s for s in analysed if s not in roles and s not in flow)
        record(db, stage=ST_U, check_type="scene_role_coverage", target_type="doc", target_id=did,
               status="needs_review" if miss else "ok",
               detail=f"全体の流れ・役割に位置付けられていない場面: {miss}" if miss else f"解析した {len(analysed)} 場面すべてを位置付け", **kw)
        if g.get("_failed_scenes"):
            record(db, stage=ST_U, check_type="global_completeness", target_type="doc", target_id=did, status="needs_review",
                   detail=f"局所理解に失敗した場面 {g['_failed_scenes']} を含まない全体理解", **kw)


def _scene_level(db, did, scenes, kw) -> None:
    lacking, unread = [], []
    n = len(scenes)
    for i, sc in enumerate(scenes):
        r = db.q1("SELECT analysis_json FROM sem_scenes WHERE doc_id=? AND unit_id=? AND status='ok' ORDER BY pass DESC LIMIT 1",
                  (did, sc.unit_id))
        if r is None:
            continue
        a = json.loads(r["analysis_json"] or "{}")
        if (i > 0 and not str(a.get("relation_prev") or "").strip()) or (i < n - 1 and not str(a.get("relation_next") or "").strip()):
            lacking.append(sc.seq)
        unread += [f"#{sc.seq} {x}" for x in a.get("unreadable") or []]
    record(db, stage=ST_U, check_type="scene_neighbors", target_type="doc", target_id=did,
           status="needs_review" if lacking else "ok",
           detail=f"直前・直後との関係が解釈されていない場面: {lacking}" if lacking else "全場面で前後の説明との関係を解釈", **kw)
    record(db, stage=ST_U, check_type="unreadable", target_type="doc", target_id=did,
           status="needs_review" if unread else "ok",
           detail=("読み取り不能（推測で補っていない）: " + " / ".join(unread[:10])) if unread else "読み取り不能の箇所の報告なし", **kw)


def check_directives(db: DB, doc, frow, cfg, run_id: int | None = None) -> None:
    fid, did = frow["id"], doc["id"]
    clear(db, ST_D, file_id=fid)
    kw = dict(file_id=fid, run_id=run_id)
    ds = db.q("SELECT * FROM directives WHERE doc_id=? AND effective NOT IN ('merged','dismissed','rejected')", (did,))
    with db.tx():
        speech = [d for d in ds if d["speaker"] in ("unknown", "teacher_inferred", "teacher_confirmed")]
        unk = [d for d in speech if d["speaker"] != "teacher_confirmed"]
        if speech:
            record(db, stage=ST_D, check_type="speaker", target_type="doc", target_id=did,
                   status="ok" if not unk else "unverified",
                   detail=("動画の音声は先生と利用者が確認済み（semantic.video_speaker: teacher）" if not unk else
                           f"発言者を識別していない音声の指示 {len(unk)} 件（先生の指示とは断定していない）"
                           + (f"／うちAIが先生と推定 {sum(1 for d in unk if d['speaker'] == 'teacher_inferred')} 件" if unk else "")),
                   **kw)
        if doc["mode"] == "ai":
            no_target = [d["id"] for d in ds if d["extracted_by"] != "rule" and not (d["target_task"] or "").strip()]
            record(db, stage=ST_D, check_type="target", target_type="doc", target_id=did,
                   status="needs_review" if no_target else "ok",
                   detail=(f"対象作業が特定されていない指示: {['#' + str(i) for i in no_target[:30]]}" if no_target
                           else "全指示に対象作業あり"), **kw)
        nocond = [d["id"] for d in ds if d["directive_class"] == "conditional" and not d["condition"]]
        record(db, stage=ST_D, check_type="condition", target_type="doc", target_id=did,
               status="mismatch" if nocond else "ok",
               detail=f"条件付き指示なのに条件がない: {['#' + str(i) for i in nocond]}" if nocond else "条件付き指示の条件を確認", **kw)
        # 既存の照合結果の集計（検査そのものは第3回照合で実施済み。ここでは状態を一覧化するだけ）
        omis = [d["id"] for d in ds if d["verification"] == "omission_candidate"]
        record(db, stage=ST_D, check_type="omission", target_type="doc", target_id=did,
               status="needs_review" if omis else ("ok" if doc["mode"] == "ai" else "unverified"),
               detail=(f"未解決の見落とし候補: {['#' + str(i) for i in omis[:30]]}" if omis else
                       "規則候補はすべてAI抽出と突き合わせ済み" if doc["mode"] == "ai" else
                       "AI抽出がないため見落とし検査は未実施（規則による抽出のみ）"), **kw)
        _directive_details(db, did, frow, doc, ds, kw)
        mis = [d["id"] for d in ds if d["verification"] == "class_mismatch_unresolved"]
        rev = [d["id"] for d in ds if d["verification"] in ("needs_review", "found_in_verify", "unverified")]
        record(db, stage=ST_D, check_type="classification", target_type="doc", target_id=did,
               status="mismatch" if mis else "needs_review" if rev else ("ok" if doc["mode"] == "ai" else "unverified"),
               detail=(f"分類が原文の言い回しと食い違う（未解決）: {['#' + str(i) for i in mis]}" if mis else
                       f"照合で確定していない指示: {['#' + str(i) for i in rev[:30]]}" if rev else
                       "全指示の分類を原文の言い回しと照合済み" if doc["mode"] == "ai" else
                       "分類は言い回しによる機械判定のみ（文脈での確認は未実施）"), **kw)


_RESOLVED = ("verified", "revised")


def _directive_details(db, did, frow, doc, ds, kw) -> None:
    """既存の照合結果（第3回照合・関連付け）を、要件ごとの項目として記録する（照合は再実行しない）。"""
    ai = [d for d in ds if d["extracted_by"] != "rule"]
    noev = [d["id"] for d in ai if d["verification"] not in _RESOLVED
            and any("根拠の引用" in n for n in json.loads(d["verification_notes"] or "[]"))]
    record(db, stage=ST_D, check_type="evidence", target_type="doc", target_id=did, status="needs_review" if noev else "ok",
           detail=(f"根拠の引用を元の範囲で確認できない指示: {['#' + str(i) for i in noev[:30]]}" if noev else
                   "AI抽出の指示の根拠を原文で確認済み" if ai else "規則で原文から抽出した指示のみ（根拠は原文そのもの）"), **kw)
    if frow["kind"] in ("video", "audio"):
        notime = [d["id"] for d in ds if d["t_start"] is None and d["origin"] == "explicit"]
        record(db, stage=ST_D, check_type="time_located", target_type="doc", target_id=did,
               status="mismatch" if notime else "ok",
               detail=f"時刻が特定されていない指示: {['#' + str(i) for i in notime[:30]]}" if notime else "全指示に時刻あり", **kw)
    inf = [d for d in ds if d["origin"] == "ai_inference"]
    bad = [d["id"] for d in inf if (d["speaker"] or "").startswith("teacher")]
    record(db, stage=ST_D, check_type="inference_separation", target_type="doc", target_id=did,
           status="mismatch" if bad else "ok",
           detail=(f"AIの推論なのに先生の発言扱いの指示: {bad}" if bad else
                   f"AIの推論 {len(inf)} 件は明示された指示・先生の発言と区別して記録"), **kw)
    capped = [d["id"] for d in ds if any("照合の上限" in n for n in json.loads(d["verification_notes"] or "[]"))]
    if capped:
        record(db, stage=ST_D, check_type="verify_cap", target_type="doc", target_id=did, status="needs_review",
               detail=f"照合の上限のため再解析していない指示: {['#' + str(i) for i in capped[:30]]}", **kw)
    unres_rel = [d["id"] for d in ds if json.loads(d["extra_json"] or "{}").get("relation_unresolved")]
    ids = [d["id"] for d in ds] or [-1]
    qs = ",".join("?" * len(ids))
    cand = db.q(f"SELECT * FROM directive_links WHERE scope='document' AND status='unresolved' AND relation!='conflicts_with' "
                f"AND from_id IN ({qs})", ids)
    record(db, stage=ST_D, check_type="relations", target_type="doc", target_id=did,
           status="needs_review" if unres_rel or cand else "ok",
           detail=("、".join(x for x in (f"関係先を特定できない訂正・例外等: {['#' + str(i) for i in unres_rel[:20]]}" if unres_rel else "",
                                       f"原文の言い回しで確定できない関係（推定）: {len(cand)} 件" if cand else "") if x)
                   or "訂正・例外・補足・条件変更の関係先をすべて原文で特定"), **kw)
    conf = db.q(f"SELECT * FROM directive_links WHERE scope='document' AND relation='conflicts_with' AND from_id IN ({qs})", ids)
    record(db, stage=ST_D, check_type="doc_conflicts", target_type="doc", target_id=did,
           status="mismatch" if conf else "ok",
           detail=(f"資料内で矛盾する指示 {len(conf)} 組（どちらが有効かは判断していない）" if conf else "資料内の矛盾する指示なし"), **kw)
