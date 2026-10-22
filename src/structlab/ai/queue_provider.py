"""候補 G5: AI の解析を、従量課金の API ではなく Claude Code（利用者の定額の契約）で行うための受け渡し口。

依頼（指示文・本文・画像・出力の形）は API に送るものと同じものをファイルに書き出す。画像は API が受け取るのと同じもの
（同じ縮小・同じ形式で符号化して復号した画素）を PNG（可逆）で書き出す。Claude Code の答え役のエージェント
（.claude/agents/g5-answerer.md、新しい文脈・Read と Write だけ）が 1 件ずつ読み、受け取り箱（inbox）に答えを書く。
取り込み（ingest）で出力の形（JSON スキーマ）を検査し、合格した答えだけを answers に移す。

  queue/requests/<id>.json   … 依頼（id は依頼の内容の指紋。同じ依頼は同じ id）
  queue/images/<id>/NN.png   … API が受け取るのと同じ画像
  queue/inbox/<id>.json      … 答え役が書く答え（まだ検査していない）
  queue/answers/<id>.json    … 検査に合格した答え（依頼の指紋つき）
  queue/rejected/<id>.<n>.json … 形に合わない・壊れた答え（理由つき。依頼は未回答のまま残る）
  queue/log.jsonl            … 取り込み・重複・破損の記録

答えの無い依頼は AwaitingAnswer（失敗ではない）として、状態を変えずにその実行を止める。同じコマンドを繰り返すと答えた分だけ進む。
使い方は docs/CLAUDE_CODE_QUEUE.md。API と同じ品質かは、見本で API の結果と比べるまで未検証（既定では使わない）。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .base import AIProvider, AIRequest, AIResponse, AwaitingAnswer

#: 依頼の記録の形式の版（変えたら上げる。古い版の答えは使わない）
RECORD_VERSION = 5
#: Claude Code の Read は長辺 2000 画素を超える画像を縮小して見せる（2026-10-22 に合成画像で確認: 2576×1449 → 2000×1125）。
#: API は 2576 まで受け取るので、これを超える画像は元の画素のまま 2000 以下の重なりのある部分に分けて添える
READ_MAX_EDGE = 2000
TILE_OVERLAP = 64


def request_id(req: AIRequest) -> str:
    from ..reading.runner import request_hash
    return request_hash(req)


def api_view(path: Path, dest: Path, max_edge: int, lossless: bool) -> dict:
    """API に送るのと同じ処理（anthropic_provider.encode_image）で符号化し、復号した画素を PNG で保存する。"""
    import base64
    import io
    from PIL import Image
    from .anthropic_provider import encode_image
    media, data = encode_image(path, max_edge, lossless)
    with Image.open(io.BytesIO(base64.standard_b64decode(data))) as im:
        im = im.convert("RGB")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.stem + ".tmp.png")
        im.save(tmp, format="PNG")
        tmp.replace(dest)  # 途中で止まっても壊れた画像を残さない
        w, h = im.size
    return {"width": w, "height": h, "api_media_type": media}


def _readable(path: Path) -> bool:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.load()
        return True
    except Exception:  # noqa: BLE001
        return False


def tile_boxes(w: int, h: int, max_edge: int = READ_MAX_EDGE, overlap: int = TILE_OVERLAP) -> list[tuple[int, int, int, int]]:
    """w×h の画像を、どの部分も長辺 max_edge 以下になるように重なり overlap で分ける（分ける必要がなければ空）。"""
    if max(w, h) <= max_edge:
        return []
    import math

    def spans(n):
        k = 1 if n <= max_edge else max(2, math.ceil((n - overlap) / (max_edge - overlap)))
        step = (n - max_edge) / (k - 1) if k > 1 else 0
        return [(round(i * step), round(i * step) + min(max_edge, n)) for i in range(k)]
    return [(x0, y0, x1, y1) for y0, y1 in spans(h) for x0, x1 in spans(w)]


def write_tiles(png: Path, out_dir: Path) -> list[str]:
    from PIL import Image
    with Image.open(png) as im:
        boxes = tile_boxes(*im.size)
        paths = []
        for k, b in enumerate(boxes, 1):
            dest = out_dir / f"{png.stem}_t{k}.png"
            if not dest.exists() or not _readable(dest):
                tmp = dest.with_name(dest.stem + ".tmp.png")
                im.crop(b).save(tmp, format="PNG")
                tmp.replace(dest)
            paths.append(str(dest.resolve()))
    return paths


def request_record(req: AIRequest, images_dir: Path | None = None, max_edge: int = 2576) -> dict:
    from .prompts import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_user_text
    rid = request_id(req)
    text = req.text if req.schema is not None else build_user_text(req.task, req.source_label, req.text)
    imgs = []
    for n, i in enumerate(req.images, 1):
        row = {"label": i.label, "source": str(Path(i.path).resolve()), "lossless": bool(getattr(i, "lossless", False))}
        if images_dir is not None:
            dest = images_dir / rid / f"{n:02d}.png"
            if not dest.exists() or not _readable(dest):
                row.update(api_view(Path(i.path), dest, max_edge, row["lossless"]))
            else:
                from PIL import Image
                with Image.open(dest) as im:
                    row.update(width=im.size[0], height=im.size[1])
            row["path"] = str(dest.resolve())
            # 長辺が READ_MAX_EDGE を超える画像は、元の画素のままの部分画像も添える（Read の縮小で細部を失わないため）
            row["tiles"] = write_tiles(dest, dest.parent)
        else:
            row["path"] = row["source"]
        imgs.append(row)
    rec = {"version": RECORD_VERSION, "id": rid, "task": req.task, "source_label": req.source_label,
           "system": req.system or SYSTEM_PROMPT, "text": text, "images": imgs,
           "schema": req.schema or OUTPUT_SCHEMA, "max_tokens": req.max_tokens,
           # API の依頼をそのまま作り直せるように（見本の比較 P-G5 で同じ依頼を API に送る）
           "api": {"text": req.text, "system": req.system, "schema": req.schema, "cache_prefix_chars": req.cache_prefix_chars,
                   "prefix_before_images": getattr(req, "prefix_before_images", False),
                   "cache_ttl": getattr(req, "cache_ttl", ""), "t_start": req.t_start, "t_end": req.t_end,
                   "images": [{"path": str(Path(i.path).resolve()), "label": i.label, "cache": bool(getattr(i, "cache", False)),
                               "lossless": bool(getattr(i, "lossless", False))} for i in req.images]}}
    rec["record_sha256"] = record_digest(rec)
    return rec


def _portable(p: str) -> str:
    """作業フォルダの場所に依らない画像の名前（queue/… または files/… 以降）。画像の中身は依頼の番号（id）に含まれる。"""
    for mark in ("/queue/", "/files/"):
        if mark in p:
            return mark.strip("/") + "/" + p.split(mark, 1)[1]
    return Path(p).name


def record_digest(rec: dict, legacy: bool = False) -> str:
    """依頼の指紋。M28 から作業フォルダの場所を含めない（別の場所に復元した答えも使える）。legacy は M27 までの形（絶対パス）。"""
    keep = {k: rec[k] for k in ("version", "id", "task", "source_label", "system", "text", "schema")}
    keep["images"] = [{k: i.get(k) for k in ("label", "path", "width", "height", "tiles")} for i in rec["images"]]
    if not legacy:
        for i in keep["images"]:
            i["path"] = _portable(i["path"]) if i.get("path") else i.get("path")
            i["tiles"] = [_portable(t) for t in i["tiles"]] if i.get("tiles") else i.get("tiles")
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def digest_ok(rec: dict, d: str | None) -> bool:
    """d がこの依頼の指紋か（M28 の形・M27 までの形のどちらでもよい。どちらも中身から計算し直して比べる）。"""
    return d is not None and d in (record_digest(rec), record_digest(rec, legacy=True))


END_MARK = "<<依頼の終わり>>"


def render_markdown(rec: dict) -> str:
    """答え役が Read で読む依頼（長い 1 行が切れないように、出力の形も字下げして行に分ける）。
    先頭に全体の行数、最後に終わりの印を置き、読み落としに気付けるようにする。"""
    body = ["## 指示（system）", rec["system"], "", "## 画像（この順にすべて Read で開く。tiles は同じ画像の元の画素の部分）"]
    for n, i in enumerate(rec["images"], 1):
        body.append(f"{n}. {i['label']}: {i['path']}（{i.get('width')}×{i.get('height')}）")
        body += [f"   - 部分画像: {p}" for p in i.get("tiles", [])]
    body += ["", "## 資料（text）", rec["text"], "", "## 出力の形（JSON スキーマ。これに合う JSON だけを書く）",
             json.dumps(rec["schema"], ensure_ascii=False, indent=1), "",
             f"## 答えの書き先: inbox/{rec['id']}.json に {{\"data\": <答え>, \"model\": \"<モデル名>\"}}", END_MARK]
    lines = "\n".join(body).split("\n")
    head = f"# 依頼 {rec['id']}（{rec['task']}・{rec['source_label']}）全 {len(lines) + 1} 行。最後の行が {END_MARK} であることを確かめる"
    return head + "\n" + "\n".join(lines) + "\n"


def validate(data, schema: dict) -> list[str]:
    """答えが出力の形（JSON スキーマ）に合うか。合わない点の一覧（空なら合格）。"""
    import jsonschema
    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path)) or '(全体)'}: {e.message}" for e in v.iter_errors(data)][:20]


class Queue:
    """受け渡しのフォルダ。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        for sub in ("requests", "images", "inbox", "answers", "rejected"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def log(self, **kw) -> None:
        with open(self.root / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), **kw}, ensure_ascii=False) + "\n")

    def request(self, rid: str) -> dict:
        return json.loads((self.root / "requests" / f"{rid}.json").read_text(encoding="utf-8"))

    def pending(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "requests").glob("*.json")
                      if not (self.root / "answers" / p.name).exists())

    def answer_for(self, rec: dict):
        """検査済みの答え（無ければ None）。依頼が変わっていれば使わない（壊れた・古い答えの検出）。"""
        ap = self.root / "answers" / f"{rec['id']}.json"
        if not ap.exists():
            return None
        try:
            a = json.loads(ap.read_text(encoding="utf-8"))
        except ValueError:
            self._reject(rec["id"], ap.read_text(encoding="utf-8", errors="replace"), ["answers の JSON が壊れている"])
            ap.unlink()
            return None
        # 依頼のファイルが書き換えられていないか（中身から指紋を計算し直す）と、答えがその依頼のものかを確かめる
        if not digest_ok(rec, rec.get("record_sha256")) or not digest_ok(rec, a.get("record_sha256")) \
                or validate(a.get("data"), rec["schema"]):
            self._reject(rec["id"], json.dumps(a, ensure_ascii=False), ["依頼が変わった・形に合わない（answers）"])
            ap.unlink()
            return None
        return a

    def _reject(self, rid: str, content: str, why: list[str]) -> Path:
        n = len(list((self.root / "rejected").glob(f"{rid}.*.json")))
        p = self.root / "rejected" / f"{rid}.{n}.json"
        p.write_text(json.dumps({"why": why, "content": content}, ensure_ascii=False, indent=1), encoding="utf-8")
        self.log(event="rejected", id=rid, why=why)
        (self.root / "assigned" / rid).unlink(missing_ok=True)  # 答え直せるように、受け渡し中の印を外す
        return p

    def ingest(self, model_label: str = "") -> dict:
        """受け取り箱（inbox）の答えを検査して取り込む。戻り値: 件数（取り込み・却下・重複・依頼なし）。"""
        out = {"accepted": 0, "rejected": 0, "duplicate": 0, "unknown": 0}
        for p in sorted((self.root / "inbox").glob("*.json")):
            rid = p.stem
            raw = p.read_text(encoding="utf-8", errors="replace")
            rq = self.root / "requests" / f"{rid}.json"
            if not rq.exists():
                self._reject(rid, raw, ["対応する依頼が無い"])
                out["unknown"] += 1
                p.unlink()
                continue
            rec = self.request(rid)
            if (self.root / "answers" / f"{rid}.json").exists():  # 既に答えがある: 最初の答えを使い、後の答えは記録だけ
                self._reject(rid, raw, ["重複した答え（最初の答えを使う）"])
                out["duplicate"] += 1
                p.unlink()
                continue
            try:
                body = json.loads(raw)
            except ValueError as e:
                self._reject(rid, raw, [f"JSON として読めない: {e}"])
                out["rejected"] += 1
                p.unlink()
                continue
            if not (isinstance(body, dict) and "data" in body):  # 決まった形 {"data": ..., "model": ...} だけを受け付ける
                self._reject(rid, raw, ['形が {"data": <答え>, "model": "..."} ではない'])
                out["rejected"] += 1
                p.unlink()
                continue
            if not digest_ok(rec, rec.get("record_sha256")):
                self._reject(rid, raw, ["依頼のファイルが書き換えられている（作り直しが必要）"])
                out["rejected"] += 1
                p.unlink()
                continue
            data = body["data"]
            errs = validate(data, rec["schema"])
            if errs:
                self._reject(rid, raw, errs)
                out["rejected"] += 1
                p.unlink()
                continue
            ans = {"data": data, "model": body.get("model") or model_label, "answered_by": "claude-code", "record_sha256": rec["record_sha256"],
                   "ingested_at": time.time()}
            tmp = self.root / "answers" / f".{rid}.tmp"
            tmp.write_text(json.dumps(ans, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.root / "answers" / f"{rid}.json")  # 途中で止まっても壊れた答えを残さない
            p.unlink()
            out["accepted"] += 1
            self.log(event="accepted", id=rid)
        return out


class QueueProvider(AIProvider):
    name = "claude_code_queue"
    external = True  # Claude Code も Anthropic に送るので、外部送信の許可は API と同じに扱う

    def __init__(self, queue_dir: Path, model_label: str = "claude-code", max_edge: int = 2576):
        self.q = Queue(queue_dir)
        self.dir = self.q.root
        self.model = model_label
        self.max_edge = max_edge

    def _answer(self, req: AIRequest):
        rid = request_id(req)
        rp = self.dir / "requests" / f"{rid}.json"
        if rp.exists():
            rec = json.loads(rp.read_text(encoding="utf-8"))
            missing = [p for i in rec["images"] for p in [i["path"], *i.get("tiles", [])]
                       if not Path(p).exists() or not _readable(Path(p))]
            if rec.get("version") != RECORD_VERSION or missing:  # 古い・画像が欠けた依頼は作り直す
                rec = None
        else:
            rec = None
        if rec is None:
            rec = request_record(req, self.dir / "images", self.max_edge)
            md = rp.with_suffix(".md")
            md.write_text(render_markdown(rec), encoding="utf-8")
            tmp = rp.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(rp)
        a = self.q.answer_for(rec)
        if a is None:
            return AwaitingAnswer(f"未回答（Claude Code の回答待ち）: {rid}")
        return AIResponse(data=a["data"], model=a.get("model") or self.model, input_tokens=0, output_tokens=0,
                          raw_text=json.dumps(a["data"], ensure_ascii=False))

    def analyze(self, req: AIRequest) -> AIResponse:
        r = self._answer(req)
        if isinstance(r, Exception):
            raise r
        return r

    def analyze_batch(self, reqs: list[AIRequest], *, log=print, known=None, on_created=None, **_) -> list:
        out = [self._answer(r) for r in reqs]
        missing = sum(isinstance(r, Exception) for r in out)
        if missing:
            log(f"  Claude Code の回答待ち: {missing} 件（{self.dir / 'requests'}）")
        return out


def pending(queue_dir: Path) -> list[Path]:
    q = Queue(queue_dir)
    return [q.root / "requests" / f"{r}.json" for r in q.pending()]


def answer(queue_dir: Path, rid: str, data_file: Path, model: str) -> list[str]:
    """答えを受け取り箱に置いて取り込む。戻り値: 形の合わない点（空なら取り込んだ）。"""
    q = Queue(queue_dir)
    body = Path(data_file).read_text(encoding="utf-8")
    (q.root / "inbox" / f"{rid}.json").write_text(json.dumps({"data": json.loads(body), "model": model},
                                                             ensure_ascii=False), encoding="utf-8")
    before = len(list((q.root / "rejected").glob(f"{rid}.*.json")))
    q.ingest(model)
    rej = sorted((q.root / "rejected").glob(f"{rid}.*.json"))
    if len(rej) > before:
        return json.loads(rej[-1].read_text(encoding="utf-8"))["why"]
    return []


def api_request(rec: dict) -> AIRequest:
    """依頼の記録から、API に送るのと同じ AIRequest を作り直す（見本の比較用）。"""
    from .base import AIImage
    a = rec["api"]
    return AIRequest(task=rec["task"], source_label=rec["source_label"], text=a["text"], system=a["system"],
                     schema=a["schema"], max_tokens=rec.get("max_tokens"), t_start=a.get("t_start"), t_end=a.get("t_end"),
                     cache_prefix_chars=a.get("cache_prefix_chars") or 0,
                     prefix_before_images=a.get("prefix_before_images", False), cache_ttl=a.get("cache_ttl") or "",
                     images=[AIImage(Path(i["path"]), i["label"], cache=i["cache"], lossless=i["lossless"]) for i in a["images"]])
