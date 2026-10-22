#!/usr/bin/env python3
"""候補 G5: 読み取りの AI の依頼を Claude Code の受け渡し口（work/queue）に出し、答えのある分を読み取り結果に入れる。

無料の前処理の成果物（reading.json と画面）を展開したフォルダを入力にする（Dropbox には触れない）。
  python3 scripts/queue_reading.py <成果物のフォルダ>... --work work/g5 [--queue work/queue]
繰り返し実行すると、答えた分だけ先へ進む（第2段の拡大・用語の訂正は前の答えから作られるので数周かかる）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    from structlab.ai.queue_provider import QueueProvider, pending
    from structlab.config import load_config
    from structlab.costmodel import relocate
    from structlab.reading.runner import Reader
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--work", default="work/g5")
    ap.add_argument("--queue", default="work/queue")
    ap.add_argument("-c", "--config", default="configs/dropbox.yaml")
    a = ap.parse_args()
    cfg = load_config(a.config)
    cfg.data["reading"]["batch"] = True
    work = Path(a.work)
    recs = []
    for d in a.dirs:
        for rp in sorted(Path(d).rglob("reading.json")):
            r = json.loads(rp.read_text(encoding="utf-8"))
            if not r.get("path") or r.get("error") or any(x["path"] == r["path"] for x in recs):
                continue
            dest = work / "files" / rp.parent.name
            if not dest.exists():  # 作業用にコピー（成果物そのものは変えない）
                shutil.copytree(rp.parent, dest)
            r = relocate(json.loads((dest / "reading.json").read_text(encoding="utf-8")), dest)
            r["dir"] = str(dest)
            recs.append(r)
    reader = Reader(cfg, out_dir=work, provider=QueueProvider(Path(a.queue)), log=lambda m: print(m, flush=True))
    for _ in range(4):
        for r in recs:
            reader.ai_read(r)
        if not reader.flush_batch():
            break
    for r in recs:
        (Path(r["dir"]) / "reading.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"資料 {len(recs)} 件、Claude Code の回答待ち {len(pending(Path(a.queue)))} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
