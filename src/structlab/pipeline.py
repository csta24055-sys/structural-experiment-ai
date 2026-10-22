"""解析パイプライン: ファイル → 解析単位 → ローカル抽出 → （許可があれば）AI解析 → DB保存。

- 単位ごとに結果を確定させるので、途中で止まっても次回は未完了の単位から再開する
- 同じ content_hash の解析済み単位は再実行しない。ファイルが更新されれば新しいハッシュで解析し直す
- 失敗した単位は記録し、max_attempts まで次回以降に再試行する
"""
from __future__ import annotations

import json
import logging
import shutil
import signal
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .ai import AIError, AIImage, AIRequest, items_from_response
from .classify import rule_items
from .db import DB
from .models import Item, UnitSpec, kind_for
from .processors import UNSUPPORTED_HINTS, Ctx, Extraction, get_processor
from .processors.video import focus_spec
from .ai.base import AwaitingAnswer
from .semantic import BudgetExceeded, SemanticEngine
from .semantic.material import SCENE_TYPES
from .safety import DiskSpaceError, SafetyError, ZipLimits, dropbox_content_hash, safe_extract_zip
from .scanner import is_ignored, remote_file_of
from .sources import Source, SourceChanged

log = logging.getLogger("structlab")


class StopRun(Exception):
    """安全に処理を中断する（次回続きから）。"""


class Budget:
    def __init__(self, cfg, db: DB, run_id: int):
        self.ai = cfg.data["ai"]
        self.db = db
        self.run_id = run_id

    def check(self) -> str | None:
        u = self.db.usage_totals(self.run_id)
        a = self.ai
        if a["max_calls_per_run"] and u["calls"] >= a["max_calls_per_run"]:
            return f"AI呼び出し回数の上限（{a['max_calls_per_run']}回/実行）"
        if a["max_input_tokens_per_run"] and u["input_tokens"] >= a["max_input_tokens_per_run"]:
            return f"入力トークンの上限（{a['max_input_tokens_per_run']}/実行）"
        if a["max_output_tokens_per_run"] and u["output_tokens"] >= a["max_output_tokens_per_run"]:
            return f"出力トークンの上限（{a['max_output_tokens_per_run']}/実行）"
        if a["max_calls_total"] and self.db.usage_totals()["calls"] >= a["max_calls_total"]:
            return f"AI呼び出し回数の累計上限（{a['max_calls_total']}回）"
        return None


class Pipeline:
    def __init__(self, cfg, db: DB, source: Source, *, provider=None, provider_note: str = "",
                 transcriber=None, ocr=None, run_id: int | None = None,
                 on_progress: Callable[[], None] | None = None):
        from .media import OCR, Transcriber
        self.cfg = cfg
        self.db = db
        self.source = source
        self.provider = provider
        self.provider_note = provider_note
        self.transcriber = transcriber or Transcriber()
        self.ocr = ocr or OCR("none")
        self.run_id = run_id if run_id is not None else db.start_run()
        self.budget = Budget(cfg, db, self.run_id)
        self.on_progress = on_progress
        lim = cfg.data["limits"]
        self.max_attempts = int(lim["max_attempts"])
        self.deadline = time.time() + lim["max_runtime_minutes"] * 60 if lim["max_runtime_minutes"] else None
        self.download_limit = lim["max_download_mb_per_run"] * 1024 * 1024 if lim["max_download_mb_per_run"] else None
        self.downloaded = 0
        self.stop_reason: str | None = None
        self.ai_blocked: str | None = None
        self.stats: dict[str, int] = {"files_done": 0, "files_failed": 0, "units_done": 0, "units_failed": 0,
                                      "ai_calls": 0, "ai_failed": 0, "skipped": 0, "duplicates": 0}
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        self.semantic = SemanticEngine(
            cfg, db, ai_call=self._semantic_ai_call if provider is not None else None,
            ai_name=provider.name if provider is not None else "", checkpoint=self._checkpoint,
            transcriber=self.transcriber, ocr=self.ocr, run_id=self.run_id)
        sem = cfg.data["semantic"]
        #: 単位ごとの従来のAI解析を、意味理解の場面解析で置き換えるか
        self.defer_to_semantic = bool(provider is not None and sem.get("enabled") and sem.get("ai")
                                      and sem.get("replace_unit_analysis"))

    def _semantic_ai_call(self, req: AIRequest, uid: int | None):
        """意味理解層からの AI 呼び出し。使用量の上限・記録は単位ごとの解析と共通。"""
        if self.ai_blocked:
            raise BudgetExceeded(self.ai_blocked)
        why = self.budget.check()
        if why:
            self.ai_blocked = why
            raise BudgetExceeded(why)
        prov = self.provider
        try:
            resp = prov.analyze(req)
        except AIError:
            self.db.x("INSERT INTO api_usage(run_id,unit_id,provider,model,input_tokens,output_tokens,images,ok,created_at)"
                      " VALUES(?,?,?,?,0,0,?,0,?)", (self.run_id, uid, prov.name, prov.model, len(req.images), time.time()))
            self.stats["ai_failed"] += 1
            raise
        self.db.x("INSERT INTO api_usage(run_id,unit_id,provider,model,input_tokens,output_tokens,images,ok,created_at)"
                  " VALUES(?,?,?,?,?,?,?,1,?)", (self.run_id, uid, prov.name, resp.model, resp.input_tokens,
                                                  resp.output_tokens, len(req.images), time.time()))
        self.stats["ai_calls"] += 1
        return resp

    def _record_run_checks(self) -> None:
        """取得工程（A）と運用・全体（O）の項目、実行の最後の状態で記録する抽出工程の項目（B14・B15）。"""
        if not self.cfg.data.get("checks", {}).get("enabled", True):
            return
        from .checks.acquisition import record_acquisition
        from .checks.operations import record_operations
        try:
            record_acquisition(self.db, self.cfg, self.run_id)
            record_operations(self.db, self.cfg, self.run_id, ai_note=self.provider_note,
                              ai_blocked=self.ai_blocked or self.semantic.ai_stopped,
                              external_allowed=bool(self.provider is not None and self.provider.external))
        except Exception as e:  # noqa: BLE001
            self.db.log_error(run_id=self.run_id, stage="checks:run", message=str(e), detail=traceback.format_exc())
            log.error("全体チェックの記録に失敗: %s", e)

    def _run_extraction_checks(self, row, local_path: Path | None) -> None:
        """資料抽出工程に組み込んだチェック（時刻対応・文字起こし・OCR・数値・数式・Excel再計算・読み取り失敗）。"""
        if not self.cfg.data.get("checks", {}).get("enabled", True):
            return
        from .checks.extraction import check_file
        row = self.db.q1("SELECT * FROM files WHERE id=?", (row["id"],))
        try:
            check_file(self.db, self.cfg, row, local_path, self.run_id)
        except (StopRun, DiskSpaceError):
            raise
        except Exception as e:  # noqa: BLE001  チェックの失敗で解析結果を失わない
            self.db.log_error(run_id=self.run_id, stage="checks:extraction", message=str(e),
                              detail=traceback.format_exc(), file_id=row["id"])
            log.error("抽出チェックに失敗 %s: %s", row["path"], e)

    def _run_semantic(self, row, local_path: Path | None) -> None:
        row = self.db.q1("SELECT * FROM files WHERE id=?", (row["id"],))
        try:
            self.semantic.process_file(row, local_path)
        except (StopRun, DiskSpaceError):
            raise
        except Exception as e:  # noqa: BLE001  意味理解の失敗でファイルの抽出結果を失わない
            self.db.log_error(run_id=self.run_id, stage="semantic", message=str(e), detail=traceback.format_exc(),
                              file_id=row["id"])
            log.error("意味理解の処理に失敗 %s: %s", row["path"], e)

    # ---------- 停止制御 ----------
    def install_signal_handlers(self) -> None:
        def handler(signum, frame):  # noqa: ARG001
            self.stop_reason = f"シグナル {signum} を受信"
            log.warning("停止要求を受け付けました。現在の単位が終わり次第停止します。")
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def should_stop(self) -> bool:
        if self.stop_reason:
            return True
        if (self.cfg.work_dir / "STOP").exists():
            self.stop_reason = "STOP ファイルを検出"
            return True
        if self.deadline and time.time() >= self.deadline:
            self.stop_reason = "最大実行時間に到達"
            return True
        return False

    def _checkpoint(self) -> None:
        if self.on_progress:
            self.on_progress()
        if self.should_stop():
            raise StopRun(self.stop_reason)

    # ---------- 全体 ----------
    def run(self) -> dict[str, Any]:
        try:
            self.ai_backfill()
            attempted: set[int] = set()
            while True:
                rows = [r for r in self.db.q("SELECT * FROM files WHERE parent_id IS NULL AND status='ready'"
                                             " ORDER BY path") if r["id"] not in attempted]
                if not rows:
                    break
                for row in rows:
                    self._checkpoint()
                    attempted.add(row["id"])  # 1回の実行で同じファイルは1度だけ（失敗分は次回再試行）
                    self.process_file(row)
            # 意味理解の未完了分（AIを後から許可した場合など）と、資料横断の統合
            self.semantic.backfill()
            from .semantic.corpus import integrate
            integrate(self.semantic)
            if self.cfg.data.get("checks", {}).get("enabled", True):
                from .checks.integration import check_corpus
                check_corpus(self.db, self.run_id)
            self._record_run_checks()
        except StopRun as e:
            log.warning("処理を中断しました: %s（次回の実行で続きから再開します）", e)
        except DiskSpaceError as e:
            self.stop_reason = str(e)
            log.error("%s", e)
        return {**self.stats, "stop_reason": self.stop_reason, "ai_blocked": self.ai_blocked or self.semantic.ai_stopped,
                "downloaded_mb": round(self.downloaded / 1e6, 1)}

    # ---------- ファイル ----------
    def _set_file(self, fid: int, status: str, detail: str | None = None, **extra) -> None:
        cols = ", ".join(f"{k}=?" for k in extra)
        self.db.x(f"UPDATE files SET status=?, status_detail=?, updated_at=?{', ' + cols if cols else ''} WHERE id=?",
                  (status, detail, time.time(), *extra.values(), fid))

    def _file_error(self, row, stage: str, err: BaseException, retryable: bool = True) -> None:
        attempts = (row["attempts"] or 0) + 1
        final = (not retryable) or attempts >= self.max_attempts
        self.db.log_error(run_id=self.run_id, stage=stage, message=str(err), detail=traceback.format_exc(),
                          file_id=row["id"])
        self._set_file(row["id"], "failed" if final else "ready",
                       f"{stage}: {err}" + ("" if final else f"（次回再試行 {attempts}/{self.max_attempts}）"),
                       attempts=attempts, last_error=str(err)[:2000])
        if final:
            self.stats["files_failed"] += 1
        log.error("[%s] %s: %s", row["path"], stage, err)

    def process_file(self, row, local_path: Path | None = None, depth: int = 0) -> None:
        fid, path, kind = row["id"], row["path"], row["kind"]
        lim = self.cfg.data["limits"]
        if row["size"] and row["size"] > lim["max_file_size_mb"] * 1024 * 1024:
            self._set_file(fid, "skipped", f"サイズ上限超過（{row['size'] / 1e6:.0f}MB > {lim['max_file_size_mb']}MB）")
            self.stats["skipped"] += 1
            return
        if kind != "zip" and get_processor(kind) is None:
            self._set_file(fid, "unsupported", UNSUPPORTED_HINTS.get(kind, UNSUPPORTED_HINTS["other"]))
            self.stats["skipped"] += 1
            return
        if self.cfg.data["stability"]["dedupe_identical_content"] and row["content_hash"]:
            dup = self.db.q1("SELECT path FROM files WHERE content_hash=? AND id!=? AND status IN ('done','partial')"
                             " AND processed_hash=content_hash ORDER BY id LIMIT 1", (row["content_hash"], fid))
            if dup:
                self._set_file(fid, "duplicate", f"内容が同一: {dup['path']}", processed_hash=row["content_hash"])
                self.stats["duplicates"] += 1
                return

        self._set_file(fid, "processing", None)
        log.info("処理開始: %s", path)
        cache_file = None
        try:
            if local_path is None:
                if self.download_limit and self.downloaded + (row["size"] or 0) > self.download_limit:
                    self._set_file(fid, "ready", "1回あたりのダウンロード上限のため次回に持ち越し")
                    raise StopRun("ダウンロード量の上限に到達")
                cache_file = self.cfg.cache_dir / f"{fid}_{(row['content_hash'] or 'x')[:12]}{row['ext'] or ''}"
                try:
                    local_path = self.source.fetch(remote_file_of(row), cache_file,
                                                   min_free_mb=lim["min_free_disk_mb"])
                except SourceChanged as e:
                    # アップロード・更新中。次回スキャンで新しい版として安定を待つ
                    self._set_file(fid, "discovered", f"取得中に変更を検出: {e}", stable_since=time.time())
                    return
                if not self.source.is_local_passthrough():
                    self.downloaded += row["size"] or 0
        except StopRun:
            raise
        except DiskSpaceError:
            self._set_file(fid, "ready", "ディスク容量不足のため待機")
            raise
        except Exception as e:  # noqa: BLE001
            self._file_error(row, "download", e)
            return

        finished = False
        try:
            if kind == "zip":
                finished = self._process_zip(row, local_path, depth)
            else:
                finished = self._process_units(row, local_path)
                if finished:
                    self._run_extraction_checks(row, local_path)
                    self._run_semantic(row, local_path)  # 元ファイルが手元にあるうちに（照合時の再取得用）
        except StopRun:
            self._set_file(fid, "ready", "中断（次回続きから）")
            raise
        except DiskSpaceError:
            self._set_file(fid, "ready", "ディスク容量不足のため中断")
            raise
        except Exception as e:  # noqa: BLE001
            self._file_error(row, "plan", e)
        finally:
            keep = self.cfg.data["limits"]["keep_downloads"]
            if cache_file and finished and not keep and not self.source.is_local_passthrough():
                Path(cache_file).unlink(missing_ok=True)

    def _ctx(self, fid: int, unit_id: int | None) -> Ctx:
        art = self.cfg.work_dir / "artifacts" / str(fid) / (str(unit_id) if unit_id else "plan")
        return Ctx(cfg=self.cfg, artifacts_dir=art, ocr=self.ocr, transcriber=self.transcriber,
                   ai_enabled=self.provider is not None,
                   timeline_lookup=lambda s, e: [dict(r) for r in self.db.q(
                       "SELECT * FROM timeline WHERE file_id=? AND t_end>=? AND t_start<=? ORDER BY t_start",
                       (fid, s, e))])

    def _process_units(self, row, local_path: Path) -> bool:
        fid, h = row["id"], row["content_hash"] or ""
        proc = get_processor(row["kind"])
        assert proc is not None
        existing = self.db.q1("SELECT COUNT(*) c FROM units WHERE file_id=? AND file_hash=?", (fid, h))["c"]
        if existing == 0:
            specs = proc.plan(local_path, self._ctx(fid, None))
            with self.db.tx():
                for s in specs:
                    self.db.x("INSERT OR IGNORE INTO units(file_id,file_hash,unit_type,locator,seq,updated_at)"
                              " VALUES(?,?,?,?,?,?)",
                              (fid, h, s.unit_type, json.dumps(s.locator, ensure_ascii=False, sort_keys=True),
                               s.seq, time.time()))
            log.info("  %d 単位に分割: %s", len(specs), row["path"])
        tried: set[int] = set()
        while True:  # 重要場面などの追加単位が増える場合があるので、新しい単位がなくなるまで繰り返す
            units = [u for u in self.db.q(
                "SELECT * FROM units WHERE file_id=? AND file_hash=? AND (status='pending' OR"
                " (status='failed' AND attempts<?)) ORDER BY seq, id", (fid, h, self.max_attempts))
                if u["id"] not in tried]
            if not units:
                break
            for u in units:
                self._checkpoint()
                tried.add(u["id"])
                self._process_unit(row, u, proc, local_path)
        return self._finalize_file(row)

    def _finalize_file(self, row) -> bool:
        fid, h = row["id"], row["content_hash"] or ""
        c = {r["status"]: r["c"] for r in self.db.q(
            "SELECT status, COUNT(*) c FROM units WHERE file_id=? AND file_hash=? GROUP BY status", (fid, h))}
        retry = self.db.q1("SELECT COUNT(*) c FROM units WHERE file_id=? AND file_hash=? AND status='failed' AND attempts<?",
                           (fid, h, self.max_attempts))["c"]
        failed = c.get("failed", 0)
        if c.get("pending", 0) or c.get("running", 0):
            self._set_file(fid, "ready", "未処理の単位あり")
            return False
        if retry:
            self._set_file(fid, "ready", f"{retry} 単位が失敗（次回再試行）", attempts=(row["attempts"] or 0) + 1)
            return True
        if failed:
            self._set_file(fid, "partial", f"{failed} 単位が再試行上限まで失敗", processed_hash=h)
        else:
            self._set_file(fid, "done", None, processed_hash=h, attempts=0, last_error=None)
            self.stats["files_done"] += 1
        # 古い版の結果は無効化（履歴として残すが出力には使わない）
        self.db.x("UPDATE units SET status='superseded' WHERE file_id=? AND file_hash!=? AND status!='superseded'",
                  (fid, h))
        return True

    # ---------- 単位 ----------
    def _source_label(self, row, proc, spec: UnitSpec) -> str:
        return f"{row['path']} {proc.label(spec)}".strip()

    def _process_unit(self, row, u, proc, local_path: Path) -> None:
        fid, uid = row["id"], u["id"]
        spec = UnitSpec(u["unit_type"], json.loads(u["locator"]), u["seq"])
        self.db.x("UPDATE units SET status='running', attempts=attempts+1, updated_at=? WHERE id=?", (time.time(), uid))
        ctx = self._ctx(fid, uid)
        try:
            ex = proc.extract(local_path, spec, ctx)
        except (StopRun, DiskSpaceError):
            self.db.x("UPDATE units SET status='pending' WHERE id=?", (uid,))
            raise
        except Exception as e:  # noqa: BLE001
            self.db.x("UPDATE units SET status='failed', last_error=?, updated_at=? WHERE id=?",
                      (str(e)[:2000], time.time(), uid))
            self.db.log_error(run_id=self.run_id, stage="extract", message=str(e), detail=traceback.format_exc(),
                              file_id=fid, unit_id=uid)
            self.stats["units_failed"] += 1
            log.error("  単位の抽出に失敗 %s: %s", self._source_label(row, proc, spec), e)
            return
        page = ex.page
        with self.db.tx():
            # 再試行時に重複しないよう、この単位の既存結果を消してから保存
            self.db.x("DELETE FROM items WHERE unit_id=?", (uid,))
            self.db.x("DELETE FROM timeline WHERE unit_id=?", (uid,))
            self.db.add_items([self._item_row(row, uid, it) for it in rule_items(ex.segments)])
            for t in ex.timeline:
                self.db.x("INSERT INTO timeline(unit_id,file_id,t_start,t_end,speech,frame_path,frame_time,screen_text,"
                          "important,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (uid, fid, t["t_start"], t["t_end"], t.get("speech"), t.get("frame_path"), t.get("frame_time"),
                           t.get("screen_text"), int(t.get("important", 0)), t.get("reason")))
            for k, f in enumerate(ex.followups):
                self.db.x("INSERT OR IGNORE INTO units(file_id,file_hash,unit_type,locator,seq,updated_at)"
                          " VALUES(?,?,?,?,?,?)",
                          (fid, row["content_hash"] or "", f.unit_type,
                           json.dumps(f.locator, ensure_ascii=False, sort_keys=True), 1_000_000 + spec.seq * 100 + k,
                           time.time()))
            ai_status = "not_requested" if self.provider is None else "pending"
            if self.provider is None and self.cfg.data["ai"]["provider"] != "none":
                ai_status = "skipped_no_permission"
            self.db.x("UPDATE units SET status='done', ai_status=?, last_error=NULL, extract_json=?, updated_at=?"
                      " WHERE id=?", (ai_status, json.dumps(self._extract_record(ex), ensure_ascii=False, default=str),
                                      time.time(), uid))
        self.stats["units_done"] += 1
        log.info("  抽出完了 %s（図表等の要AI解析: %s）", self._source_label(row, proc, spec),
                 "; ".join(ex.needs_ai) or "なし")
        if self.provider is not None:
            if self.defer_to_semantic and spec.unit_type in SCENE_TYPES:
                self.db.x("UPDATE units SET ai_status='deferred_semantic' WHERE id=?", (uid,))
                return
            self._ai_unit(row, uid, spec, proc, ex, page)

    @staticmethod
    def _extract_record(ex: Extraction) -> dict:
        return {"data": ex.data, "text": ex.text, "ai_text": ex.ai_text, "needs_ai": ex.needs_ai,
                "images": [[str(p), lbl] for p, lbl in ex.images], "page": ex.page,
                "t_start": ex.t_start, "t_end": ex.t_end}

    def _item_row(self, row, uid: int, it: Item) -> dict:
        d = asdict(it)
        d.update({"unit_id": uid, "file_id": row["id"], "file_hash": row["content_hash"] or "",
                  "source_path": row["path"]})
        return d

    # ---------- AI ----------
    def _ai_unit(self, row, uid: int, spec: UnitSpec, proc, ex: Extraction, page: int | None) -> None:
        if self.ai_blocked:
            self.db.x("UPDATE units SET ai_status='budget_exceeded' WHERE id=?", (uid,))
            return
        why = self.budget.check()
        if why:
            self.ai_blocked = why
            log.warning("AI使用量の上限に達したため、以降のAI解析は次回に持ち越します: %s", why)
            self.db.x("UPDATE units SET ai_status='budget_exceeded' WHERE id=?", (uid,))
            return
        max_img = int(self.cfg.data["ai"]["max_images_per_call"])
        req = AIRequest(task=spec.unit_type, source_label=self._source_label(row, proc, spec), text=ex.ai_text,
                        images=[AIImage(Path(p), lbl) for p, lbl in ex.images[:max_img] if Path(p).exists()],
                        t_start=ex.t_start, t_end=ex.t_end)
        self.db.x("UPDATE units SET ai_attempts=ai_attempts+1 WHERE id=?", (uid,))
        prov = self.provider
        try:
            resp = prov.analyze(req)
        except AwaitingAnswer:  # 回答待ち（候補 G5）: 失敗ではないので試行回数も状態も元に戻す（次回続きから）
            self.db.x("UPDATE units SET ai_attempts=ai_attempts-1 WHERE id=?", (uid,))
            return
        except AIError as e:
            self._ai_failed(row, uid, e, e.retryable, len(req.images))
            return
        except Exception as e:  # noqa: BLE001
            self._ai_failed(row, uid, e, True, len(req.images))
            return
        self.db.x("INSERT INTO api_usage(run_id,unit_id,provider,model,input_tokens,output_tokens,images,ok,created_at)"
                  " VALUES(?,?,?,?,?,?,?,1,?)", (self.run_id, uid, prov.name, resp.model, resp.input_tokens,
                                                  resp.output_tokens, len(req.images), time.time()))
        analyzer = f"{prov.name}:{resp.model}"
        # 根拠照合の対象はローカル抽出結果（本文・OCR・文字起こし・セル/数式一覧など）
        items, rejected = items_from_response(resp.data, ex.text + "\n" + ex.ai_text, analyzer=analyzer, page=page,
                                              t_start=ex.t_start, t_end=ex.t_end)
        with self.db.tx():
            self.db.x("DELETE FROM items WHERE unit_id=? AND analyzer!='local-rules'", (uid,))
            self.db.add_items([self._item_row(row, uid, it) for it in items])
            if spec.unit_type == "video_segment" and self.cfg.data["video"].get("focus_analysis") \
                    and spec.locator.get("has_video"):
                for k, m in enumerate((resp.data or {}).get("important_moments") or []):
                    try:
                        t = float(m["time_sec"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not (ex.t_start - 1 <= t <= ex.t_end + 1):
                        continue
                    if self._focus_covered(row, t):
                        continue  # 既に同じ時刻を追加解析する単位がある
                    fs = focus_spec(t - 5, t + 15, "AI検出: " + str(m.get("reason", ""))[:200],
                                    float(spec.locator.get("duration") or ex.t_end))
                    self.db.x("INSERT OR IGNORE INTO units(file_id,file_hash,unit_type,locator,seq,updated_at)"
                              " VALUES(?,?,?,?,?,?)", (row["id"], row["content_hash"] or "", "video_focus",
                                                      json.dumps(fs.locator, ensure_ascii=False, sort_keys=True),
                                                      2_000_000 + spec.seq * 100 + k, time.time()))
            self.db.x("UPDATE units SET ai_status='done', ai_error=? WHERE id=?",
                      (json.dumps(rejected, ensure_ascii=False)[:5000] if rejected else None, uid))
        self.stats["ai_calls"] += 1

    def _focus_covered(self, row, t: float) -> bool:
        for u in self.db.q("SELECT locator FROM units WHERE file_id=? AND file_hash=? AND unit_type='video_focus'",
                           (row["id"], row["content_hash"] or "")):
            lo = json.loads(u["locator"])
            if lo["start"] <= t <= lo["end"]:
                return True
        return False

    def _ai_failed(self, row, uid: int, e: BaseException, retryable: bool, n_images: int) -> None:
        self.db.x("INSERT INTO api_usage(run_id,unit_id,provider,model,input_tokens,output_tokens,images,ok,created_at)"
                  " VALUES(?,?,?,?,0,0,?,0,?)", (self.run_id, uid, self.provider.name, self.provider.model, n_images,
                                                   time.time()))
        att = self.db.q1("SELECT ai_attempts FROM units WHERE id=?", (uid,))["ai_attempts"]
        status = "failed" if (retryable and att < self.max_attempts) else "failed_final"
        self.db.x("UPDATE units SET ai_status=?, ai_error=? WHERE id=?", (status, str(e)[:2000], uid))
        self.db.log_error(run_id=self.run_id, stage="ai", message=str(e), file_id=row["id"], unit_id=uid)
        self.stats["ai_failed"] += 1
        log.error("  AI解析に失敗（%s）: %s", "次回再試行" if status == "failed" else "再試行しない", e)

    def ai_backfill(self) -> None:
        """ローカル抽出は済んでいるが AI 解析が未完了の単位を処理する（許可・予算が後から得られた場合など）。"""
        if self.provider is None:
            return
        rows = self.db.q(
            "SELECT u.*, f.path, f.content_hash fh, f.kind FROM units u JOIN files f ON f.id=u.file_id"
            " WHERE u.status='done' AND u.file_hash=f.content_hash AND (u.ai_status IN"
            " ('pending','budget_exceeded','skipped_no_permission','not_requested') OR"
            " (u.ai_status='failed' AND u.ai_attempts<?)) AND u.unit_type!='zip_manifest' ORDER BY u.file_id, u.seq",
            (self.max_attempts,))
        if rows:
            log.info("AI未解析の単位 %d 件を処理します", len(rows))
        for u in rows:
            self._checkpoint()
            if self.ai_blocked:
                break
            rec = json.loads(u["extract_json"] or "{}")
            images = [(Path(p), lbl) for p, lbl in rec.get("images", [])]
            proc = get_processor(u["kind"])
            if proc is None:
                continue
            if any(not p.exists() for p, _ in images) or (u["kind"] == "pdf" and not images):
                # 画像（ページ画像・キーフレーム）が残っていないので抽出からやり直す
                self.db.x("UPDATE units SET status='pending' WHERE id=?", (u["id"],))
                self.db.x("UPDATE files SET status='ready' WHERE id=? AND status IN ('done','partial')", (u["file_id"],))
                continue
            ex = Extraction(text=rec.get("text", ""), ai_text=rec.get("ai_text", ""), images=images,
                            page=rec.get("page"), t_start=rec.get("t_start"), t_end=rec.get("t_end"))
            spec = UnitSpec(u["unit_type"], json.loads(u["locator"]), u["seq"])
            frow = self.db.q1("SELECT * FROM files WHERE id=?", (u["file_id"],))
            self._ai_unit(frow, u["id"], spec, proc, ex, ex.page)

    # ---------- ZIP ----------
    def _process_zip(self, row, zpath: Path, depth: int) -> bool:
        zc = self.cfg.data["zip"]
        fid = row["id"]
        h = row["content_hash"] or ""
        if depth >= int(zc["max_nesting_depth"]):
            self._set_file(fid, "skipped", f"ZIPの入れ子が深すぎます（上限 {zc['max_nesting_depth']}）")
            return True
        dest = self.cfg.cache_dir / f"zip_{fid}_{h[:12]}"
        marker = dest / ".structlab_extracted.json"
        if marker.exists():
            manifest = json.loads(marker.read_text(encoding="utf-8"))
        else:
            if dest.exists():
                shutil.rmtree(dest)
            limits = ZipLimits(max_total_bytes=int(zc["max_total_uncompressed_mb"]) * 1024 * 1024,
                               max_files=int(zc["max_files"]), max_ratio=float(zc["max_compression_ratio"]))
            try:
                members, skipped = safe_extract_zip(zpath, dest / "x", limits,
                                                    self.cfg.data["limits"]["min_free_disk_mb"])
            except SafetyError as e:
                if dest.exists():
                    shutil.rmtree(dest)
                if isinstance(e, DiskSpaceError):
                    raise
                self._file_error(row, "zip", e, retryable=False)
                return True
            manifest = {"members": [{"name": m.name, "size": m.size} for m in members], "skipped": skipped}
            marker.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        # ZIP の一覧・スキップ理由を単位として記録
        with self.db.tx():
            self.db.x("INSERT OR IGNORE INTO units(file_id,file_hash,unit_type,locator,seq,status,attempts,extract_json,"
                      "updated_at) VALUES(?,?,'zip_manifest','{}',0,'done',1,?,?)",
                      (fid, h, json.dumps(manifest, ensure_ascii=False), time.time()))
            uid = self.db.q1("SELECT id FROM units WHERE file_id=? AND file_hash=? AND unit_type='zip_manifest'",
                             (fid, h))["id"]
            self.db.x("DELETE FROM items WHERE unit_id=?", (uid,))
            for s in manifest["skipped"]:
                self.db.add_items([self._item_row(row, uid, Item(
                    category="unknowns_contradictions", statement=f"ZIP内のエントリを安全のため展開しませんでした: {s}",
                    origin="system", verification="not_applicable"))])
        patterns = list(self.cfg.data["stability"]["ignore_patterns"])
        present: set[str] = set()
        children = []
        now = time.time()
        for m in manifest["members"]:
            if is_ignored(m["name"], patterns):
                continue
            cpath = f"{row['path']}!/{m['name']}"
            key = cpath.lower()
            present.add(key)
            local = dest / "x" / m["name"]
            ch = dropbox_content_hash(local)
            kind, ext = kind_for(m["name"])
            c = self.db.q1("SELECT * FROM files WHERE path_key=?", (key,))
            if c is None:
                self.db.x("INSERT INTO files(path,path_key,parent_id,kind,ext,size,server_modified,content_hash,"
                          "first_seen,last_seen,stable_since,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ready',?)",
                          (cpath, key, fid, kind, ext, m["size"], row["server_modified"], ch, now, now, now, now))
            elif c["content_hash"] != ch:
                self.db.x("UPDATE files SET content_hash=?, size=?, status='ready', last_seen=?, attempts=0 WHERE id=?",
                          (ch, m["size"], now, c["id"]))
            else:
                st = "ready" if c["status"] in ("deleted", "processing", "discovered") else c["status"]
                self.db.x("UPDATE files SET last_seen=?, status=? WHERE id=?", (now, st, c["id"]))
            children.append((key, local))
        for c in self.db.q("SELECT id, path_key FROM files WHERE parent_id=? AND status!='deleted'", (fid,)):
            if c["path_key"] not in present:
                self.db.x("UPDATE files SET status='deleted', status_detail='ZIPから削除された' WHERE id=?", (c["id"],))
        for key, local in children:
            c = self.db.q1("SELECT * FROM files WHERE path_key=?", (key,))
            if c["status"] == "ready":
                self._checkpoint()
                self.process_file(c, local_path=local, depth=depth + 1)
        pending = self.db.q1("SELECT COUNT(*) n FROM files WHERE parent_id=? AND status IN ('ready','processing')",
                             (fid,))["n"]
        if pending:
            self._set_file(fid, "ready", f"ZIP内の {pending} ファイルが未完了")
            return False
        self._set_file(fid, "done", None, processed_hash=h)
        self.stats["files_done"] += 1
        shutil.rmtree(dest, ignore_errors=True)
        return True
