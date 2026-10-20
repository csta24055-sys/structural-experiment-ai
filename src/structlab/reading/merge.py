"""GitHub Actions で資料ごとに並列に読み取った結果（summary.json）を1つにまとめて表示する。

  python -m structlab.reading.merge <読み取り結果のフォルダ>...
PDF はどのジョブでも読むので、同じ資料は1回だけ数える。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def merge(dirs: list[Path]) -> dict:
    files: dict[str, dict] = {}
    est = {"calls": {}, "total_calls": 0, "images": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0}
    seen_est: set[str] = set()
    for d in dirs:
        for sp in sorted(Path(d).rglob("summary.json")):
            s = json.loads(sp.read_text(encoding="utf-8"))
            for f in s["files"]:
                if f["path"] in files and f.get("kind") == "pdf":
                    continue
                files[f["path"]] = f
            key = json.dumps(sorted(f["path"] for f in s["files"] if f.get("kind") != "pdf"))
            if key not in seen_est:
                seen_est.add(key)
                for k, v in s["estimate"]["calls"].items():
                    # PDF のページは各ジョブで重複するので最大値を使う
                    est["calls"][k] = max(est["calls"].get(k, 0), v) if k == "pdf_page" else est["calls"].get(k, 0) + v
                for k in ("images", "input_tokens", "output_tokens"):
                    est[k] += s["estimate"][k]
                est["usd"] += s["estimate"].get("usd", 0.0)
            for k in usage:
                usage[k] += s["ai_usage"].get(k, 0)
    est["total_calls"] = sum(est["calls"].values())
    counts = {"ok": 0, "needs_review": 0, "unverified": 0, "text_only": 0}
    for f in files.values():
        for k, v in (f.get("status") or {}).items():
            counts[k] = counts.get(k, 0) + v
    return {"files": list(files.values()), "estimate": est, "ai_usage": usage, "status_counts": counts}


def to_markdown(m: dict) -> str:
    c, e, u = m["status_counts"], m["estimate"], m["ai_usage"]
    lines = ["# 資料の読み取り結果（全ジョブの合計）", "",
             f"照合済み {c['ok']}、要確認 {c['needs_review']}、未照合 {c['unverified']}（未照合は成功ではない）、テキスト層で取得 {c['text_only']}", ""]
    for f in m["files"]:
        if f.get("error"):
            lines.append(f"- {f['path']}: 失敗 {f['error']}")
        elif f["kind"] == "pdf":
            lines.append(f"- {f['path']}: {f['pages']} ページ（図・写真のあるページ {f['pages_needing_vision']}） 状態 {f['status']}")
        else:
            lines.append(f"- {f['path']}: {f['duration_min']} 分、場面 {f['scenes']}（異なる画面 {f['unique_scenes']}）、"
                         f"発話 {f['speech_segments']} 区間（信頼度低 {f['low_conf_speech']}）、PDFと対応 {f['pdf_linked_scenes']}、"
                         f"切り出し {'あり' if f.get('region_cropped') else 'なし'}、ローカル処理 {f.get('local_seconds')} 秒 状態 {f['status']}")
    lines += ["", f"AI の読み取りの見積もり: {e['total_calls']} 回 {e['calls']}、画像 {e['images']} 枚、"
              f"入力 {e['input_tokens']:,}・出力 {e['output_tokens']:,} トークン → 約 ${e['usd']:.2f}",
              f"今回の AI 使用: {u['calls']} 回（失敗 {u['failed']}）、入力 {u['input_tokens']:,}・出力 {u['output_tokens']:,} トークン"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(to_markdown(merge([Path(a) for a in sys.argv[1:]])))
