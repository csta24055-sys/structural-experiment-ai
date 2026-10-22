"""候補 G5: AI の解析を、従量課金の API ではなく Claude Code（利用者の定額の契約）で行うための受け渡し口。

依頼（指示文・本文・画像・出力の形）は API に送るものと同じものをファイルに書き出す。Claude Code のエージェントが
1 件ずつ読み（画像は元のファイルを開く）、出力の形（JSON スキーマ）に合う答えを書き戻す。答えはスキーマで検査してから使う。
答えの無い依頼は「未回答（あとで再試行）」として返すので、同じコマンドを繰り返すと答えた分だけ先へ進む（途中で止めても続きから）。

  queue/requests/<id>.json  … 依頼（id は依頼の内容の指紋。同じ依頼は同じ id）
  queue/answers/<id>.json   … 答え {"data": {...}, "model": "...", "answered_by": "claude-code"}

使い方は docs/CLAUDE_CODE_QUEUE.md。API と同じ品質かは、見本で API の結果と比べるまで未検証（既定では使わない）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import AIError, AIProvider, AIRequest, AIResponse, AwaitingAnswer


def request_id(req: AIRequest) -> str:
    from ..reading.runner import request_hash
    return request_hash(req)


def request_record(req: AIRequest) -> dict:
    from .prompts import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_user_text
    text = req.text if req.schema is not None else build_user_text(req.task, req.source_label, req.text)
    return {"id": request_id(req), "task": req.task, "source_label": req.source_label,
            "system": req.system or SYSTEM_PROMPT, "text": text,
            "images": [{"path": str(Path(i.path).resolve()), "label": i.label, "lossless": bool(getattr(i, "lossless", False))}
                       for i in req.images],
            "schema": req.schema or OUTPUT_SCHEMA, "max_tokens": req.max_tokens}


def validate(data, schema: dict) -> list[str]:
    """答えが出力の形（JSON スキーマ）に合うか。合わない点の一覧（空なら合格）。"""
    import jsonschema
    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path)) or '(全体)'}: {e.message}" for e in v.iter_errors(data)][:20]


class QueueProvider(AIProvider):
    name = "claude_code_queue"
    external = True  # Claude Code も Anthropic に送るので、外部送信の許可は API と同じに扱う

    def __init__(self, queue_dir: Path, model_label: str = "claude-code"):
        self.dir = Path(queue_dir)
        (self.dir / "requests").mkdir(parents=True, exist_ok=True)
        (self.dir / "answers").mkdir(parents=True, exist_ok=True)
        self.model = model_label

    def _answer(self, req: AIRequest):
        rec = request_record(req)
        rp = self.dir / "requests" / f"{rec['id']}.json"
        if not rp.exists():
            rp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        ap = self.dir / "answers" / f"{rec['id']}.json"
        if not ap.exists():
            return AwaitingAnswer(f"未回答（Claude Code の回答待ち）: {rec['id']}")
        a = json.loads(ap.read_text(encoding="utf-8"))
        errs = validate(a.get("data"), rec["schema"])
        if errs:
            return AwaitingAnswer(f"回答が出力の形に合わない（答え直しが必要）: {errs[:3]}")
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
    d = Path(queue_dir)
    return sorted(p for p in (d / "requests").glob("*.json") if not (d / "answers" / p.name).exists())


def answer(queue_dir: Path, rid: str, data_file: Path, model: str) -> list[str]:
    """答えを検査して保存する。戻り値: 形の合わない点（空なら保存した）。"""
    d = Path(queue_dir)
    rec = json.loads((d / "requests" / f"{rid}.json").read_text(encoding="utf-8"))
    data = json.loads(Path(data_file).read_text(encoding="utf-8"))
    errs = validate(data, rec["schema"])
    if not errs:
        (d / "answers" / f"{rid}.json").write_text(
            json.dumps({"data": data, "model": model, "answered_by": "claude-code"}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return errs
