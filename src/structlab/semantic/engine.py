"""意味理解エンジン。

資料（動画・PDF・Excel など）ごとに次の段階を順に実行し、各段階の結果を DB に確定させる（途中停止しても続きから）。

  local モード（常に実行・AI不使用）
    rules      : 言い回しによる指示候補の抽出（A〜H 分類・条件・訂正/補足/例外/条件変更の標識）
    verify     : 原文との照合（規則抽出なので照合は自明。判断が曖昧なものを要確認に）
    relations  : 訂正・補足・例外・条件変更・繰り返し・矛盾の関連付け

  ai モード（AIが有効で、外部送信が明示的に許可された場合のみ）
    第1回 scenes      : 場面ごとの局所理解（音声・映像・前後の文脈を統合）
          synthesis   : 資料全体の理解
          reinterpret : 全体理解を使った各場面の再解釈 → 全体理解の更新（変更がなくなるまで、上限 max_rounds）
    第2回 directives  : 全体理解を踏まえた指示の重点抽出（規則候補を全件確認させ、見落としを検出）
    第3回 verify      : 元の発話・画面・資料との照合（曖昧・不一致・見落とし候補は範囲を広げて再解析）
          relations   : 関連付け
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from ..ai import AIError, AIImage, AIRequest, items_from_response
from ..classify import verify_evidence
from ..db import DB
from ..models import fmt_time
from . import prompts as P
from .material import Scene, build_scenes, is_time_based, lines_text, window_lines
from .relations import link_document
from .rules import DIRECTIVE_CLASSES, STRENGTH, classify, directives_in, polarity

log = logging.getLogger("structlab")

ELIGIBLE_KINDS = ("video", "audio", "pdf", "image", "excel", "text", "docx", "pptx")


FINAL_VERDICTS = ("verified", "revised", "rejected", "class_mismatch_unresolved")


class BudgetExceeded(Exception):
    """AI使用量の上限。今回の実行ではこれ以上 AI を呼ばない（次回続きから）。"""


def _j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"…（以下 {len(s) - n} 文字省略）"


class SemanticEngine:
    def __init__(self, cfg, db: DB, *, ai_call: Callable[[AIRequest, int | None], Any] | None = None,
                 ai_name: str = "", checkpoint: Callable[[], None] | None = None, transcriber=None, ocr=None,
                 run_id: int | None = None):
        self.cfg = cfg
        self.sc = cfg.data["semantic"]
        self.db = db
        self.ai_call = ai_call
        self.ai_name = ai_name
        self.checkpoint = checkpoint or (lambda: None)
        self.transcriber = transcriber
        self.ocr = ocr
        self.run_id = run_id
        self.max_attempts = int(cfg.data["limits"]["max_attempts"])
        self.max_images = int(cfg.data["ai"]["max_images_per_call"])
        self.ai_stopped: str | None = None

    @property
    def ai_active(self) -> bool:
        return bool(self.sc.get("enabled") and self.sc.get("ai") and self.ai_call is not None and not self.ai_stopped)

    # ================= 入口 =================
    def process_file(self, frow, local_path: Path | None = None) -> None:
        if not self.sc.get("enabled") or frow["kind"] not in ELIGIBLE_KINDS:
            return
        if frow["status"] not in ("done", "partial"):
            return
        scenes = build_scenes(self.db, frow)
        if not scenes:
            return
        self._run_local(frow, scenes)
        if self.ai_active:
            try:
                self._run_ai(frow, scenes, local_path)
            except BudgetExceeded as e:
                self.ai_stopped = str(e)
                log.warning("意味理解のAI解析を次回に持ち越します: %s", e)
            except AIError as e:
                d = self._doc_row(frow, "ai")
                if d:
                    self._set_doc(d["id"], note=f"AI呼び出しに失敗（次回再試行）: {e}")
                log.error("意味理解のAI解析に失敗（次回再試行）%s: %s", frow["path"], e)

    def backfill(self) -> None:
        """解析済みファイルのうち、意味理解が未完了のものを処理する（AIを後から許可した場合など）。"""
        if not self.sc.get("enabled"):
            return
        modes = ["local"] + (["ai"] if self.ai_active else [])
        for frow in self.db.q("SELECT * FROM files WHERE status IN ('done','partial') ORDER BY path"):
            if frow["kind"] not in ELIGIBLE_KINDS:
                continue
            for mode in modes:
                d = self._doc_row(frow, mode)
                if d is None or d["status"] != "done":
                    self.checkpoint()
                    self.process_file(frow, None)
                    break

    # ================= 共通 =================
    def _doc_row(self, frow, mode: str):
        return self.db.q1("SELECT * FROM sem_docs WHERE file_id=? AND file_hash=? AND version=? AND mode=?",
                          (frow["id"], frow["content_hash"] or "", P.SEMANTIC_VERSION, mode))

    def _doc(self, frow, mode: str):
        d = self._doc_row(frow, mode)
        if d is None:
            self.db.x("INSERT INTO sem_docs(file_id,file_hash,version,mode,step,status,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (frow["id"], frow["content_hash"] or "", P.SEMANTIC_VERSION, mode,
                       "rules" if mode == "local" else "scenes", "in_progress", time.time()))
            d = self._doc_row(frow, mode)
        return d

    def _set_doc(self, doc_id: int, **kw) -> None:
        kw["updated_at"] = time.time()
        self.db.x(f"UPDATE sem_docs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), doc_id))

    def _step(self, doc_id: int, unit_id: int, step: str):
        return self.db.q1("SELECT * FROM sem_steps WHERE doc_id=? AND unit_id=? AND step=?", (doc_id, unit_id, step))

    def _mark(self, doc_id: int, unit_id: int, step: str, status: str, detail: str = "") -> None:
        prev = self._step(doc_id, unit_id, step)
        att = (prev["attempts"] if prev else 0) + (1 if status != "skipped" else 0)
        if status == "failed" and att >= self.max_attempts:
            status = "failed_final"
        self.db.x("INSERT INTO sem_steps(doc_id,unit_id,step,status,attempts,detail,updated_at) VALUES(?,?,?,?,?,?,?)"
                  " ON CONFLICT(doc_id,unit_id,step) DO UPDATE SET status=excluded.status, attempts=excluded.attempts,"
                  " detail=excluded.detail, updated_at=excluded.updated_at",
                  (doc_id, unit_id, step, status, att, detail[:2000], time.time()))

    def _pending(self, doc_id: int, unit_id: int, step: str) -> bool:
        s = self._step(doc_id, unit_id, step)
        return s is None or s["status"] == "failed"

    def _ai(self, req: AIRequest, unit_id: int | None):
        if self.ai_stopped:
            raise BudgetExceeded(self.ai_stopped)
        return self.ai_call(req, unit_id)

    # ================= local モード =================
    def _run_local(self, frow, scenes: list[Scene]) -> None:
        d = self._doc(frow, "local")
        if d["status"] == "done":
            return
        if d["step"] == "rules":
            self._extract_rule_directives(d["id"], frow, scenes, candidate=False)
            self._set_doc(d["id"], step="verify")
        if self._doc_row(frow, "local")["step"] == "verify":
            self._verify_local(d["id"], frow, scenes, mode="local")
            self._set_doc(d["id"], step="relations")
        link_document(self.db, d["id"])
        self._set_doc(d["id"], step="done", status="done")
        self._post_checks(frow, "local", scenes)

    def _post_checks(self, frow, mode: str, scenes: list[Scene]) -> None:
        """意味理解・指示抽出の工程に組み込んだチェック（AI不使用）。"""
        if not self.cfg.data.get("checks", {}).get("enabled", True):
            return
        from ..checks.semantic_checks import check_directives, check_understanding
        doc = self._doc_row(frow, mode)
        check_understanding(self.db, doc, frow, scenes, self.run_id)
        check_directives(self.db, doc, frow, self.cfg, self.run_id)

    def _extract_rule_directives(self, doc_id: int, frow, scenes: list[Scene], candidate: bool) -> None:
        with self.db.tx():
            self.db.x("DELETE FROM directive_links WHERE from_id IN (SELECT id FROM directives WHERE doc_id=? AND "
                      "extracted_by='rule') OR to_id IN (SELECT id FROM directives WHERE doc_id=? AND extracted_by='rule')",
                      (doc_id, doc_id))
            self.db.x("DELETE FROM directives WHERE doc_id=? AND extracted_by='rule'", (doc_id,))
            for sc in scenes:
                frames = {ln.frame: ln.text for ln in sc.lines if ln.origin == "screen" and ln.frame}
                for i, ln in enumerate(sc.lines):
                    for rd in directives_in(ln.text):
                        before = " / ".join(x.text for x in sc.lines[max(0, i - 2):i])
                        after = " / ".join(x.text for x in sc.lines[i + 1:i + 3])
                        screen = ""
                        if ln.frame:
                            screen = f"{Path(ln.frame).name}" + (f"（画面の文字: {_clip(frames.get(ln.frame, ''), 200)}）"
                                                                if frames.get(ln.frame) else "")
                        notes = [rd.note] if rd.note else []
                        notes.append("分類は言い回しによる機械判定（文脈は未考慮）")
                        self._insert_directive(doc_id, frow, sc, {
                            "directive_class": rd.directive_class, "base_strength": rd.base_strength,
                            "content": rd.content, "condition": rd.condition, "t_start": ln.t_start, "t_end": ln.t_end,
                            "page": ln.page, "screen": screen, "speech": ln.text if ln.origin == "speech" else "",
                            "evidence": ln.text, "context_before": before, "context_after": after,
                            "certainty": rd.certainty, "unconfirmed": "；".join(notes), "origin": "explicit",
                            "extracted_by": "rule", "marker": rd.marker, "discourse": _j(rd.discourse),
                            "verification": "candidate" if candidate else "rule_extracted",
                            "extra": {"line_origin": ln.origin},
                            "speaker": self._speaker(None, ln.origin),
                        })
                self._mark(doc_id, sc.unit_id, "rules", "ok")

    def _insert_directive(self, doc_id: int, frow, sc: Scene | None, d: dict) -> int:
        cur = self.db.x(
            "INSERT INTO directives(doc_id,file_id,unit_id,directive_class,base_strength,content,target_task,deliverable,"
            "audience,condition,source_path,t_start,t_end,page,screen,operation,speech,evidence,context_before,"
            "context_after,certainty,unconfirmed,origin,extracted_by,marker,discourse,verification,verification_notes,"
            "effective,extra_json,created_at,speaker) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, frow["id"], sc.unit_id if sc else None, d["directive_class"], d.get("base_strength"), d["content"],
             d.get("target_task", ""), d.get("deliverable", ""), d.get("audience", ""), d.get("condition"),
             frow["path"], d.get("t_start"), d.get("t_end"), d.get("page", sc.page if sc else None),
             d.get("screen", ""), d.get("operation", ""), d.get("speech", ""), d.get("evidence", ""),
             d.get("context_before", ""), d.get("context_after", ""), d.get("certainty", "low"),
             d.get("unconfirmed", ""), d["origin"], d["extracted_by"], d.get("marker", ""), d.get("discourse", "[]"),
             d.get("verification", "unverified"), _j(d.get("verification_notes", [])), d.get("effective", "effective"),
             _j(d.get("extra", {})), time.time(), d.get("speaker") or "unknown"))
        return int(cur.lastrowid)

    def _speaker(self, claimed: str | None, line_origin: str | None) -> str:
        """発言者。話者識別はしていないので、利用者が「動画の音声は先生」と確認した場合（semantic.video_speaker:
        teacher）以外は、音声の発言を先生のものと断定しない。"""
        if line_origin == "document" or claimed == "document":
            return "document"
        if line_origin == "screen" or claimed == "screen":
            return "screen"
        confirmed = self.sc.get("video_speaker") == "teacher"
        if claimed in ("student", "other"):
            return claimed
        if confirmed:
            return "teacher_confirmed"
        return "teacher_inferred" if claimed == "teacher" else "unknown"

    def _verify_local(self, doc_id: int, frow, scenes: list[Scene], mode: str) -> list[tuple[int, list[str]]]:
        """元の発話・画面・資料との機械的な照合。戻り値は要再解析の (指示ID, 問題点)。"""
        flagged: list[tuple[int, list[str]]] = []
        for d in self.db.q("SELECT * FROM directives WHERE doc_id=? AND effective NOT IN ('rejected','dismissed')",
                           (doc_id,)):
            if d["verification"] in FINAL_VERDICTS:
                continue  # 第3回の照合結果が確定済み（再開時に上書きしない）
            issues: list[str] = []
            if d["extracted_by"] == "rule":
                if mode == "ai":
                    continue  # ai モードの規則候補は AI 抽出との突き合わせで扱う
                if d["directive_class"] == "other":
                    issues.append("指示の強さ・意味を言い回しから判断できない")
                if d["certainty"] == "low":
                    issues.append("言い回しからの推定を含む")
                self._set_verification(d["id"], "needs_review" if issues else "rule_extracted", issues)
                continue
            quote = d["speech"] or d["evidence"] or ""
            win = window_lines(scenes, d["t_start"], d["t_end"], d["page"], 30.0)
            corpus = "\n".join(ln.text for ln in win) or "\n".join(ln.text for sc in scenes for ln in sc.lines)
            ev = verify_evidence(quote, corpus)
            if d["origin"] == "explicit":
                if ev == "evidence_not_found":
                    issues.append("根拠の引用が元の発話・資料の該当範囲に見つからない")
                elif ev == "no_local_text":
                    issues.append("照合できる元のテキストがない（文字起こし・OCRなし）")
            if is_time_based(frow["kind"]) and d["t_start"] is None:
                issues.append("時刻が特定されていない")
            issues += self._class_guard(d["directive_class"], d["base_strength"], quote or d["content"])
            if d["directive_class"] == "conditional" and not d["condition"]:
                issues.append("条件付き指示だが条件が記録されていない")
            if d["certainty"] == "low" or d["directive_class"] == "other":
                issues.append("確実性が低い／判断不能")
            if d["verification"] == "omission_candidate":
                issues.append("規則で検出したがAI抽出に含まれていない（見落とし候補）")
            if issues:
                flagged.append((d["id"], issues))
                self._set_verification(d["id"], "needs_review", issues)
            else:
                self._set_verification(d["id"], "verified_local", ["根拠の引用を元の範囲で確認、分類は言い回しと矛盾しない"])
        # ai モードの見落とし候補（規則候補のうち未対応のもの）
        if mode == "ai":
            for d in self.db.q("SELECT * FROM directives WHERE doc_id=? AND extracted_by='rule' AND verification="
                               "'omission_candidate' AND effective='effective'", (doc_id,)):
                flagged.append((d["id"], ["規則で検出したがAI抽出に含まれていない（見落とし候補）"]))
        return flagged

    @staticmethod
    def _class_guard(cls: str, base: str | None, quote: str) -> list[str]:
        """AI の分類が原文の言い回しの強さ・向きと食い違っていないか。"""
        rc, marker = classify(quote)
        eff = base if cls == "conditional" else cls
        rc_eff = rc
        if rc == "conditional":
            rc_eff = None
        if rc is None or eff in (None, "other") or rc_eff in (None, "other"):
            return []
        if polarity(eff) and polarity(rc_eff) and polarity(eff) != polarity(rc_eff):
            return [f"分類の向きが原文の言い回し「{marker}」({DIRECTIVE_CLASSES[rc_eff]})と逆"]
        if polarity(eff) == polarity(rc_eff) and STRENGTH.get(eff, 0) > STRENGTH.get(rc_eff, 0):
            return [f"分類の強さ({DIRECTIVE_CLASSES[eff]})が原文の言い回し「{marker}」({DIRECTIVE_CLASSES[rc_eff]})より強い"]
        return []

    def _set_verification(self, did: int, status: str, notes: list[str]) -> None:
        self.db.x("UPDATE directives SET verification=?, verification_notes=? WHERE id=?", (status, _j(notes), did))

    # ================= ai モード =================
    def _run_ai(self, frow, scenes: list[Scene], local_path: Path | None) -> None:
        d = self._doc(frow, "ai")
        if d["status"] == "done":
            return
        did = d["id"]
        label = frow["path"]
        if d["step"] == "scenes":
            if not self._stage1_scenes(did, frow, scenes):
                return
            self._set_doc(did, step="synthesis")
        d = self._doc_row(frow, "ai")
        if d["step"] == "synthesis":
            self._synthesize(did, frow, scenes, version=1, changes=[])
            self._set_doc(did, step="reinterpret", round=0)
        d = self._doc_row(frow, "ai")
        if d["step"] == "reinterpret":
            self._reinterpret_loop(did, frow, scenes, int(d["round"] or 0))
            self._set_doc(did, step="directives")
        d = self._doc_row(frow, "ai")
        if d["step"] == "directives":
            if not self._stage2_directives(did, frow, scenes):
                return
            self._set_doc(did, step="verify")
        d = self._doc_row(frow, "ai")
        if d["step"] == "verify":
            self._stage3_verify(did, frow, scenes, local_path)
            self._set_doc(did, step="relations")
        link_document(self.db, did)
        self._set_doc(did, step="done", status="done")
        self._post_checks(frow, "ai", scenes)
        log.info("意味理解 完了: %s", label)

    # ---------- 第1回: 場面 ----------
    def _latest(self, did: int, unit_id: int) -> dict | None:
        r = self.db.q1("SELECT * FROM sem_scenes WHERE doc_id=? AND unit_id=? AND status='ok' ORDER BY pass DESC LIMIT 1",
                       (did, unit_id))
        return json.loads(r["analysis_json"]) if r else None

    @staticmethod
    def _brief(a: dict | None) -> str:
        if not a:
            return ""
        return _clip(f"意味: {a.get('meaning', '')}\n目的: {a.get('purpose', '')}\n作業: {a.get('task', '')}\n"
                     f"要点: {'; '.join(k.get('text', '') for k in a.get('key_points', []))}", 1500)

    def _stage1_scenes(self, did: int, frow, scenes: list[Scene]) -> bool:
        for i, sc in enumerate(scenes):
            if not self._pending(did, sc.unit_id, "scene1"):
                continue
            self.checkpoint()
            prev = self._latest(did, scenes[i - 1].unit_id) if i > 0 else None
            nxt = lines_text(scenes[i + 1].lines) if i + 1 < len(scenes) else ""
            mat = f"### 時刻同期した発話・画面\n{lines_text(sc.lines) or '（なし）'}\n\n### 抽出結果\n{sc.material}"
            if sc.transcribed is False:
                mat += "\n\n※この区間の音声は文字起こしされていません。音声の内容は推測しないこと。"
            req = AIRequest(task="scene_analysis", source_label=f"{frow['path']} {sc.label}",
                            text=P.scene_prompt(frow["path"], f"#{sc.seq} {sc.where()}", _clip(mat, 30000),
                                                self._brief(prev), _clip(nxt, 3000)),
                            images=[AIImage(p, lbl) for p, lbl in sc.images[:self.max_images]],
                            t_start=sc.t_start, t_end=sc.t_end, system=P.COMMON_SYSTEM, schema=P.SCENE_SCHEMA)
            try:
                resp = self._ai(req, sc.unit_id)
            except AIError as e:
                self._mark(did, sc.unit_id, "scene1", "failed", str(e))
                self.db.log_error(run_id=self.run_id, stage="semantic:scene", message=str(e), file_id=frow["id"],
                                  unit_id=sc.unit_id)
                continue
            a = (resp.data or {}).get("analysis") or {}
            self._save_scene(did, sc, 1, a, None)
            self._legacy_items(frow, sc, a, f"{self.ai_name}:{resp.model}")
            self._mark(did, sc.unit_id, "scene1", "ok")
        pending = [sc for sc in scenes if self._pending(did, sc.unit_id, "scene1")]
        if pending:
            self._set_doc(did, note=f"{len(pending)} 場面の局所理解が未完了（次回再試行）")
            return False
        return True

    def _save_scene(self, did: int, sc: Scene, ps: int, analysis: dict, changes: list | None) -> None:
        self.db.x("INSERT INTO sem_scenes(doc_id,unit_id,seq,pass,t_start,t_end,page,status,analysis_json,changes_json,"
                  "created_at) VALUES(?,?,?,?,?,?,?,'ok',?,?,?) ON CONFLICT(doc_id,unit_id,pass) DO UPDATE SET "
                  "analysis_json=excluded.analysis_json, changes_json=excluded.changes_json, status='ok'",
                  (did, sc.unit_id, sc.seq, ps, sc.t_start, sc.t_end, sc.page, _j(analysis),
                   _j(changes) if changes is not None else None, time.time()))

    def _legacy_items(self, frow, sc: Scene, a: dict, analyzer: str) -> None:
        """従来の10分類（knowledge.md）へも反映する（単位ごとのAI解析を意味理解で置き換えた場合）。"""
        u = self.db.q1("SELECT ai_status FROM units WHERE id=?", (sc.unit_id,))
        if not u or u["ai_status"] != "deferred_semantic":
            return
        corpus = "\n".join(ln.text for ln in sc.lines) + "\n" + sc.material
        items, _ = items_from_response({"items": a.get("items", []), "unreadable": a.get("unreadable", [])}, corpus,
                                       analyzer=analyzer, page=sc.page, t_start=sc.t_start, t_end=sc.t_end)
        from dataclasses import asdict
        with self.db.tx():
            self.db.x("DELETE FROM items WHERE unit_id=? AND analyzer!='local-rules'", (sc.unit_id,))
            rows = []
            for it in items:
                r = asdict(it)
                r.update({"unit_id": sc.unit_id, "file_id": frow["id"], "file_hash": frow["content_hash"] or "",
                          "source_path": frow["path"]})
                rows.append(r)
            self.db.add_items(rows)
            self.db.x("UPDATE units SET ai_status='done' WHERE id=?", (sc.unit_id,))

    # ---------- 第1回: 全体理解 ----------
    def _scene_digest(self, did: int, scenes: list[Scene]) -> list[dict]:
        out = []
        for sc in scenes:
            a = self._latest(did, sc.unit_id)
            st = self._step(did, sc.unit_id, "scene1")
            if a is None:
                out.append({"scene_seq": sc.seq, "where": sc.where(), "status": "未解析",
                            "reason": st["detail"] if st else ""})
                continue
            out.append({"scene_seq": sc.seq, "where": sc.where(),
                        **{k: a.get(k) for k in ("speech", "screen", "operations", "meaning", "purpose", "relation_prev",
                                                 "relation_next", "audience", "task", "deliverables", "key_points",
                                                 "discourse", "uncertain", "unreadable")}})
        return out

    def _synthesize(self, did: int, frow, scenes: list[Scene], version: int, changes: list) -> dict:
        self.checkpoint()
        digest = self._scene_digest(did, scenes)
        limit = int(self.sc.get("max_synthesis_chars", 200000))
        prev = self._understanding(did)
        blocks = []
        body = _j(digest)
        if len(body) > limit:
            # 長い資料は場面をまとめて部分的に理解し、その結果をさらに統合する（切り捨てない）
            per = max(1, len(digest) * limit // len(body))
            for k in range(0, len(digest), per):
                part = digest[k:k + per]
                r = self._ai(AIRequest(task="global_synthesis_part", source_label=frow["path"],
                                       text=f"{P.GLOBAL_INSTRUCTION}\n※以下は資料の一部（場面 {part[0]['scene_seq']}〜"
                                            f"{part[-1]['scene_seq']}）です。\n## 資料\n{frow['path']}\n"
                                            f"## 場面の理解\n```json\n{_j(part)}\n```\n",
                                       system=P.COMMON_SYSTEM, schema=P.GLOBAL_SCHEMA), None)
                blocks.append({"scenes": [part[0]["scene_seq"], part[-1]["scene_seq"]], "understanding": r.data})
            body = _j({"partial_understandings": blocks})
        text = (f"{P.GLOBAL_INSTRUCTION}\n## 資料\n{frow['path']}（場面数 {len(scenes)}）\n"
                f"## 場面の理解\n```json\n{body}\n```\n")
        if prev:
            text += f"\n## 前回までの全体理解（更新対象）\n```json\n{_j(prev)}\n```\n"
        if changes:
            text += f"\n## 再解釈で変更された場面\n```json\n{_j(changes)}\n```\n"
        try:
            r = self._ai(AIRequest(task="global_synthesis", source_label=frow["path"], text=text,
                                   system=P.COMMON_SYSTEM, schema=P.GLOBAL_SCHEMA), None)
        except AIError as e:
            self.db.log_error(run_id=self.run_id, stage="semantic:synthesis", message=str(e), file_id=frow["id"])
            raise
        data = r.data or {}
        data["_failed_scenes"] = [x["scene_seq"] for x in digest if x.get("status") == "未解析"]
        self.db.x("INSERT INTO sem_understanding(scope,doc_id,version,data_json,analyzer,created_at) VALUES"
                  "('document',?,?,?,?,?)", (did, version, _j(data), f"{self.ai_name}:{r.model}", time.time()))
        return data

    def _understanding(self, did: int) -> dict | None:
        r = self.db.q1("SELECT data_json FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC"
                       " LIMIT 1", (did,))
        return json.loads(r["data_json"]) if r else None

    def _understanding_version(self, did: int) -> int:
        r = self.db.q1("SELECT MAX(version) v FROM sem_understanding WHERE scope='document' AND doc_id=?", (did,))
        return int(r["v"] or 0)

    @staticmethod
    def _global_brief(g: dict | None) -> str:
        if not g:
            return "（全体理解なし）"
        keep = {k: g.get(k) for k in ("theme", "purpose", "message", "logic_structure", "flow", "procedure", "changes",
                                      "apparent_contradictions", "final_requirements", "conclusion", "scene_roles")}
        return _clip(_j(keep), 20000)

    # ---------- 相互照合（再解釈） ----------
    def _reinterpret_loop(self, did: int, frow, scenes: list[Scene], start_round: int) -> None:
        max_rounds = int(self.sc.get("max_rounds", 2))
        rnd = start_round
        while rnd < max_rounds:
            rnd += 1
            g = self._understanding(did)
            changed: list[dict] = []
            for i, sc in enumerate(scenes):
                step = f"reinterpret{rnd}"
                cur = self._latest(did, sc.unit_id)
                if cur is None:
                    self._mark(did, sc.unit_id, step, "skipped", "局所理解なし")
                    continue
                if not self._pending(did, sc.unit_id, step):
                    r = self.db.q1("SELECT changes_json FROM sem_scenes WHERE doc_id=? AND unit_id=? AND pass=?",
                                   (did, sc.unit_id, rnd + 1))
                    if r and r["changes_json"] and json.loads(r["changes_json"]):
                        changed.append({"scene_seq": sc.seq, "changes": json.loads(r["changes_json"])})
                    continue
                self.checkpoint()
                prev = self._latest(did, scenes[i - 1].unit_id) if i > 0 else None
                nxt = self._latest(did, scenes[i + 1].unit_id) if i + 1 < len(scenes) else None
                text = (f"{P.REINTERPRET_INSTRUCTION}\n## 資料\n{frow['path']}\n## 対象場面 #{sc.seq} {sc.where()}\n"
                        f"### 元の発話・画面\n{_clip(lines_text(sc.lines), 15000)}\n"
                        f"### この場面の現在の解釈\n```json\n{_j(cur)}\n```\n"
                        f"## 資料全体の理解\n```json\n{self._global_brief(g)}\n```\n"
                        f"## 直前の場面の解釈\n{self._brief(prev) or '（なし）'}\n## 直後の場面の解釈\n{self._brief(nxt) or '（なし）'}\n")
                imgs = [AIImage(p, lbl) for p, lbl in sc.images[:self.max_images]] if self.sc.get("reinterpret_images") else []
                try:
                    r = self._ai(AIRequest(task="scene_reinterpret", source_label=f"{frow['path']} {sc.label}", text=text,
                                           images=imgs, t_start=sc.t_start, t_end=sc.t_end, system=P.COMMON_SYSTEM,
                                           schema=P.REINTERPRET_SCHEMA), sc.unit_id)
                except AIError as e:
                    self._mark(did, sc.unit_id, step, "failed", str(e))
                    continue
                data = r.data or {}
                ch = data.get("changes") or [] if data.get("revised") else []
                self._save_scene(did, sc, rnd + 1, data.get("analysis") or cur, ch)
                if ch:
                    changed.append({"scene_seq": sc.seq, "changes": ch})
                self._mark(did, sc.unit_id, step, "ok", f"変更 {len(ch)} 件")
            self._set_doc(did, round=rnd)
            if not changed:
                self._set_doc(did, converged=1, note=f"再解釈 {rnd} 回目で変更なし（収束）")
                return
            self._synthesize(did, frow, scenes, version=self._understanding_version(did) + 1, changes=changed)
        self._set_doc(did, converged=0, note=f"再解釈の上限 {max_rounds} 回に達した（最後の回でも変更あり。要確認）")

    # ---------- 第2回: 指示の重点抽出 ----------
    def _stage2_directives(self, did: int, frow, scenes: list[Scene]) -> bool:
        if not self.db.q1("SELECT 1 FROM sem_steps WHERE doc_id=? AND step='rules'", (did,)):
            self._extract_rule_directives(did, frow, scenes, candidate=True)
        g = self._understanding(did)
        ctx_n = int(self.sc.get("directive_context_scenes", 1))
        for i, sc in enumerate(scenes):
            if not self._pending(did, sc.unit_id, "directives"):
                continue
            self.checkpoint()
            cands = self.db.q("SELECT * FROM directives WHERE doc_id=? AND unit_id=? AND extracted_by='rule' ORDER BY id",
                              (did, sc.unit_id))
            cand_txt = "\n".join(
                f"候補 {k + 1}: [{DIRECTIVE_CLASSES[c['directive_class']]}] {c['content']}"
                + (f"（{fmt_time(c['t_start'])}）" if c["t_start"] is not None else "")
                + (f"（条件: {c['condition']}）" if c["condition"] else "") for k, c in enumerate(cands)) or "（なし）"
            around = []
            for j in range(max(0, i - ctx_n), min(len(scenes), i + ctx_n + 1)):
                if j != i:
                    around.append(f"#### 場面 #{scenes[j].seq} {scenes[j].where()}\n{_clip(lines_text(scenes[j].lines), 4000)}")
            text = (f"{P.DIRECTIVE_INSTRUCTION}\n## 資料\n{frow['path']}\n## 資料全体の理解\n```json\n{self._global_brief(g)}\n```\n"
                    f"## 対象場面 #{sc.seq} {sc.where()} の理解\n```json\n{_j(self._latest(did, sc.unit_id) or {})}\n```\n"
                    f"## 対象場面の元の発話・画面・資料\n{_clip(lines_text(sc.lines), 20000) or '（なし）'}\n"
                    f"### 抽出結果\n{_clip(sc.material, 10000)}\n"
                    f"## 前後の場面（文脈）\n{chr(10).join(around) or '（なし）'}\n"
                    f"## 規則による候補（全件確認すること）\n{cand_txt}\n")
            imgs = [AIImage(p, lbl) for p, lbl in sc.images[:self.max_images]] if self.sc.get("directive_images") else []
            try:
                r = self._ai(AIRequest(task="directive_extraction", source_label=f"{frow['path']} {sc.label}", text=text,
                                       images=imgs, t_start=sc.t_start, t_end=sc.t_end, system=P.COMMON_SYSTEM,
                                       schema=P.DIRECTIVE_SCHEMA), sc.unit_id)
            except AIError as e:
                self._mark(did, sc.unit_id, "directives", "failed", str(e))
                self.db.log_error(run_id=self.run_id, stage="semantic:directives", message=str(e), file_id=frow["id"],
                                  unit_id=sc.unit_id)
                continue
            self._save_ai_directives(did, frow, scenes, sc, cands, r.data or {}, f"ai:{self.ai_name}:{r.model}")
            self._mark(did, sc.unit_id, "directives", "ok")
        pending = [sc for sc in scenes if self._pending(did, sc.unit_id, "directives")]
        if pending:
            self._set_doc(did, note=f"{len(pending)} 場面の指示抽出が未完了（次回再試行）")
            return False
        return True

    def _directive_from_ai(self, x: dict, sc: Scene, scenes: list[Scene], analyzer: str) -> dict | None:
        cls = x.get("directive_class")
        if cls not in DIRECTIVE_CLASSES or not str(x.get("content") or "").strip():
            return None
        ts, te = x.get("t_start"), x.get("t_end")
        lo = scenes[0].t_start if scenes[0].t_start is not None else None
        hi = scenes[-1].t_end
        if not isinstance(ts, (int, float)) or (lo is not None and hi is not None and not (lo - 1 <= ts <= hi + 1)):
            ts = None
        if not isinstance(te, (int, float)) or ts is None or te < ts:
            te = ts
        if ts is None and sc.t_start is not None:
            note_time = "AIが時刻を示さなかったため場面の範囲を記録"
            ts, te = sc.t_start, sc.t_end
        else:
            note_time = ""
        unconf = str(x.get("unconfirmed") or "")
        if note_time:
            unconf = (unconf + "；" if unconf else "") + note_time
        rel = x.get("relation_type") or "none"
        return {
            "directive_class": cls, "base_strength": x.get("base_strength") if cls == "conditional" else None,
            "content": str(x["content"])[:2000], "target_task": x.get("target_task", ""),
            "deliverable": x.get("deliverable", ""), "audience": x.get("audience", ""), "condition": x.get("condition"),
            "t_start": ts, "t_end": te, "page": sc.page, "screen": x.get("screen", ""), "operation": x.get("operation", ""),
            "speech": x.get("speech_quote", ""), "evidence": x.get("speech_quote", ""),
            "context_before": x.get("context_before", ""), "context_after": x.get("context_after", ""),
            "certainty": x.get("certainty") if x.get("certainty") in ("high", "medium", "low") else "low",
            "unconfirmed": unconf, "origin": x.get("origin") if x.get("origin") in ("explicit", "ai_inference") else "ai_inference",
            "extracted_by": analyzer,
            "discourse": _j([rel] if rel != "none" else []),
            "speaker": ("none" if x.get("origin") == "ai_inference" else  # 推論は誰の発言でもない
                        self._speaker(x.get("speaker"), None if sc.t_start is not None else "document")),
            "extra": {"relation_refers_to": x.get("relation_refers_to", ""), "relation_type": rel,
                      "speaker_claimed": x.get("speaker"), "speaker_basis": x.get("speaker_basis", "")},
        }

    @staticmethod
    def _fill_context(d: dict, scenes: list[Scene], sc: Scene) -> None:
        """AIが前後の文脈・画面を返さなかった場合、時刻同期した元の発話・画面から補う（補ったことを記録）。"""
        lines = [ln for s in scenes for ln in s.lines]
        if not lines:
            return
        if d.get("t_start") is not None:
            cands = [i for i, ln in enumerate(lines) if ln.t_start is not None]
            if not cands:
                return
            i = min(cands, key=lambda k: abs(lines[k].t_start - d["t_start"]))
        else:
            key = (d.get("speech") or d["content"])[:12]
            i = next((k for k, ln in enumerate(lines) if key and key in ln.text), None)
            if i is None:
                return
        filled = []
        if not d.get("context_before"):
            d["context_before"] = " / ".join(x.text for x in lines[max(0, i - 2):i])
            filled.append("context_before")
        if not d.get("context_after"):
            d["context_after"] = " / ".join(x.text for x in lines[i + 1:i + 3])
            filled.append("context_after")
        if not d.get("screen") and lines[i].frame:
            scr = next((x.text for x in lines if x.origin == "screen" and x.frame == lines[i].frame), "")
            d["screen"] = Path(lines[i].frame).name + (f"（画面の文字: {_clip(scr, 200)}）" if scr else "")
            filled.append("screen")
        if filled:
            d.setdefault("extra", {})["filled_from_source"] = filled

    def _save_ai_directives(self, did: int, frow, scenes, sc: Scene, cands, data: dict, analyzer: str) -> None:
        with self.db.tx():
            old = [r["id"] for r in self.db.q("SELECT id FROM directives WHERE doc_id=? AND unit_id=? AND extracted_by LIKE"
                                              " 'ai:%'", (did, sc.unit_id))]
            for oid in old:
                self.db.x("DELETE FROM directive_links WHERE from_id=? OR to_id=?", (oid, oid))
                self.db.x("DELETE FROM directives WHERE id=?", (oid,))
            matched: set[int] = set()
            for x in data.get("directives") or []:
                d = self._directive_from_ai(x, sc, scenes, analyzer)
                if d is None:
                    continue
                self._fill_context(d, scenes, sc)
                nid = self._insert_directive(did, frow, sc, d)
                inherited: set[str] = set()
                for k in x.get("matched_candidates") or []:
                    if isinstance(k, int) and 1 <= k <= len(cands):
                        matched.add(k)
                        inherited |= set(json.loads(cands[k - 1]["discourse"] or "[]"))
                        self.db.x("INSERT OR IGNORE INTO directive_links(from_id,to_id,relation,scope,basis,note,status)"
                                  " VALUES(?,?,'corroborated_by','document','rule+ai','規則候補と一致','recorded')",
                                  (nid, cands[k - 1]["id"]))
                if inherited:  # 規則候補が原文から拾った訂正・例外などの言い回しは「明示」として引き継ぐ
                    ex = json.loads(self.db.q1("SELECT extra_json FROM directives WHERE id=?", (nid,))["extra_json"])
                    ex["explicit_discourse"] = sorted(inherited)
                    self.db.x("UPDATE directives SET extra_json=? WHERE id=?", (_j(ex), nid))
            dismissed: dict[int, str] = {}
            for x in data.get("dismissed_candidates") or []:
                k = x.get("candidate")
                if isinstance(k, int) and 1 <= k <= len(cands) and k not in matched:
                    dismissed[k] = str(x.get("reason") or "")
            for k, c in enumerate(cands, 1):
                if k in matched:
                    self.db.x("UPDATE directives SET effective='merged', verification='corroborated' WHERE id=?", (c["id"],))
                elif k in dismissed:
                    # AIの「指示ではない」判断も無条件には信用せず、第3回で原文と照合する
                    self.db.x("UPDATE directives SET verification='omission_candidate', verification_notes=? WHERE id=?",
                              (_j([f"AIが指示ではないと判断: {dismissed[k]}"]), c["id"]))
                else:
                    self.db.x("UPDATE directives SET verification='omission_candidate', verification_notes=? WHERE id=?",
                              (_j(["AI抽出で言及されなかった規則候補"]), c["id"]))

    # ---------- 第3回: 照合 ----------
    def _stage3_verify(self, did: int, frow, scenes: list[Scene], local_path: Path | None) -> None:
        flagged = self._verify_local(did, frow, scenes, mode="ai")
        cap = int(self.sc.get("max_verify_per_doc", 40))
        done_ids = {r["unit_id"] for r in self.db.q("SELECT unit_id FROM sem_steps WHERE doc_id=? AND step LIKE 'verify:%'"
                                                    " AND status IN ('ok','failed_final')", (did,))}
        g = self._understanding(did)
        n = 0
        for dir_id, issues in flagged:
            if dir_id in done_ids:  # sem_steps の unit_id 欄に指示IDを入れている（step='verify:<id>'）
                continue
            if n >= cap:
                self.db.x("UPDATE directives SET verification='needs_review', verification_notes=? WHERE id=?",
                          (_j(issues + [f"照合の上限（semantic.max_verify_per_doc={cap}）のため再解析していない"]), dir_id))
                continue
            self.checkpoint()
            n += 1
            self._verify_one(did, frow, scenes, dir_id, issues, g, local_path)
        # 場面ごとの照合状態（網羅率の計算用）
        for sc in scenes:
            rows = self.db.q("SELECT verification FROM directives WHERE doc_id=? AND unit_id=? AND effective NOT IN "
                             "('merged','rejected','dismissed')", (did, sc.unit_id))
            open_ = [r for r in rows if r["verification"] in ("needs_review", "omission_candidate", "class_mismatch_unresolved")]
            self._mark(did, sc.unit_id, "verify", "ok", f"指示 {len(rows)} 件 / 要確認 {len(open_)} 件")

    def _verify_one(self, did: int, frow, scenes: list[Scene], dir_id: int, issues: list[str], g: dict | None,
                    local_path: Path | None) -> None:
        d = self.db.q1("SELECT * FROM directives WHERE id=?", (dir_id,))
        expand = float(self.sc.get("verify_expand_seconds", 45))
        win = window_lines(scenes, d["t_start"], d["t_end"], d["page"], expand)
        if not win:
            sc = next((s for s in scenes if s.unit_id == d["unit_id"]), None)
            win = sc.lines if sc else []
        extra_txt, images = self._reanalyze_media(frow, d, scenes, local_path, expand)
        target = {k: d[k] for k in ("content", "directive_class", "base_strength", "condition", "t_start", "t_end", "page",
                                    "speech", "target_task", "origin")}
        text = (f"{P.VERIFY_INSTRUCTION}\n## 資料\n{frow['path']}\n## 確認対象\n```json\n{_j(target)}\n```\n"
                f"## 指摘された問題点\n" + "\n".join(f"- {x}" for x in issues) +
                f"\n## 元の発話・画面・資料（前後 {int(expand)} 秒／前後ページまで拡大）\n{_clip(lines_text(win), 20000) or '（なし）'}\n"
                f"{extra_txt}\n## 資料全体の理解（参考）\n```json\n{self._global_brief(g)}\n```\n")
        try:
            r = self._ai(AIRequest(task="directive_verify", source_label=f"{frow['path']} 指示#{dir_id}", text=text,
                                   images=images, t_start=d["t_start"], t_end=d["t_end"], system=P.COMMON_SYSTEM,
                                   schema=P.VERIFY_SCHEMA), d["unit_id"])
        except AIError as e:
            self._mark(did, dir_id, f"verify:{dir_id}", "failed", str(e))
            self.db.x("UPDATE directives SET verification='needs_review', verification_notes=? WHERE id=?",
                      (_j(issues + [f"照合のAI呼び出しに失敗: {e}"]), dir_id))
            return
        self._apply_verdict(did, frow, scenes, d, issues, r.data or {}, f"ai:{self.ai_name}:{r.model}")
        self._mark(did, dir_id, f"verify:{dir_id}", "ok")

    def _reanalyze_media(self, frow, d, scenes: list[Scene], local_path: Path | None, expand: float):
        """曖昧な指示の前後を、元の映像・音声から取り直す（元ファイルが手元にある場合）。"""
        images: list[AIImage] = []
        if not is_time_based(frow["kind"]) or d["t_start"] is None:
            sc = next((s for s in scenes if s.unit_id == d["unit_id"]), None)
            if sc:
                images = [AIImage(p, lbl) for p, lbl in sc.images[:self.max_images]]
            return "", images
        t0 = max(0.0, d["t_start"] - expand)
        t1 = (d["t_end"] or d["t_start"]) + expand
        if local_path is not None and Path(local_path).exists() and self.sc.get("reextract_media", True):
            from .. import media
            art = self.cfg.work_dir / "artifacts" / str(frow["id"]) / f"verify_{d['id']}"
            out = []
            try:
                info = media.probe(Path(local_path))
                t1 = min(t1, info.duration - 0.2)
                if info.has_video:
                    n = max(1, self.max_images)
                    step = max((t1 - t0) / n, 1.0)
                    t = t0
                    while t < t1 and len(images) < n:
                        jpg = media.extract_frame(Path(local_path), t, art / f"v_{int(t * 1000):010d}.jpg", max_width=1600)
                        images.append(AIImage(jpg, f"{fmt_time(t)} の画面（再取得）"))
                        t += step
                if info.has_audio and self.transcriber is not None and self.transcriber.available:
                    wav = media.extract_audio(Path(local_path), t0, t1 - t0, art / "window.wav")
                    try:
                        segs = self.transcriber.transcribe(wav, t0)
                    finally:
                        wav.unlink(missing_ok=True)
                    out.append("## 該当範囲の再文字起こし（区間の切れ目をまたいで取り直したもの）")
                    out += [f"[{fmt_time(s['start'])}] {s['text']}" for s in segs] or ["（発話なし）"]
                return "\n".join(out), images
            except Exception as e:  # noqa: BLE001  再取得に失敗しても既存の材料で照合を続ける
                log.warning("照合用の映像・音声の再取得に失敗: %s", e)
        seen = []
        for ln in window_lines(scenes, d["t_start"], d["t_end"], None, expand):
            if ln.frame and ln.frame not in seen and Path(ln.frame).exists():
                seen.append(ln.frame)
                images.append(AIImage(Path(ln.frame), f"{fmt_time(ln.t_start)} の画面"))
        return "（元動画が手元にないため、保存済みの画面と文字起こしで照合）", images[:self.max_images]

    def _apply_verdict(self, did: int, frow, scenes, d, issues: list[str], data: dict, analyzer: str) -> None:
        verdict = data.get("verdict")
        quote = str(data.get("evidence_quote") or "")
        notes = issues + [f"第3回照合: {verdict} — {data.get('reason', '')}"] + ([f"照合根拠: 「{quote[:300]}」"] if quote else [])
        is_rule = d["extracted_by"] == "rule"
        if verdict == "confirmed":
            upd = {"verification": "verified"}
            if is_rule:
                upd.update(extracted_by="rule+verify", effective="effective")
            if quote and not d["speech"]:
                upd["speech"] = quote
        elif verdict == "revised" and isinstance(data.get("revised"), dict):
            rv = data["revised"]
            guard = self._class_guard(rv.get("directive_class", ""), rv.get("base_strength"), quote or rv.get("content", ""))
            if rv.get("directive_class") not in DIRECTIVE_CLASSES or guard:
                upd = {"verification": "class_mismatch_unresolved"}
                notes.append("修正案の分類が原文の言い回しと合わないため採用せず、要確認とした: " + "；".join(guard))
            else:
                upd = {"verification": "revised", "content": rv.get("content") or d["content"],
                       "directive_class": rv["directive_class"],
                       "base_strength": rv.get("base_strength") if rv["directive_class"] == "conditional" else None,
                       "condition": rv.get("condition"), "certainty": rv.get("certainty", d["certainty"])}
                if isinstance(rv.get("t_start"), (int, float)):
                    upd["t_start"] = rv["t_start"]
                    upd["t_end"] = rv.get("t_end") if isinstance(rv.get("t_end"), (int, float)) else rv["t_start"]
                notes.append(f"修正前: [{DIRECTIVE_CLASSES[d['directive_class']]}] {d['content']}")
                if is_rule:
                    upd.update(extracted_by="rule+verify", effective="effective")
        elif verdict == "rejected":
            upd = {"verification": "rejected", "effective": "dismissed" if is_rule else "rejected"}
        else:
            upd = {"verification": "needs_review"}
        upd["verification_notes"] = _j(notes)
        self.db.x(f"UPDATE directives SET {', '.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), d["id"]))
        sc = next((s for s in scenes if s.unit_id == d["unit_id"]), None)
        for x in data.get("additional_directives") or []:
            nd = self._directive_from_ai(x, sc or scenes[0], scenes, analyzer)
            if nd is None:
                continue
            nd["verification"] = "found_in_verify"
            nd["unconfirmed"] = (nd["unconfirmed"] + "；" if nd["unconfirmed"] else "") + "第3回照合で新たに見つかった指示"
            self._insert_directive(did, frow, sc, nd)
