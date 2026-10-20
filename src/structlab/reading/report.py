"""読み取り結果の報告（Markdown / JSON）。資料ごとの書き起こしと照合、全体の精度指標・要確認・未照合を書く。"""
from __future__ import annotations

import json
from pathlib import Path

from ..models import fmt_time

STATUS = {"ok": "照合済み", "needs_review": "要確認", "unverified": "未照合", "text_only": "テキスト層で取得"}


def _ai_md(ai: dict | None) -> list[str]:
    if not ai:
        return []
    out = []
    if ai.get("title"):
        out.append(f"**{ai['title']}**")
    for b in ai.get("text_blocks", []):
        out.append(f"- [{b['role']}] {b['text']}")
    for t in ai.get("tables", []):
        out.append(f"\n表: {t.get('caption') or '（見出しなし）'}")
        if t.get("header"):
            out.append("| " + " | ".join(t["header"]) + " |")
            out.append("|" + "---|" * len(t["header"]))
        out += ["| " + " | ".join(r) + " |" for r in t.get("rows", [])]
        if t.get("notes"):
            out.append(f"（注: {t['notes']}）")
    for f in ai.get("formulas", []):
        out.append(f"- 式: `{f['latex']}` … {f['plain']}" + (f"（{f['where']}）" if f.get("where") else ""))
    for g in ai.get("figures", []):
        out.append(f"- 図（{g['kind']}）: {g.get('caption') or ''} {g.get('description', '')}")
        for a in g.get("axes", []):
            out.append(f"  - 軸 {a['axis']}: {a['label']} [{a['unit']}] 範囲 {a['range']}")
        if g.get("legend"):
            out.append("  - 凡例: " + "、".join(g["legend"]))
        if g.get("labels"):
            out.append("  - 図中の文字: " + "、".join(g["labels"]))
        for v in g.get("values_read", []):
            out.append(f"  - {v['what']}: {v['value']}" + ("（グラフからの概算）" if v.get("approx") else ""))
    for o in ai.get("operations", []):
        out.append(f"- 操作 [{o['time']}] {o['action']} {o['target']} {o['value']}")
    for u in ai.get("unreadable", []):
        out.append(f"- 読めない: {u['where']}（{u['reason']}）")
    if ai.get("notes"):
        out.append(f"- 注記: {ai['notes']}")
    return out


def _pdf_md(rec: dict) -> str:
    lines = [f"# {rec['path']}", "", f"PDF {rec['n_pages']} ページ。文字はテキスト層（正確）、図・写真・グラフはAIの読み取り＋照合。", ""]
    for p, v in zip(rec["pages"], rec["verification"]):
        lines += [f"## p.{p['page']}　[{STATUS[v['status']]}]", v.get("detail", ""), "",
                  "### 文字情報（見た目の行の順）", "```", *p["rows"], "```"]
        if p.get("ai"):
            lines += ["### 書き起こし（AI）", *_ai_md(p["ai"])]
            if v.get("missing_lines"):
                lines.append("- 書き起こしで確認できないテキスト層の行: " + "、".join(v["missing_lines"][:10]))
        elif p["needs_vision"]:
            lines.append(f"- 画像・図があります（画像の面積 {p['image_area_ratio']:.0%}、図形 {p['n_drawings']} 要素）。内容は未読み取り")
        lines.append("")
    return "\n".join(lines)


def _video_md(rec: dict) -> str:
    sc_all = rec["scenes"]
    lines = [f"# {rec['path']}", "",
             f"長さ {fmt_time(rec['duration'])}、{rec.get('mode', '-')} モード、場面 {len(sc_all)}（異なる画面 {rec.get('n_unique_scenes', 0)}）、"
             f"発話 {len(rec['speech'])} 区間（{rec.get('transcriber', '文字起こしなし')}）",
             "資料の領域: " + (f"{rec['region']['w']}x{rec['region']['h']}（画面録画の時計・ツールバーを除いて切り出し）"
                          if rec.get("region_cropped") else "画面全体"), ""]
    if rec.get("speech_fix"):
        f = rec["speech_fix"]
        lines += [f"文字起こしの専門用語の訂正: 適用 {f['applied']} 件（資料の用語で根拠を確認したものだけ）、"
                  f"不採用 {len(f['rejected'])} 件、判断保留 {len(f.get('uncertain', []))} 件", ""]
    if rec.get("ocr_vs_pdf"):
        o = rec["ocr_vs_pdf"]
        lines += [f"OCR精度の実測: PDFと同じ内容の場面 {o['scenes']} 件で、PDFの行の {o['mean_line_coverage']:.0%} をOCRで読めた", ""]
    for sc, v in zip(sc_all, rec["verification"]):
        head = f"## 場面 {sc['index']} [{fmt_time(sc['start'])}–{fmt_time(sc['end'])}]　[{STATUS[v['status']]}]"
        if sc.get("same_as") is not None:
            head += f"（場面 {sc['same_as']} と同じ画面）"
        lines += [head, v.get("detail", "")]
        if sc.get("pdf_match"):
            m = sc["pdf_match"]
            lines.append(f"- 対応するPDF: {m['file']} p.{m['page']}（一致度 {m['similarity']}）")
        if sc.get("same_as") is None:
            if sc.get("ai"):
                lines += ["### 画面の書き起こし（AI）", *_ai_md(sc["ai"])]
            lines += ["### 画面のOCR（ローカル・誤りを含む）", "```", (sc.get("ocr") or "（なし）")[:2000], "```"]
        if sc.get("events"):
            lines.append(f"- 場面内の画面の変化 {len(sc['events'])} 回: " + "、".join(fmt_time(e["time"]) for e in sc["events"][:20]))
        if sc.get("speech"):
            lines.append("### 発話")
            for s in sc["speech"]:
                ln = f"- [{fmt_time(s['start'])}] {s['text']}" + (" （信頼度低）" if (s.get("avg_logprob") or 0) < -1.0 else "")
                if s.get("corrections"):
                    ln += "（訂正: " + "、".join(f"{c['original']}→{c['corrected']}" for c in s["corrections"]) + "）"
                lines.append(ln)
        lines.append("")
    return "\n".join(lines)


def write_reports(cfg, out: Path, items, records: dict, est: dict, usage: dict, *, ai_enabled: bool) -> dict:
    ai_cfg = cfg.data["ai"]
    pin, pout = float(ai_cfg.get("price_input_per_mtok", 0)), float(ai_cfg.get("price_output_per_mtok", 0))
    cost = est["input_tokens"] / 1e6 * pin + est["output_tokens"] / 1e6 * pout
    summary: dict = {"files": [], "estimate": {**est, "usd": round(cost, 2)}, "ai_usage": usage, "ai_enabled": ai_enabled}
    counts = {"ok": 0, "needs_review": 0, "unverified": 0, "text_only": 0}
    for path, rec in records.items():
        entry = {"path": path, "kind": rec.get("kind")}
        if rec.get("error"):
            entry["error"] = rec["error"]
            summary["files"].append(entry)
            continue
        md = _pdf_md(rec) if rec["kind"] == "pdf" else _video_md(rec)
        d = Path(rec["dir"])
        (d / "reading.md").write_text(md, encoding="utf-8")
        (d / "reading.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        st = {k: 0 for k in counts}
        for v in rec["verification"]:
            st[v["status"]] += 1
            counts[v["status"]] += 1
        entry.update({"status": st, "report": str(d / "reading.md"), "local_seconds": rec.get("local_seconds")})
        if rec["kind"] == "pdf":
            entry["pages"] = rec["n_pages"]
            entry["pages_needing_vision"] = sum(p["needs_vision"] for p in rec["pages"])
        else:
            entry.update({"duration_min": round(rec["duration"] / 60, 1), "scenes": len(rec["scenes"]),
                          "unique_scenes": rec.get("n_unique_scenes"), "speech_segments": len(rec["speech"]),
                          "low_conf_speech": sum((s.get("avg_logprob") or 0) < -1.0 for s in rec["speech"]),
                          "region_cropped": rec.get("region_cropped"), "ocr_vs_pdf": rec.get("ocr_vs_pdf"),
                          "pdf_linked_scenes": sum(1 for s in rec["scenes"] if s.get("pdf_match"))})
        summary["files"].append(entry)
    summary["status_counts"] = counts
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 資料の読み取り結果（概要）", "",
          f"照合済み {counts['ok']}、要確認 {counts['needs_review']}、未照合 {counts['unverified']}（未照合は成功ではない）、"
          f"テキスト層で取得 {counts['text_only']}", "", "## 資料ごと", ""]
    for e in summary["files"]:
        if e.get("error"):
            md.append(f"- {e['path']}: 失敗 {e['error']}")
        elif e["kind"] == "pdf":
            md.append(f"- {e['path']}: {e['pages']} ページ（図・写真のあるページ {e['pages_needing_vision']}）"
                      f" 状態 {e['status']}")
        else:
            md.append(f"- {e['path']}: {e['duration_min']} 分、場面 {e['scenes']}（異なる画面 {e['unique_scenes']}）、"
                      f"発話 {e['speech_segments']} 区間（信頼度低 {e['low_conf_speech']}）、PDFと対応する場面 {e['pdf_linked_scenes']}"
                      + (f"、OCR実測 {e['ocr_vs_pdf']['mean_line_coverage']:.0%}" if e.get("ocr_vs_pdf") else "")
                      + f" 状態 {e['status']}")
    md += ["", "## AI の読み取り（有料）", "",
           f"必要な呼び出し {est['total_calls']} 回（PDFのページ {est['calls']['pdf_page']}、動画の場面 {est['calls']['video_scene']}、"
           f"操作を含む場面 {est['calls']['video_ops']}、文字起こしの用語訂正 {est['calls']['speech_fix']}）、画像 {est['images']} 枚",
           f"概算: 入力 {est['input_tokens']:,} トークン・出力 {est['output_tokens']:,} トークン → 約 ${cost:.2f}"
           f"（単価 入力 ${pin}/100万・出力 ${pout}/100万。実際は画像の大きさ・出力量で前後する）",
           f"今回の実行: AI {'あり' if ai_enabled else 'なし（見積もりのみ）'}、呼び出し {usage['calls']} 回、失敗 {usage['failed']} 回、"
           f"入力 {usage['input_tokens']:,}・出力 {usage['output_tokens']:,} トークン"]
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "estimate.md").write_text("\n".join(md[-4:]) + "\n", encoding="utf-8")
    return summary
