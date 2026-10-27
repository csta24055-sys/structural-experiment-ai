"""AI解析を始める前の、呼び出し回数・トークン数・費用の概算（実際のAPIは呼ばない）。

あくまで目安。日本語は1文字≒0.7〜1.2トークン、画像1枚≒1,600トークン（長辺1568px）として計算する。
出力トークンは段階ごとの典型値の範囲で見積もる。料金は ai.price_*_per_mtok（設定値）を使う。
"""
from __future__ import annotations

import math

from ..db import DB
from . import prompts as P
from .engine import ELIGIBLE_KINDS
from .material import SCENE_TYPES, build_scenes, lines_text

IMG_TOKENS = 1600
OUT = {"scene": (800, 2500), "synthesis": (2000, 8000), "reinterpret": (300, 2500), "directives": (500, 4000),
       "verify": (300, 1200), "corpus": (3000, 12000), "legacy": (300, 1500)}


def estimate(db: DB, cfg) -> dict:
    sc = cfg.data["semantic"]
    ai = cfg.data["ai"]
    max_img = int(ai["max_images_per_call"])
    rounds = int(sc.get("max_rounds", 2))
    cap = int(sc.get("max_verify_per_doc", 40))
    rows, tot = [], {"calls_min": 0, "calls_max": 0, "in_min": 0, "in_max": 0, "out_min": 0, "out_max": 0, "images": 0}
    for f in db.q("SELECT * FROM files WHERE status IN ('done','partial') ORDER BY path"):
        if f["kind"] not in ELIGIBLE_KINDS:
            continue
        done = db.q1("SELECT 1 FROM sem_docs WHERE file_id=? AND file_hash=? AND version=? AND mode='ai' AND status='done'",
                     (f["id"], f["content_hash"] or "", P.SEMANTIC_VERSION))
        scenes = build_scenes(db, f)
        if not scenes:
            continue
        n = len(scenes)
        chars = sum(len(lines_text(s.lines)) + len(s.material) for s in scenes)
        imgs = sum(min(len(s.images), max_img) for s in scenes)
        cands = db.q1("SELECT COUNT(*) c FROM directives d JOIN sem_docs s ON s.id=d.doc_id WHERE s.file_id=? AND "
                      "s.file_hash=? AND s.mode='local'", (f["id"], f["content_hash"] or ""))["c"]
        legacy = 0
        if not (sc.get("ai") and sc.get("replace_unit_analysis")):
            legacy = db.q1("SELECT COUNT(*) c FROM units WHERE file_id=? AND file_hash=? AND status='done' AND ai_status NOT IN "
                           "('done','failed_final') AND unit_type IN (%s)" % ",".join("?" * len(SCENE_TYPES)),
                           (f["id"], f["content_hash"] or "", *SCENE_TYPES))["c"]
        e = {"file": f["path"], "scenes": n, "chars": chars, "images": imgs, "rule_candidates": cands,
             "already_done": bool(done), "legacy_unit_calls": legacy}
        if done or not sc.get("ai"):
            e.update(calls_min=legacy, calls_max=legacy)
            stages = {}
        else:
            ver_max = min(cap, cands + n)
            stages = {
                "scene": (n, n), "synthesis": (1, 1 + rounds), "reinterpret": (n, n * rounds),
                "directives": (n, n), "verify": (0, ver_max),
            }
            e.update(calls_min=sum(a for a, _ in stages.values()) + legacy,
                     calls_max=sum(b for _, b in stages.values()) + legacy)
        # 入力: 場面テキスト（前後の文脈を含め最大3倍）＋全体理解の参照、画像
        per_scene = chars / max(n, 1)
        in_min = in_max = 0
        if stages:
            in_min = int(chars * 0.7 * 3 + imgs * IMG_TOKENS + 2000 * (n * 3 + 1))
            in_max = int(chars * 1.2 * (3 + 2 * rounds + 3) + imgs * IMG_TOKENS * (1 + (rounds if sc.get("reinterpret_images") else 0))
                         + 6000 * (n * (2 + rounds) + rounds + 1) + per_scene * 1.2 * stages["verify"][1] * 2)
        legacy_in = int(legacy * (per_scene * 1.0 + max_img * IMG_TOKENS * 0.5))
        in_min += legacy_in
        in_max += legacy_in
        out_min = sum(OUT[k][0] * v[0] for k, v in stages.items()) + legacy * OUT["legacy"][0]
        out_max = sum(OUT[k][1] * v[1] for k, v in stages.items()) + legacy * OUT["legacy"][1]
        e.update(in_min=in_min, in_max=in_max, out_min=out_min, out_max=out_max)
        rows.append(e)
        for k in ("calls_min", "calls_max", "in_min", "in_max", "out_min", "out_max"):
            tot[k] += e[k]
        tot["images"] += imgs if stages else 0
    if sc.get("ai") and any(not r["already_done"] for r in rows):
        k = max(1, math.ceil(sum(r["rule_candidates"] for r in rows) * 120 / max(1, sc.get("corpus_max_chars", 300000))))
        tot["calls_min"] += 1
        tot["calls_max"] += k
        tot["in_min"] += 20000
        tot["in_max"] += 120000 * k
        tot["out_min"] += OUT["corpus"][0]
        tot["out_max"] += OUT["corpus"][1] * k
    pin, pout = float(ai.get("price_input_per_mtok", 4.0)), float(ai.get("price_output_per_mtok", 20.0))
    tot["usd_min"] = round(tot["in_min"] / 1e6 * pin + tot["out_min"] / 1e6 * pout, 2)
    tot["usd_max"] = round(tot["in_max"] / 1e6 * pin + tot["out_max"] / 1e6 * pout, 2)
    pending = db.q("SELECT path, status FROM files WHERE status IN ('discovered','ready','processing') AND parent_id IS NULL")
    lim = {k: ai[k] for k in ("max_calls_per_run", "max_input_tokens_per_run", "max_output_tokens_per_run", "max_calls_total")}
    return {"files": rows, "total": tot, "not_yet_extracted": [dict(r) for r in pending], "model": ai["model"],
            "price": {"input_per_mtok": pin, "output_per_mtok": pout}, "limits": lim,
            "semantic_ai_enabled": bool(sc.get("ai")), "replace_unit_analysis": bool(sc.get("replace_unit_analysis"))}


def estimate_text(e: dict) -> str:
    t = e["total"]
    L = ["# AI解析の概算（APIは呼んでいません）", "",
         f"- モデル: {e['model']} ／ 料金設定: 入力 ${e['price']['input_per_mtok']} ・出力 ${e['price']['output_per_mtok']} / 100万トークン（公式料金表で要確認）",
         f"- 意味理解のAI: {'有効' if e['semantic_ai_enabled'] else '無効'} ／ 単位ごとの従来AI解析の置き換え: {'する' if e['replace_unit_analysis'] else 'しない'}",
         f"- 呼び出し回数: {t['calls_min']}〜{t['calls_max']} 回 ／ 画像 {t['images']} 枚以上",
         f"- 入力トークン: {t['in_min']:,}〜{t['in_max']:,} ／ 出力トークン: {t['out_min']:,}〜{t['out_max']:,}",
         f"- **概算費用: ${t['usd_min']}〜${t['usd_max']}**（実際の値は資料の内容・AIの応答量で変わります）",
         f"- 1回の実行の上限設定: {e['limits']}（上限に達すると残りは次回に持ち越し）", "",
         "| 資料 | 場面 | 文字数 | 画像 | 規則候補 | 呼び出し | 状態 |", "|---|---:|---:|---:|---:|---|---|"]
    for r in e["files"]:
        L.append(f"| {r['file']} | {r['scenes']} | {r['chars']} | {r['images']} | {r['rule_candidates']} | "
                 f"{r['calls_min']}〜{r['calls_max']} | {'AI解析済み' if r['already_done'] else ''} |")
    if e["not_yet_extracted"]:
        L += ["", f"※ まだ抽出（ダウンロード・文字起こし等）が終わっていない {len(e['not_yet_extracted'])} ファイルは見積りに含まれていません。",
              "  先に AI なしで `structlab run` を実行してから見積もってください。"]
    return "\n".join(L) + "\n"
