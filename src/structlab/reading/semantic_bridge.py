"""読み取り工程の結果（reading.json）を意味理解層（semantic）に渡す橋渡し。

読み取り工程で AI が画面・ページを読んだ結果（書き起こし）と文字起こしを、意味理解層の「場面」として使う。
画像は読み取り工程で読み済みなので、第1回（場面の理解）・第2回（指示の抽出）では送らない（同じ画像を二重に送らない）。
第3回（元資料との照合）では、指示の根拠の時刻の画面（保存済みのフレーム・ページ画像）を送って確かめる。

場面の長さは従来の意味理解と同じ（動画は video.segment_seconds、既定 120 秒。PDF は1ページ）。
先生の実験経過動画は除外した末尾（valid_end 以降）を含めない。

  python -m structlab.reading.semantic_bridge <読み取り結果のフォルダ>... --out <出力先> -c 設定 [--allow-external-ai]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from ..db import DB
from ..models import fmt_time


#: 読み取り結果を意味理解層に渡す形: json（既定）/ compact（候補: 同じキー・値をすべて含む短い書き方。
#: 品質の比較で同等と確かめるまで既定では使わない）
MATERIAL_FORMAT = "json"


def compact(x, indent: str = "") -> str:
    """JSON の値を、キー・値・順序をすべて保ったまま、括弧・引用符を省いた行の形にする（情報は減らさない）。"""
    if isinstance(x, dict):
        lines = []
        for k, v in x.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{indent}{k}:")
                lines.append(compact(v, indent + "  "))
            else:
                lines.append(f"{indent}{k}: {_scalar(v)}")
        return "\n".join(lines)
    if isinstance(x, list):
        out = []
        for v in x:
            if isinstance(v, (dict, list)) and v:
                body = compact(v, indent + "  ")
                out.append(f"{indent}- " + body.lstrip())
            else:
                out.append(f"{indent}- {_scalar(v)}")
        return "\n".join(out)
    return f"{indent}{_scalar(x)}"


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return "{}" if isinstance(v, dict) else "[]"
    return str(v).replace("\n", "\\n")


def _ai_json(ai: dict | None) -> str:
    if not ai:
        return ""
    return compact(ai) if MATERIAL_FORMAT == "compact" else json.dumps(ai, ensure_ascii=False)


def _screen_text(sc: dict) -> str:
    ai = sc.get("ai")
    if ai:
        bits = [ai.get("title", "")] + [b.get("text", "") for b in ai.get("text_blocks", [])]
        bits += [ai.get("specimen_state", "")] if "specimen_state" in ai else []
        txt = " / ".join(b for b in bits if b)
        if txt:
            return txt[:2000]
    return (sc.get("ocr") or "")[:2000]


def reading_fingerprint(rec: dict) -> str:
    """読み取り結果のうち意味理解に渡す部分の指紋。"""
    import hashlib
    keep = {"hash": rec.get("content_hash"), "valid_end": rec.get("valid_end"), "speech": rec.get("speech"),
            "scenes": [{k: sc.get(k) for k in ("start", "end", "ai", "ocr", "same_as", "frame_time")} for sc in rec.get("scenes", [])],
            "pages": [{k: p.get(k) for k in ("page", "text_layer", "ai")} for p in rec.get("pages", [])]}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


def load_records(db: DB, records: dict[str, dict], segment: float = 120.0) -> list[int]:
    """reading.json を files / units / timeline に入れる。戻り値: file_id の一覧。"""
    ids = []
    now = time.time()
    for path, rec in sorted(records.items()):
        if rec.get("error"):
            continue
        kind = rec["kind"]
        # 意味理解の「版」は読み取り結果の指紋（読み取りが変われば新しく理解し直し、同じなら途中から続ける・使い直す）
        rec = {**rec, "content_hash": reading_fingerprint(rec)}
        db.x("INSERT INTO files(path,path_key,kind,ext,size,content_hash,first_seen,last_seen,status,updated_at) "
             "VALUES(?,?,?,?,?,?,?,?,'done',?) ON CONFLICT(path_key) DO UPDATE SET content_hash=excluded.content_hash, "
             "status='done', updated_at=excluded.updated_at",
             (path, path.lower(), kind, Path(path).suffix.lower(), rec.get("size"), rec.get("content_hash") or "",
              now, now, now))
        f = db.q1("SELECT * FROM files WHERE path_key=?", (path.lower(),))
        fid, fh = f["id"], f["content_hash"] or ""
        ids.append(fid)
        if db.q1("SELECT 1 FROM units WHERE file_id=? AND file_hash=?", (fid, fh)):
            continue  # 読み込み済み（同じ内容）
        with db.tx():
            if kind == "pdf":
                for p in rec["pages"]:
                    ex = {"text": p.get("text_layer", ""), "ai_text": _ai_json(p.get("ai")) or "\n".join(p.get("rows", [])),
                          "images": [], "verify_images": [[p["render"], f"p.{p['page']}"]] if p.get("render") else [],
                          "page": p["page"], "needs_ai": [] if p.get("ai") else (["vision"] if p.get("needs_vision") else [])}
                    db.x("INSERT INTO units(file_id,file_hash,unit_type,locator,seq,status,ai_status,extract_json,updated_at) "
                         "VALUES(?,?,?,?,?,'done','not_requested',?,?)",
                         (fid, fh, "pdf_page", json.dumps({"page": p["page"]}), p["page"], json.dumps(ex, ensure_ascii=False), now))
                continue
            end = float(rec.get("valid_end") or rec["duration"])
            t, seq = 0.0, 0
            while t < end - 0.01:
                t1 = min(end, t + segment)
                seq += 1
                scs = [sc for sc in rec["scenes"] if sc["start"] < t1 and sc["end"] > t and sc["start"] < end]
                mat = []
                for sc in scs:
                    src = rec["scenes"][sc["same_as"]] if sc.get("same_as") is not None else sc
                    head = f"### 画面 {fmt_time(sc['start'])}–{fmt_time(min(sc['end'], end))}（{sc.get('mode', '')}）"
                    if src.get("ai"):
                        mat.append(f"{head} 読み取り工程の AI の書き起こし\n{_ai_json(src['ai'])}")
                    else:
                        mat.append(f"{head} AI 未読（OCR、誤りを含む）\n{(src.get('ocr') or '（なし）')[:3000]}")
                ex = {"data": {"transcriber": rec.get("transcriber") or "none", "n_keyframes": len(scs)},
                      "ai_text": "\n\n".join(mat), "images": []}
                loc = {"start": round(t, 3), "end": round(t1, 3), "has_audio": bool(rec.get("has_audio")), "has_video": True}
                cur = db.x("INSERT INTO units(file_id,file_hash,unit_type,locator,seq,status,ai_status,extract_json,updated_at) "
                           "VALUES(?,?,?,?,?,'done','not_requested',?,?)",
                           (fid, fh, "video_segment", json.dumps(loc), seq, json.dumps(ex, ensure_ascii=False), now))
                uid = cur.lastrowid
                frames = sorted([(sc.get("frame_time", sc["start"]), sc.get("frame"), sc) for sc in scs if sc.get("frame")],
                                key=lambda x: x[0])
                for ft, fp, sc in frames:
                    db.x("INSERT INTO timeline(unit_id,file_id,t_start,t_end,speech,frame_path,frame_time,screen_text) "
                         "VALUES(?,?,?,?,NULL,?,?,?)", (uid, fid, ft, ft, fp, ft, _screen_text(sc)))
                for s in rec.get("speech", []):
                    if t <= (s["start"] + s["end"]) / 2 < t1:
                        near = min(frames, key=lambda x: abs(x[0] - s["start"]))[1] if frames else None
                        db.x("INSERT INTO timeline(unit_id,file_id,t_start,t_end,speech,frame_path,frame_time,screen_text) "
                             "VALUES(?,?,?,?,?,?,NULL,NULL)", (uid, fid, s["start"], s["end"], s["text"], near))
                t = t1
    return ids


class BatchGate:
    """複数のスレッド（資料ごとの意味理解）からの AI 呼び出しを集め、全員が待ちになったら Batch API でまとめて送る。
    各資料の処理の順序（前の場面の理解を次の場面に渡すなど）はそのまま。"""

    def __init__(self, provider, log=print, state_path: Path | None = None):
        self.provider = provider
        self.log = log
        #: 送信した Batch の ID を依頼の指紋とともに保存する（中断後の再実行で、同じ依頼の集まりなら送り直さずに結果を取りに行く）
        self.state_path = state_path
        self.lock = threading.Lock()
        self.active = 0
        self.parties = 0  # 待っているスレッドの数（1 スレッドが複数の依頼をまとめて待つことがある）
        self.waiting: list[tuple] = []

    def enter(self):
        with self.lock:
            self.active += 1

    def leave(self):
        with self.lock:
            self.active -= 1
            batch = self._take()
        self._send(batch)

    def call(self, req):
        r = self.call_many([req])[0]
        if isinstance(r, Exception):
            raise r
        return r

    def call_many(self, reqs: list) -> list:
        """1つのスレッドから、互いに依存しない複数の依頼をまとめて待つ（スレッドとしては 1 人分の待ち）。
        戻り値は各依頼の応答か例外。"""
        evs = []
        with self.lock:
            self.parties += 1
            for req in reqs:
                ev, slot = threading.Event(), {}
                self.waiting.append((req, ev, slot))
                evs.append((ev, slot))
            batch = self._take()
        self._send(batch)
        for ev, _ in evs:
            ev.wait()
        return [slot.get("error", slot.get("resp")) for _, slot in evs]

    def _take(self):
        if self.waiting and self.parties >= self.active:
            batch, self.waiting, self.parties = self.waiting, [], 0
            return batch
        return None

    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, st: dict) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

    def _send(self, batch):
        if not batch:
            return
        import hashlib
        from .runner import request_hash
        hashes = [request_hash(b[0]) for b in batch]
        key = hashlib.sha256("\n".join(sorted(hashes)).encode()).hexdigest()[:24]
        prev = self._state().get(key)
        if prev and sorted(prev.get("order", [])) == sorted(hashes):
            # 前回送った同じ依頼の集まり: 前回と同じ順序に並べ直して、送信済みの Batch の結果を取りに行く
            pos = {h: i for i, h in enumerate(prev["order"])}
            batch = sorted(batch, key=lambda b: pos[request_hash(b[0])])
            hashes = prev["order"]
            known = {int(k): v for k, v in prev.get("batches", {}).items()}
        else:
            known = {}

        def created(base, bid):
            st = self._state()
            st.setdefault(key, {"order": hashes, "batches": {}})["batches"][str(base)] = bid
            self._save_state(st)
        try:
            res = self.provider.analyze_batch([b[0] for b in batch], log=self.log, known=known or None, on_created=created)
            st = self._state()
            st.pop(key, None)
            self._save_state(st)
        except Exception as e:  # noqa: BLE001
            res = [e] * len(batch)
        for (req, ev, slot), r in zip(batch, res):
            if isinstance(r, Exception):
                slot["error"] = r
            else:
                slot["resp"] = r
            ev.set()


def run_semantic(cfg, out: Path, records: dict[str, dict], provider=None, *, batch: bool = True, log=print) -> dict:
    """意味理解（規則＋AI の3段階）と資料横断の統合を実行し、out/semantic/ に報告を書く。"""
    from ..ai import AIError
    from ..ai.base import AwaitingAnswer
    from ..semantic.corpus import integrate
    from ..semantic.engine import SemanticEngine
    from ..semantic.report import write_semantic_reports
    out.mkdir(parents=True, exist_ok=True)
    pcfg = SimpleNamespace(data=cfg.data, work_dir=out, results_dir=out, cache_dir=out / "cache")
    dbp = out / "semantic.db"
    db = DB(dbp)
    seg = float(cfg.data.get("video", {}).get("segment_seconds", 120))
    global MATERIAL_FORMAT
    MATERIAL_FORMAT = cfg.data.get("reading", {}).get("semantic_material", "json")
    fids = load_records(db, records, seg)
    if provider is None:  # AI を使わない実行では、意味理解の AI の見積もりを書く（Batch なら料金は半分）
        from ..semantic.estimate import estimate
        try:
            est = estimate(db, pcfg)
            est["batch_factor"] = 0.5 if cfg.data.get("reading", {}).get("batch") else 1.0
            (out / "semantic_estimate.json").write_text(json.dumps(est, ensure_ascii=False, indent=1, default=str),
                                                        encoding="utf-8")
        except Exception as e:  # noqa: BLE001  見積もりの失敗で処理を止めない
            log(f"意味理解の見積もりに失敗: {type(e).__name__}: {e}")
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0}
    ulock = threading.Lock()
    gate = BatchGate(provider, log, out / "semantic_batch_state.json") \
        if (provider is not None and batch and hasattr(provider, "analyze_batch")) else None

    def make_call(tdb):
        def call(req, uid):
            try:
                resp = gate.call(req) if gate else provider.analyze(req)
            except AwaitingAnswer:
                raise
            except AIError:
                with ulock:
                    usage["failed"] += 1
                raise
            except Exception as e:  # noqa: BLE001
                with ulock:
                    usage["failed"] += 1
                raise AIError(f"{type(e).__name__}: {e}", retryable=True) from e
            record(tdb, req, uid, resp)
            return resp
        return call

    def record(tdb, req, uid, resp):
        with ulock:
            usage["calls"] += 1
            usage["input_tokens"] += resp.input_tokens
            usage["output_tokens"] += resp.output_tokens
        tdb.x("INSERT INTO api_usage(run_id,unit_id,provider,model,input_tokens,output_tokens,images,ok,created_at)"
              " VALUES(NULL,?,?,?,?,?,?,1,?)", (uid, getattr(provider, "name", "ai"), resp.model, resp.input_tokens,
                                               resp.output_tokens, len(req.images), time.time()))

    def make_call_many(tdb):
        """互いに依存しない依頼をまとめて送る（Batch の 1 回に入る）。"""
        if gate is None:
            return None

        def call_many(items):
            res = gate.call_many([req for req, _ in items])
            out = []
            for (req, uid), r in zip(items, res):
                if isinstance(r, AwaitingAnswer):
                    out.append(r)
                elif isinstance(r, Exception):
                    with ulock:
                        usage["failed"] += 1
                    out.append(r if isinstance(r, AIError) else AIError(f"{type(r).__name__}: {r}", retryable=True))
                else:
                    record(tdb, req, uid, r)
                    out.append(r)
            return out
        return call_many

    def work(fid):
        tdb = DB(dbp)
        try:
            eng = SemanticEngine(pcfg, tdb, ai_call=make_call(tdb) if provider is not None else None,
                                 ai_call_many=make_call_many(tdb) if provider is not None else None,
                                 ai_name=getattr(provider, "name", "") if provider else "")
            frow = tdb.q1("SELECT * FROM files WHERE id=?", (fid,))
            log(f"意味理解: {frow['path']}")
            eng.process_file(frow, None)
        finally:
            tdb.close()
            if gate:
                gate.leave()

    threads = []
    for fid in fids:
        if gate:
            gate.enter()
        th = threading.Thread(target=work, args=(fid,), daemon=True)
        threads.append(th)
        th.start()
    for th in threads:
        th.join()
    eng = SemanticEngine(pcfg, db, ai_call=make_call(db) if provider is not None else None,
                         ai_name=getattr(provider, "name", "") if provider else "")
    if gate:
        gate.enter()
    try:
        integrate(eng)
    finally:
        if gate:
            gate.leave()
    paths = write_semantic_reports(db, pcfg)
    db.close()
    (out / "semantic_usage.json").write_text(json.dumps(usage, indent=1), encoding="utf-8")
    return {"usage": usage, "reports": {k: str(v) for k, v in paths.items()}}


def main(argv: list[str] | None = None) -> int:
    from ..config import load_config
    ap = argparse.ArgumentParser(prog="structlab.reading.semantic_bridge")
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("-c", "--config", default="configs/dropbox.yaml")
    ap.add_argument("--allow-external-ai", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    records: dict[str, dict] = {}
    for d in a.dirs:
        for rp in sorted(Path(d).rglob("reading.json")):
            r = json.loads(rp.read_text(encoding="utf-8"))
            if r.get("path") and not r.get("error"):
                records.setdefault(r["path"], r)
    provider = None
    if a.allow_external_ai:
        from ..ai import make_provider
        provider, why = make_provider(cfg, allow_external_flag=True)
        if provider is None:
            print(f"AI を使えません: {why}")
            return 1
    res = run_semantic(cfg, Path(a.out), records, provider,
                       batch=bool(cfg.data.get("reading", {}).get("batch")), log=lambda m: print(m, flush=True))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
