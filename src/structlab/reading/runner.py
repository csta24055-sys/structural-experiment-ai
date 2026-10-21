"""資料読み取り基盤の実行（`structlab read`）。元資料は読むだけで、結果は作業フォルダ（既定 work/reading）にだけ書く。

1. 一覧 → 棚卸し（重複・補助ファイルの判別）
2. 代表ファイルごとにローカルの読み取り（無料）: PDF は位置付きテキスト層、動画は場面分割・代表画面・OCR・文字起こし
3. （許可がある場合だけ）AI による画像の書き起こし。結果はファイルに保存し、同じ内容なら再送しない
4. 照合（無料）: 書き起こしをテキスト層・OCR・対応する PDF のページと突き合わせる
5. 報告: inventory.md、files/<資料>/reading.md、summary.md（精度の指標・要確認・未照合）、estimate.md
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from ..models import RemoteFile, fmt_time
from . import ai_read, verify
from .inventory import as_dicts, build_inventory, inventory_markdown

READ_KINDS = {"pdf", "video", "audio"}
#: ローカルの読み取り方法を変えたら上げる（保存済みの読み取り結果を使い直さず、読み直すため）
READ_VERSION = 6
TERM = re.compile(r"[一-龥ァ-ヶー]{2,}")


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-.ぁ-んァ-ヶ一-龥]+", "_", name).strip("_")[:80]


def _key(rf: RemoteFile) -> str:
    return (rf.content_hash or hashlib.sha256(rf.path.encode()).hexdigest())[:16]


def role_for(cfg, path: str) -> dict:
    """資料の役割（configs の reading.roles。例: 先生・生徒の実験経過動画）。一致しなければ {}。"""
    for r in cfg.data.get("reading", {}).get("roles", []) or []:
        full = "/" + path.lstrip("/")  # 取得元の相対パスでも「*/フォルダ/*」で一致させる
        if any(fnmatch.fnmatch(full, pat) for pat in r.get("match", [])):
            return {k: v for k, v in r.items() if k != "match"}
    return {}


def remux(src: Path, dest: Path) -> Path:
    """映像・音声をそのまま（再圧縮せずに）MKV に入れ直す。目次の不完全な録画で、途中の画面の取り出しが
    先頭からの読み直しになり極端に遅くなるのを防ぐ（内容は変わらない）。
    iPhone のカメラ動画には MKV に入れられない音声（空間オーディオ等）があるので、普通の音声トラックを1本だけ入れる。
    入れ直せない場合は元のファイルをそのまま使う（遅いが内容は同じ）。"""
    from .. import media
    audio = _plain_audio_index(src)
    amap = ["-map", f"0:{audio}"] if audio is not None else []
    try:
        media._run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", *amap,
                    "-c", "copy", str(dest)], timeout=3600)
        return dest
    except media.MediaError:
        dest.unlink(missing_ok=True)
        return src


def _plain_audio_index(src: Path) -> int | None:
    """ffmpeg で扱える最初の音声トラックの番号（無ければ None）。"""
    from .. import media
    try:
        out = media._run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index,codec_name",
                          "-of", "json", str(src)], timeout=120).stdout
    except media.MediaError:
        return None
    for st in json.loads(out).get("streams", []):
        if st.get("codec_name") in ("aac", "mp3", "alac", "pcm_s16le", "pcm_s24le", "pcm_f32le", "opus", "ac3", "eac3", "flac"):
            return int(st["index"])
    return None


def vocabulary(pdf_records: list[dict], base: str = "", limit: int = 60) -> str:
    """PDF の文字情報から専門用語の候補を集め、文字起こしの手がかりにする。"""
    c: Counter = Counter()
    for rec in pdf_records:
        for p in rec["pages"]:
            c.update(TERM.findall(p["text_layer"]))
    terms = [t for t, _ in c.most_common(limit * 2) if len(t) <= 12][:limit]
    return (base.rstrip("。") + "。" if base else "") + "、".join(terms)


class Reader:
    def __init__(self, cfg, *, out_dir: Path | None = None, provider=None, escalation=None, log=print):
        self.cfg = cfg
        self.out = Path(out_dir or cfg.work_dir / "reading")
        self.provider = provider
        self.escalation = escalation if provider is not None else None
        # Batch API（50% 引き・結果は最大 24 時間後）。reading.batch: true かつ provider が対応しているとき
        self.batch = bool(provider is not None and cfg.data.get("reading", {}).get("batch")
                          and hasattr(provider, "analyze_batch"))
        self.pending: list = []
        self.log = log
        self.lock = threading.Lock()
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0, "by_model": {}}
        self.pdf_vocab = ""

    # ---------- 1. 棚卸し ----------
    def inventory(self, files: list[RemoteFile]):
        items = build_inventory(files, list(self.cfg.data["stability"]["ignore_patterns"]))
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "inventory.md").write_text(inventory_markdown(items), encoding="utf-8")
        (self.out / "inventory.json").write_text(json.dumps(as_dicts(items), ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        return items

    # ---------- 2. ローカルの読み取り ----------
    def local_read(self, source, rf: RemoteFile, kind: str, transcriber) -> dict:
        d = self.out / "files" / f"{_safe(PurePosixPath(rf.path).stem)}_{_key(rf)[:8]}"
        rec_path = d / "record.json"
        if rec_path.exists():
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            if rec.get("content_hash") == rf.content_hash and rec.get("complete") \
                    and rec.get("read_version") == READ_VERSION and rec.get("role", {}) == role_for(self.cfg, rf.path):
                self.log(f"[済] {rf.path}")
                return rec
        self.log(f"[読み取り] {rf.path}（{rf.size / 1e6:,.1f} MB）")
        cache = self.cfg.cache_dir / f"read_{_key(rf)}{PurePosixPath(rf.path).suffix.lower()}"
        local = source.fetch(rf, cache, int(self.cfg.data["limits"]["min_free_disk_mb"]))
        t0 = time.time()
        try:
            if kind == "pdf":
                from .pdf_read import read_pdf
                rec = read_pdf(local, d / "pages")
                self._ocr_pdf_images(rec)
            else:
                from .. import media
                from .video_read import read_video
                ocr = media.OCR(self.cfg.data["ocr"]["backend"], self.cfg.data["ocr"]["languages"])
                rv = self.cfg.data.get("reading", {})
                mkv = remux(Path(local), self.cfg.cache_dir / f"read_{_key(rf)}.mkv")
                try:
                    role = role_for(self.cfg, rf.path)
                    rec = read_video(mkv, d / "frames", ocr=ocr, transcriber=transcriber,
                                     interval=float(role.get("interval", rv.get("scene_interval_seconds", 1.0))),
                                     max_events_per_scene=int(rv.get("max_events_per_scene", 4)),
                                     detail=role.get("kind") == "lecture_detail",
                                     role=role, meta_path=Path(local), log=self.log)
                finally:
                    if mkv != Path(local):
                        mkv.unlink(missing_ok=True)
        finally:
            if not getattr(source, "is_local_passthrough", lambda: False)():
                Path(local).unlink(missing_ok=True)  # 動画は大きいので読み終えたら消す（画面は保存済み）
        rec.update({"path": rf.path, "content_hash": rf.content_hash, "size": rf.size, "dir": str(d),
                    "local_seconds": round(time.time() - t0, 1), "complete": True, "read_version": READ_VERSION})
        rec.setdefault("role", role_for(self.cfg, rf.path))
        d.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return rec

    def _ocr_pdf_images(self, rec: dict) -> None:
        """図・写真のあるページは、画像の中の文字も照合に使えるようOCRしておく（テキスト層には含まれないため）。"""
        from .. import media
        from .video_read import ocr_image
        ocr = media.OCR(self.cfg.data["ocr"]["backend"], self.cfg.data["ocr"]["languages"])
        for p in rec["pages"]:
            if p["needs_vision"] and ocr.available:
                p["ocr"] = ocr_image(ocr, Path(p["render"]))

    # ---------- 3. AI の書き起こし ----------
    def _ai_cached(self, rec: dict, item_key: str, build, provider=None) -> dict | None:
        """AI の結果をファイルに保存し、同じ資料・同じ項目なら呼び直さない（失敗は未照合として残る）。"""
        provider = provider or self.provider
        path = Path(rec["dir"]) / "ai" / f"{item_key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["data"]
        if provider is None:
            return None
        if self.batch:  # まとめて送る（Batch API）: ここでは依頼を集めるだけ。結果は保存され、次の周回で読まれる
            with self.lock:
                if str(path) not in {str(x[0]) for x in self.pending}:
                    self.pending.append((path, build()))
            return None
        limit = self.cfg.data.get("reading", {}).get("max_ai_calls")
        with self.lock:
            if limit and self.usage["calls"] >= int(limit):
                return None
            self.usage["calls"] += 1
        req = build()
        try:
            resp = provider.analyze(req)
        except Exception as e:  # noqa: BLE001  1件の失敗で全体を止めない（未照合として残る）
            with self.lock:
                self.usage["failed"] += 1
                self.usage["calls"] -= 1
            self.log(f"  AI の読み取りに失敗（{req.source_label}）: {type(e).__name__}: {str(e)[:200]}")
            return None
        with self.lock:
            self.usage["input_tokens"] += resp.input_tokens
            self.usage["output_tokens"] += resp.output_tokens
            m = self.usage["by_model"].setdefault(resp.model or getattr(provider, "model", "?"),
                                                  {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            m["input_tokens"] += resp.input_tokens
            m["output_tokens"] += resp.output_tokens
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": resp.model, "input_tokens": resp.input_tokens,
                                    "output_tokens": resp.output_tokens, "data": resp.data},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        return resp.data

    def flush_batch(self) -> int:
        """集めた依頼を Batch API で送り（50% 引き）、終わるまで待って結果を保存する。送った件数を返す。"""
        if not self.pending:
            return 0
        pending, self.pending = self.pending, []
        limit = self.cfg.data.get("reading", {}).get("max_ai_calls")
        if limit:
            room = max(0, int(limit) - self.usage["calls"])
            if len(pending) > room:
                self.log(f"  上限（reading.max_ai_calls={limit}）のため {len(pending) - room} 件は送らない")
                pending = pending[:room]
        if not pending:
            return 0
        self.log(f"  Batch API で {len(pending)} 件を送信（結果が出るまで待つ）")
        # 送信済みの Batch の ID を保存しておき、待っている間に中断されても、次の実行で同じ依頼を二重に送らない
        import hashlib as _h
        key = _h.sha256("\n".join(str(x[0]) for x in pending).encode()).hexdigest()[:16]
        state_path = self.out / "batch_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        known = {int(b): i for b, i in state.get(key, {}).items()}

        def created(base, batch_id):
            state.setdefault(key, {})[str(base)] = batch_id
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
        results = self.provider.analyze_batch([req for _, req in pending], log=self.log, known=known,
                                              on_created=created)
        for (path, req), res in zip(pending, results):
            if isinstance(res, Exception):
                self.usage["failed"] += 1
                self.log(f"  AI の読み取りに失敗（{req.source_label}）: {type(res).__name__}: {str(res)[:200]}")
                continue
            self.usage["calls"] += 1
            self.usage["input_tokens"] += res.input_tokens
            self.usage["output_tokens"] += res.output_tokens
            m = self.usage["by_model"].setdefault(res.model or self.provider.model,
                                                  {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            m["input_tokens"] += res.input_tokens
            m["output_tokens"] += res.output_tokens
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"model": res.model, "input_tokens": res.input_tokens,
                                        "output_tokens": res.output_tokens, "batch": True,
                                        "cache_read_tokens": getattr(res, "cache_read_tokens", 0), "data": res.data},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
        return len(pending)

    def _parallel(self, jobs: list) -> None:
        workers = max(1, int(self.cfg.data.get("reading", {}).get("ai_workers", 4)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda f: f(), jobs))

    def ai_read(self, rec: dict) -> None:
        label = rec["path"]
        role = rec.get("role") or {}
        if rec["kind"] == "pdf":
            def page_job(p):
                def run():
                    p["ai"] = self._ai_cached(rec, f"page_{p['page']:03d}",
                                              lambda: ai_read.pdf_page_request(label, p))
                    p["ai_model"] = "main"
                    # 照合で要確認なら上位モデルで読み直す（同じ項目は保存済みを使い、呼び直さない）
                    if self.escalation is not None and p["ai"] is not None and \
                            verify.verify_pdf_page(p, p["ai"])["status"] == "needs_review":
                        up = self._ai_cached(rec, f"page_{p['page']:03d}_x", lambda: ai_read.pdf_page_request(label, p),
                                             provider=self.escalation)
                        if up is not None:
                            p["ai_main"], p["ai"], p["ai_model"] = p["ai"], up, "escalated"
                return run
            self._parallel([page_job(p) for p in rec["pages"]])
            return
        uniq = [sc for sc in rec["scenes"] if sc.get("same_as") is None]
        ref = next((sc["frame"] for sc in rec["scenes"] if sc.get("mode") == "experiment"), None)
        who = role.get("who", "")
        exp = [sc for sc in uniq if sc.get("mode") == "experiment"]
        size = int(role.get("group", ai_read.EXPERIMENT_GROUP))
        if size > 1:  # 区間をまとめる読み方（品質の確認ができるまで既定では使わない）
            uniq = [sc for sc in uniq if sc.get("mode") != "experiment"]
        groups = ai_read.experiment_groups(exp, size) if size > 1 else []

        def group_job(k, grp):
            def run():
                prev = ai_read.window_frames(groups[k - 1][-1])[-1] if k > 0 else None
                key = f"exp_{grp[0]['index']:04d}_{grp[-1]['index']:04d}"
                data = self._ai_cached(rec, key, lambda: ai_read.experiment_group_request(
                    label, grp, fmt_time, ref, prev, who))
                got = {int(w.get("window", 0)): w for w in (data or {}).get("windows", [])}
                for n, sc in enumerate(grp, 1):
                    w = got.get(n)
                    sc["ai"] = {k2: v for k2, v in w.items() if k2 != "window"} if w else None
                    sc["ai_model"] = "main"
                    sc["ai_group"] = key
            return run
        self._parallel([group_job(k, g) for k, g in enumerate(groups)])

        def detail_job(sc):
            def run():
                reqs = ai_read.video_detail_requests(label, sc, fmt_time)
                parts = [self._ai_cached(rec, f"scene_{sc['index']:04d}_d{k}", (lambda r=r: r))
                         for k, r in enumerate(reqs)]
                sc["ai"] = ai_read.merge_detail(parts) if all(x is not None for x in parts) or not self.batch else None
                sc["ai_model"] = "main"
            return run
        if role.get("kind") == "lecture_detail":
            self._parallel([detail_job(sc) for sc in uniq if sc.get("mode") != "experiment"])
            uniq = []

        def scene_job(sc):
            def run():
                if sc.get("mode") == "experiment":
                    build = lambda: ai_read.experiment_window_request(label, sc, fmt_time, ref, who)  # noqa: E731
                else:
                    build = lambda: ai_read.video_scene_request(label, sc, fmt_time)  # noqa: E731
                sc["ai"] = self._ai_cached(rec, f"scene_{sc['index']:04d}", build)
                sc["ai_model"] = "main"
                if self.escalation is None or sc["ai"] is None:
                    return
                if sc.get("mode") == "experiment":
                    hard = sc["ai"].get("face") == "unknown" and role.get("faces")
                else:
                    hard = verify.verify_video_scene(sc, sc["ai"])["status"] == "needs_review" or bool(sc["ai"].get("unreadable"))
                if hard:
                    up = self._ai_cached(rec, f"scene_{sc['index']:04d}_x", build, provider=self.escalation)
                    if up is not None:
                        sc["ai_main"], sc["ai"], sc["ai_model"] = sc["ai"], up, "escalated"
            return run
        self._parallel([scene_job(sc) for sc in uniq])
        for sc in rec["scenes"]:
            if sc.get("same_as") is not None:
                src = rec["scenes"][sc["same_as"]]
                sc["ai"], sc["ai_model"] = src.get("ai"), src.get("ai_model")
        self._fix_speech(rec)

    def _fix_speech(self, rec: dict) -> None:
        """文字起こしの専門用語の誤認識を、資料（スライドの書き起こし・OCR・PDF）の用語を根拠に訂正する。"""
        if not rec.get("speech"):
            return
        if self.batch and any(sc.get("ai") is None for sc in rec["scenes"] if sc.get("same_as") is None):
            return  # 用語の根拠に画面の書き起こしを使うので、画面を読み終えた次の周回で行う
        parts = [self.pdf_vocab]
        for sc in rec["scenes"]:
            if sc.get("same_as") is None:
                parts.append(verify.ai_text(sc["ai"]) if sc.get("ai") else "")
                parts.append(sc.get("ocr") or "")
        vocab_text = "\n".join(p for p in parts if p)
        terms = "、".join(dict.fromkeys(TERM.findall(vocab_text)))
        corrections, uncertain = [], []
        for start, segs in ai_read.speech_chunks(rec["speech"]):
            data = self._ai_cached(rec, f"speech_{start:05d}", lambda start=start, segs=segs: ai_read.speech_fix_request(
                rec["path"], start, segs, terms, fmt_time))
            if data:
                corrections += data.get("corrections", [])
                uncertain += data.get("uncertain", [])
        if corrections or uncertain:
            rec["speech_fix"] = verify.apply_speech_corrections(rec["speech"], corrections, vocab_text)
            rec["speech_fix"]["uncertain"] = uncertain
            for sc in rec["scenes"]:
                sc["speech"] = [x for x in rec["speech"] if sc["start"] <= (x["start"] + x["end"]) / 2 < sc["end"]]

    # ---------- 4. 照合 ----------
    def verify(self, records: dict[str, dict]) -> None:
        pdfs = [(r["path"], p) for r in records.values() if r["kind"] == "pdf" for p in r["pages"]]
        page_index = {(f, p["page"]): p for f, p in pdfs}
        for rec in records.values():
            if rec["kind"] == "pdf":
                rec["verification"] = [verify.verify_pdf_page(p, p.get("ai")) for p in rec["pages"]]
            else:
                verify.link_scenes_to_pdf(rec["scenes"], pdfs)
                for sc in rec["scenes"]:
                    if sc.get("same_as") is not None and rec["scenes"][sc["same_as"]].get("pdf_match"):
                        sc["pdf_match"] = rec["scenes"][sc["same_as"]]["pdf_match"]
                rec["verification"] = []
                for sc in rec["scenes"]:
                    m = sc.get("pdf_match")
                    pg = page_index.get((m["file"], m["page"])) if m else None
                    v = verify.verify_video_scene(sc, sc.get("ai"), pg)
                    if sc.get("same_as") is not None:
                        v["same_as"] = sc["same_as"]
                    rec["verification"].append(v)
                rec["ocr_vs_pdf"] = self._ocr_accuracy(rec, page_index)

    @staticmethod
    def _ocr_accuracy(rec: dict, page_index: dict) -> dict | None:
        """PDF と同じ内容の場面で、ローカルOCRがPDFの文字をどれだけ読めたか（OCR精度の実測）。"""
        cov = []
        for sc in rec["scenes"]:
            m = sc.get("pdf_match")
            if not m or sc.get("same_as") is not None:
                continue
            p = page_index.get((m["file"], m["page"]))
            c, _ = verify.line_coverage([ln["text"] for ln in p["lines"]], sc.get("ocr", ""), threshold=0.8)
            if c is not None:
                cov.append(c)
        return {"scenes": len(cov), "mean_line_coverage": round(sum(cov) / len(cov), 3)} if cov else None


def run_reading(cfg, *, only: list[str] | None = None, allow_ai: bool = False, provider=None,
                out_dir: Path | None = None, log=print, source=None, transcriber=None, escalation=None,
                counter=None) -> dict:
    from ..sources import make_source
    from .report import write_reports

    source = source or make_source(cfg)
    reader = Reader(cfg, out_dir=out_dir, provider=provider if allow_ai else None,
                    escalation=escalation if allow_ai else None, log=log)
    files = list(source.iter_files())
    items = reader.inventory(files)
    by_path = {f.path: f for f in files}
    targets = [i for i in items if i.role == "material" and i.kind in READ_KINDS
               and (not only or i.kind == "pdf" or any(o in i.path for o in only))]
    # PDF を先に読む（専門用語を文字起こしの手がかりにし、動画の画面と照合するため）
    targets.sort(key=lambda i: (i.kind != "pdf", i.size))
    records: dict[str, dict] = {}
    for it in targets:
        want = role_for(cfg, it.path).get("asr_model") or cfg.data["transcription"].get("model")
        if it.kind != "pdf" and (transcriber is None or getattr(transcriber, "_role_model", want) != want):
            transcriber = _make_transcriber(cfg, [r for r in records.values() if r["kind"] == "pdf"], log, model=want)
            if transcriber is not None:
                transcriber._role_model = want
        try:
            records[it.path] = reader.local_read(source, by_path[it.path], it.kind, transcriber)
        except Exception as e:  # noqa: BLE001
            log(f"[失敗] {it.path}: {type(e).__name__}: {e}")
            records[it.path] = {"path": it.path, "kind": it.kind, "error": f"{type(e).__name__}: {e}",
                                "scenes": [], "pages": [], "n_pages": 0}
    ok = {k: r for k, r in records.items() if not r.get("error")}
    reader.pdf_vocab = "\n".join(p["text_layer"] for r in ok.values() if r["kind"] == "pdf" for p in r["pages"])
    est = ai_read.estimate_calls(ok)
    ai_cfg = cfg.data["ai"]
    plan = ai_read.estimate_plan({p: r for p, r in ok.items() if not only or any(o in p for o in only)},
                                 price_in=float(ai_cfg.get("price_input_per_mtok", 4.0)),
                                 price_out=float(ai_cfg.get("price_output_per_mtok", 20.0)),
                                 batch=bool(cfg.data.get("reading", {}).get("batch")))
    reader.out.mkdir(parents=True, exist_ok=True)
    (reader.out / "estimate_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    if allow_ai and reader.provider is not None:
        chosen = [rec for path, rec in ok.items() if not only or any(o in path for o in only)]
        # --only のときは指定した資料だけ AI で読む（PDF は照合・用語のためにどのジョブでも読むが、AI は PDF のジョブだけ）
        for _round in range(4):  # Batch API: 依頼を集めて送り、結果を保存して読み直す（訂正などは前の結果を使うので数周）
            for rec in chosen:
                reader.ai_read(rec)
            if not reader.batch or not reader.flush_batch():
                break
    if counter is not None:  # 入力トークンの実測（無料）。結果は token_counts.json
        tc = count_tokens({p: r for p, r in ok.items() if not only or any(o in p for o in only)}, counter,
                          reader.pdf_vocab, log)
        (reader.out / "token_counts.json").write_text(json.dumps(tc, ensure_ascii=False, indent=1), encoding="utf-8")
    reader.verify(ok)
    summary = write_reports(cfg, reader.out, items, records, est, reader.usage, ai_enabled=reader.provider is not None)
    if not only:  # 全資料を読んだときは、資料をまたぐ整理（重複・差分、実験経過の時間軸、先生と生徒の対応）も行う
        from .corpus import analyze_corpus
        analyze_corpus(cfg, reader.out, items, ok, provider=escalation or provider if allow_ai else None, log=log)
    return summary


def all_requests(rec: dict, vocab: str = "") -> list:
    """資料1件で AI に送る依頼をすべて組み立てる（送らない）。入力トークンの実測（count_tokens）に使う。"""
    label = rec["path"]
    role = rec.get("role") or {}
    if rec["kind"] == "pdf":
        return [ai_read.pdf_page_request(label, p) for p in rec["pages"]]
    uniq = [sc for sc in rec["scenes"] if sc.get("same_as") is None]
    exp = [sc for sc in uniq if sc.get("mode") == "experiment"]
    ref = exp[0]["frame"] if exp else None
    size = int(role.get("group", ai_read.EXPERIMENT_GROUP))
    if size > 1:
        groups = ai_read.experiment_groups(exp, size)
        reqs = [ai_read.experiment_group_request(label, g, fmt_time, ref,
                                                 ai_read.window_frames(groups[k - 1][-1])[-1] if k else None,
                                                 role.get("who", "")) for k, g in enumerate(groups)]
    else:
        reqs = [ai_read.experiment_window_request(label, sc, fmt_time, ref, role.get("who", "")) for sc in exp]
    if role.get("kind") == "lecture_detail":
        for sc in uniq:
            if sc.get("mode") != "experiment":
                reqs += ai_read.video_detail_requests(label, sc, fmt_time)
    else:
        reqs += [ai_read.video_scene_request(label, sc, fmt_time) for sc in uniq if sc.get("mode") != "experiment"]
    reqs += [ai_read.speech_fix_request(label, i, segs, vocab, fmt_time)
             for i, segs in ai_read.speech_chunks(rec.get("speech", []))]
    return reqs


def count_tokens(records: dict[str, dict], counter, vocab: str = "", log=print) -> dict:
    """依頼ごとの入力トークンを API の count_tokens で数える（推論しないので課金なし）。"""
    import time as _t
    out = {}
    for path, rec in records.items():
        kinds: dict = {}
        for req in all_requests(rec, vocab):
            for attempt in range(5):
                try:
                    n = counter.count_input_tokens(req)
                    break
                except Exception as e:  # noqa: BLE001  レート制限などは待ってやり直す
                    if attempt == 4:
                        log(f"  入力トークンを数えられない（{req.source_label}）: {type(e).__name__}: {str(e)[:150]}")
                        n = None
                    _t.sleep(2 ** attempt)
            k = kinds.setdefault(req.task, {"requests": 0, "input_tokens": 0, "uncounted": 0})
            k["requests"] += 1
            if n is None:
                k["uncounted"] += 1
            else:
                k["input_tokens"] += n
        out[path] = kinds
        log(f"  入力トークン（実測）: {path}: " + "、".join(f"{t} {v['requests']} 件 {v['input_tokens']:,}" for t, v in kinds.items()))
    return out


def list_targets(cfg, source, with_hash: bool = False) -> list:
    """並列実行の単位: 資料1件ずつ（PDF はどのジョブでもローカルに読むが、AI で読むのはその PDF のジョブだけ）。
    with_hash=True なら [{"path", "key"}]（key は内容のハッシュと読み取り方法の版。保存済みの結果を使い直すため）。"""
    items = build_inventory(list(source.iter_files()), list(cfg.data["stability"]["ignore_patterns"]))
    mats = [i for i in items if i.role == "material" and i.kind in READ_KINDS]
    chosen = sorted(mats, key=lambda i: (i.kind == "pdf", -i.size))
    if not with_hash:
        return [i.path for i in chosen]
    return [{"path": i.path, "key": f"v{READ_VERSION}-{(i.content_hash or '')[:16]}"} for i in chosen]


def _make_transcriber(cfg, pdf_records: list[dict], log, model: str | None = None):
    """model: 資料の役割で指定した文字起こしのモデル（Excel・レポートの作り方は精度の高い large-v3）。"""
    import copy
    from .. import media
    t = cfg.data["transcription"]
    if model and model != t.get("model"):
        cfg = copy.copy(cfg)
        cfg.data = {**cfg.data, "transcription": {**t, "model": model}}
    try:
        tr = media.make_transcriber(cfg)
    except media.MediaError as e:
        log(f"文字起こしを使えません: {e}")
        return None
    if isinstance(tr, media.FasterWhisperTranscriber):
        tr.initial_prompt = vocabulary(pdf_records, t.get("initial_prompt") or "")[:400] or None
    return tr


def clean_cache(cfg) -> None:
    shutil.rmtree(cfg.cache_dir, ignore_errors=True)
