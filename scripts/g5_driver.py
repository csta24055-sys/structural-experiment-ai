#!/usr/bin/env python3
"""候補 G5（Claude Code の定額の契約で解析）の実行役。AI は呼ばない（依頼の作成・答えの取り込み・進捗だけ）。

答えは Claude Code の答え役のエージェント（.claude/agents/g5-answerer.md）が queue/inbox に書く。手順は docs/CLAUDE_CODE_QUEUE.md。

  python3 scripts/g5_driver.py prepare <無料の前処理の成果物>...   作業フォルダ（work/g5）と G5 用の設定を作る
  python3 scripts/g5_driver.py step        取り込み → 依頼の作成（読み取り → 理解・統合 → 資料横断）を 1 回進める
  python3 scripts/g5_driver.py next [-n N] 答え役に渡す未回答の依頼（受け渡し中の印をつける）
  python3 scripts/g5_driver.py status      進捗・却下・完了の判定
  python3 scripts/g5_driver.py fake-answer --test-only   （テスト専用）出力の形だけ合う仮の答えを書く。本番の判定では不合格になる

有料の API を呼ばないための決まり: 設定の ai.provider は claude_code_queue に固定し、子の処理の環境から ANTHROPIC_API_KEY を外す。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
FAKE_MODEL = "TEST-FAKE（本番では使えない）"
ASSIGN_TTL = 2 * 3600  # 受け渡し中の印の有効時間（答え役が止まった場合はこの後に再び渡す）


def paths(work: Path) -> dict:
    return {"work": work, "queue": work / "queue", "cfg": work / "config.yaml", "files": work / "files",
            "corpus": work / "corpus", "sem": work / "corpus" / "semantic_run", "assigned": work / "queue" / "assigned"}


def env() -> dict:
    e = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_KEY", "CLAUDE_KEY"):
        e.pop(k, None)  # G5 では有料の API の鍵を子の処理に渡さない
    e["PYTHONPATH"] = str(ROOT / "src")
    return e


def guard(P: dict) -> None:
    import yaml
    c = yaml.safe_load(P["cfg"].read_text(encoding="utf-8"))
    if c["ai"]["provider"] != "claude_code_queue" or c["ai"].get("escalation_model"):
        raise SystemExit("G5 の設定ではありません（ai.provider が claude_code_queue でない、または上位モデルが設定されている）。中止します")


def prepare(work: Path, dirs: list[str], config: str) -> None:
    import yaml
    P = paths(work)
    work.mkdir(parents=True, exist_ok=True)
    c = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    c["ai"].update(provider="claude_code_queue", queue_dir=str(P["queue"].resolve()), escalation_model="",
                   allow_external=False)
    c["output"]["work_dir"] = str(work.resolve())
    P["cfg"].write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
    n = 0
    for d in dirs:
        for rp in sorted(Path(d).rglob("reading.json")):
            dest = P["files"] / rp.parent.name
            if not dest.exists():
                shutil.copytree(rp.parent, dest)
                n += 1
        for inv in Path(d).rglob("inventory.json"):
            if not (work / "inventory.json").exists():
                shutil.copy2(inv, work / "inventory.json")
    print(f"作業フォルダ {work}: 資料 {n} 件を追加。設定 {P['cfg']}（ai.provider=claude_code_queue）")


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, cwd=ROOT, env=env(), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"失敗: {' '.join(cmd)}\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}")
    return r.stdout


def step(work: Path) -> dict:
    """答えを取り込み、次の依頼を作る。読み取りが全部そろうまで理解・統合には進まない（材料が欠けた理解を作らない）。"""
    from structlab.ai.queue_provider import Queue
    P = paths(work)
    guard(P)
    q = Queue(P["queue"])
    got = q.ingest()
    py = sys.executable
    run([py, str(ROOT / "scripts" / "queue_reading.py"), str(P["files"]), "--work", str(work), "--queue", str(P["queue"]),
         "-c", str(P["cfg"])])
    reading_pending = [r for r in q.pending() if q.request(r)["task"].startswith(("read_", "fix_speech"))]
    stage = "reading"
    if not reading_pending:
        stage = "semantic"
        run([py, "-m", "structlab.reading.corpus", str(P["files"]), "--out", str(P["corpus"]), "-c", str(P["cfg"]),
             "--allow-external-ai"])
        run([py, "-m", "structlab.reading.semantic_bridge", str(P["files"]), "--out", str(P["sem"]), "-c", str(P["cfg"]),
             "--allow-external-ai"])
    s = status(work, quiet=True)
    s.update(ingested=got, stage=stage)
    (work / "g5_status.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    return s


def status(work: Path, quiet: bool = False) -> dict:
    from collections import Counter
    from structlab.ai.queue_provider import Queue
    P = paths(work)
    q = Queue(P["queue"])
    reqs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (P["queue"] / "requests").glob("*.json")}
    pend = set(q.pending())
    fake = [p.stem for p in (P["queue"] / "answers").glob("*.json")
            if json.loads(p.read_text(encoding="utf-8")).get("model") == FAKE_MODEL]
    by_task = Counter((r["task"], r["id"] in pend) for r in reqs.values())
    sem_done = None
    if (P["sem"] / "semantic.db").exists():
        from structlab.db import DB
        db = DB(P["sem"] / "semantic.db")
        rows = db.q("SELECT status, step FROM sem_docs WHERE mode='ai'")
        corpus_ok = bool(db.q1("SELECT 1 FROM sem_understanding WHERE scope='corpus'"))
        db.close()
        sem_done = {"docs": len(rows), "done": sum(r["status"] == "done" for r in rows), "corpus": corpus_ok}
    corr = None
    cj = P["corpus"] / "experiment_correspondence.json"
    if cj.exists():
        corr = json.loads(cj.read_text(encoding="utf-8")).get("status")
    files = [json.loads(p.read_text(encoding="utf-8")) for p in P["files"].glob("*/reading.json")]
    complete = (not pend and bool(reqs) and not fake and sem_done is not None and sem_done["docs"] > 0
                and sem_done["done"] == sem_done["docs"] and sem_done["corpus"] and corr not in (None, "awaiting"))
    s = {"requests": len(reqs), "answered": len(reqs) - len(pend), "pending": len(pend),
         "rejected_files": len(list((P["queue"] / "rejected").glob("*.json"))),
         "by_task": {f"{t} {'未回答' if p else '回答済み'}": n for (t, p), n in sorted(by_task.items())},
         "materials": len(files), "semantic": sem_done, "correspondence": corr, "fake_answers": len(fake),
         "complete": complete}
    if not quiet:
        print(json.dumps(s, ensure_ascii=False, indent=1))
        if fake:
            print(f"注意: テスト用の仮の答えが {len(fake)} 件あります。本番の完了とは判定しません")
    return s


def next_ids(work: Path, n: int) -> list[str]:
    from structlab.ai.queue_provider import Queue
    P = paths(work)
    q = Queue(P["queue"])
    P["assigned"].mkdir(parents=True, exist_ok=True)
    now = time.time()
    out = []
    for rid in q.pending():
        mark = P["assigned"] / rid
        if mark.exists() and now - mark.stat().st_mtime < ASSIGN_TTL:
            continue  # 別の答え役が答えている途中
        if (P["queue"] / "inbox" / f"{rid}.json").exists():
            continue  # 答えは届いていて、取り込み待ち
        mark.touch()
        out.append(rid)
        if len(out) >= n:
            break
    return out


def fake_answer(work: Path) -> int:
    """テスト専用: 出力の形にだけ合う最小の答えを書く（処理の流れの確認用。中身は意味を持たない）。"""
    from structlab.ai.queue_provider import Queue
    q = Queue(paths(work)["queue"])

    def minimal(s):
        if "anyOf" in s:
            return minimal(s["anyOf"][-1])
        t = s.get("type")
        if "enum" in s:
            return s["enum"][0]
        if t == "object":
            return {k: minimal(v) for k, v in s.get("properties", {}).items()}
        if t == "array":
            return []
        if t == "string":
            return ""
        if t in ("integer", "number"):
            return 0
        if t == "boolean":
            return False
        if t == "null":
            return None
        return None
    n = 0
    for rid in q.pending():
        rec = q.request(rid)
        (q.root / "inbox" / f"{rid}.json").write_text(json.dumps({"data": minimal(rec["schema"]), "model": FAKE_MODEL},
                                                                 ensure_ascii=False), encoding="utf-8")
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["prepare", "step", "next", "status", "fake-answer"])
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--work", default=str(ROOT / "work" / "g5"))
    ap.add_argument("-c", "--config", default=str(ROOT / "configs" / "dropbox.yaml"))
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--test-only", action="store_true")
    a = ap.parse_args()
    work = Path(a.work)
    if a.action == "prepare":
        prepare(work, a.dirs, a.config)
    elif a.action == "step":
        s = step(work)
        print(json.dumps({k: s[k] for k in ("stage", "ingested", "requests", "answered", "pending", "complete")},
                         ensure_ascii=False))
    elif a.action == "next":
        for rid in next_ids(work, a.n):
            print(rid)
    elif a.action == "status":
        status(work)
    elif a.action == "fake-answer":
        if not a.test_only:
            raise SystemExit("テスト専用です（--test-only）")
        print(f"仮の答え {fake_answer(work)} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
