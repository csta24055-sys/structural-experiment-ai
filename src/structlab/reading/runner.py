"""資料読み取り基盤の実行（`structlab read`）。元資料は読むだけで、結果は作業フォルダ（既定 work/reading）にだけ書く。

1. 一覧 → 棚卸し（重複・補助ファイルの判別）
2. 代表ファイルごとにローカルの読み取り（無料）: PDF は位置付きテキスト層、動画は場面分割・代表画面・OCR・文字起こし
3. （許可がある場合だけ）AI による画像の書き起こし。結果はファイルに保存し、同じ内容なら再送しない
4. 照合（無料）: 書き起こしをテキスト層・OCR・対応する PDF のページと突き合わせる
5. 報告: inventory.md、files/<資料>/reading.md、summary.md（精度の指標・要確認・未照合）、estimate.md
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections import Counter
from pathlib import Path, PurePosixPath

from ..models import RemoteFile, fmt_time
from . import ai_read, verify
from .inventory import as_dicts, build_inventory, inventory_markdown

READ_KINDS = {"pdf", "video", "audio"}
TERM = re.compile(r"[一-龥ァ-ヶー]{2,}")


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-.ぁ-んァ-ヶ一-龥]+", "_", name).strip("_")[:80]


def _key(rf: RemoteFile) -> str:
    return (rf.content_hash or hashlib.sha256(rf.path.encode()).hexdigest())[:16]


def vocabulary(pdf_records: list[dict], base: str = "", limit: int = 60) -> str:
    """PDF の文字情報から専門用語の候補を集め、文字起こしの手がかりにする。"""
    c: Counter = Counter()
    for rec in pdf_records:
        for p in rec["pages"]:
            c.update(TERM.findall(p["text_layer"]))
    terms = [t for t, _ in c.most_common(limit * 2) if len(t) <= 12][:limit]
    return (base.rstrip("。") + "。" if base else "") + "、".join(terms)


class Reader:
    def __init__(self, cfg, *, out_dir: Path | None = None, provider=None, log=print):
        self.cfg = cfg
        self.out = Path(out_dir or cfg.work_dir / "reading")
        self.provider = provider
        self.log = log
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0}
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
            if rec.get("content_hash") == rf.content_hash and rec.get("complete"):
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
                rec = read_video(local, d / "frames", ocr=ocr, transcriber=transcriber,
                                 interval=float(rv.get("scene_interval_seconds", 1.0)),
                                 max_events_per_scene=int(rv.get("max_events_per_scene", 4)), log=self.log)
        finally:
            if not getattr(source, "is_local_passthrough", lambda: False)():
                Path(local).unlink(missing_ok=True)  # 動画は大きいので読み終えたら消す（画面は保存済み）
        rec.update({"path": rf.path, "content_hash": rf.content_hash, "size": rf.size, "dir": str(d),
                    "local_seconds": round(time.time() - t0, 1), "complete": True})
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
    def _ai_cached(self, rec: dict, item_key: str, build) -> dict | None:
        path = Path(rec["dir"]) / "ai" / f"{item_key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["data"]
        if self.provider is None:
            return None
        limit = self.cfg.data.get("reading", {}).get("max_ai_calls")
        if limit and self.usage["calls"] >= int(limit):
            return None
        req = build()
        try:
            resp = self.provider.analyze(req)
        except Exception as e:  # noqa: BLE001  1件の失敗で全体を止めない（未照合として残る）
            self.usage["failed"] += 1
            self.log(f"  AI の読み取りに失敗（{req.source_label}）: {type(e).__name__}: {str(e)[:200]}")
            return None
        self.usage["calls"] += 1
        self.usage["input_tokens"] += resp.input_tokens
        self.usage["output_tokens"] += resp.output_tokens
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": resp.model, "input_tokens": resp.input_tokens,
                                    "output_tokens": resp.output_tokens, "data": resp.data},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        return resp.data

    def ai_read(self, rec: dict) -> None:
        label = rec["path"]
        if rec["kind"] == "pdf":
            for p in rec["pages"]:
                p["ai"] = self._ai_cached(rec, f"page_{p['page']:03d}", lambda p=p: ai_read.pdf_page_request(label, p))
        else:
            for sc in rec["scenes"]:
                if sc.get("same_as") is not None:
                    continue
                sc["ai"] = self._ai_cached(rec, f"scene_{sc['index']:04d}",
                                           lambda sc=sc: ai_read.video_scene_request(label, sc, fmt_time))
            for sc in rec["scenes"]:
                if sc.get("same_as") is not None:
                    sc["ai"] = rec["scenes"][sc["same_as"]].get("ai")
            self._fix_speech(rec)

    def _fix_speech(self, rec: dict) -> None:
        """文字起こしの専門用語の誤認識を、資料（スライドの書き起こし・OCR・PDF）の用語を根拠に訂正する。"""
        if not rec.get("speech"):
            return
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
                out_dir: Path | None = None, log=print, source=None, transcriber=None) -> dict:
    from ..sources import make_source
    from .report import write_reports

    source = source or make_source(cfg)
    reader = Reader(cfg, out_dir=out_dir, provider=provider if allow_ai else None, log=log)
    files = list(source.iter_files())
    items = reader.inventory(files)
    by_path = {f.path: f for f in files}
    targets = [i for i in items if i.role == "material" and i.kind in READ_KINDS
               and (not only or i.kind == "pdf" or any(o in i.path for o in only))]
    # PDF を先に読む（専門用語を文字起こしの手がかりにし、動画の画面と照合するため）
    targets.sort(key=lambda i: (i.kind != "pdf", i.size))
    records: dict[str, dict] = {}
    for it in targets:
        if it.kind != "pdf" and transcriber is None:
            transcriber = _make_transcriber(cfg, [r for r in records.values() if r["kind"] == "pdf"], log)
        try:
            records[it.path] = reader.local_read(source, by_path[it.path], it.kind, transcriber)
        except Exception as e:  # noqa: BLE001
            log(f"[失敗] {it.path}: {type(e).__name__}: {e}")
            records[it.path] = {"path": it.path, "kind": it.kind, "error": f"{type(e).__name__}: {e}",
                                "scenes": [], "pages": [], "n_pages": 0}
    ok = {k: r for k, r in records.items() if not r.get("error")}
    reader.pdf_vocab = "\n".join(p["text_layer"] for r in ok.values() if r["kind"] == "pdf" for p in r["pages"])
    est = ai_read.estimate_calls(ok)
    if allow_ai and reader.provider is not None:
        for rec in ok.values():
            reader.ai_read(rec)
    reader.verify(ok)
    summary = write_reports(cfg, reader.out, items, records, est, reader.usage, ai_enabled=reader.provider is not None)
    return summary


def list_targets(cfg, source) -> list[str]:
    """並列実行の単位: 動画・音声は1件ずつ。PDF はどのジョブでも読むので、動画が無いときだけ1ジョブにする。"""
    items = build_inventory(list(source.iter_files()), list(cfg.data["stability"]["ignore_patterns"]))
    mats = [i for i in items if i.role == "material" and i.kind in READ_KINDS]
    vids = [i.path for i in sorted(mats, key=lambda i: -i.size) if i.kind != "pdf"]
    return vids or ([mats[0].path] if mats else [])


def _make_transcriber(cfg, pdf_records: list[dict], log):
    from .. import media
    t = cfg.data["transcription"]
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
