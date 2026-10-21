"""費用の発生箇所を、工程・依頼の種類ごとに分けて見積もる（API は呼ばない）。

入力: 読み取りの結果（reading.json と保存した画面）と、意味理解の場面（semantic.db。読み取り結果から作ったもの）。
実測できる量（場面の数、発話・画面の文字数、画像の大きさ）は実際の値を使い、まだ測れない量（AI が書く量、
資料全体の理解の長さなど）は low / mid / high の仮定で幅を持たせる。仮定は ASSUME にまとめてある。

最適化ごとの効果（何円減るか）も、同じ式で「その最適化が無い場合」と比べて出す。

  structlab cost --reading <読み取り結果>... --semantic-db <semantic.db> --out <出力先>
"""
from __future__ import annotations

import json
from pathlib import Path

YEN_PER_USD = 155
PRICE = {"in": 4.0, "out": 20.0}          # Opus 5.5 標準（USD / 100万トークン）
CASES = ("low", "mid", "high")

ASSUME = {  # 実測できない量の仮定（推測）
    "tok_per_char": (0.8, 1.0, 1.2),             # 日本語の文字 → トークン
    "global_brief_chars": (8000, 14000, 20000),   # 資料全体の理解（最大 2 万字で切る）
    "scene_analysis_chars": (1500, 2500, 4000),   # 場面の理解（JSON）
    "reading_ai_chars_per_unit": (1500, 2500, 4000),  # 読み取りの AI の書き起こし（1 場面・1 ページ）
    "reinterpret_rounds": (1.0, 1.5, 2.0),
    "verify_per_doc": (15, 30, 40),               # 第3回の照合（上限 40/資料）
    "thinking_per_call": (500, 1500, 3000),       # 思考（出力に含まれる）
    "out_scene": (1000, 1500, 2500), "out_synthesis": (3000, 5000, 8000), "out_reinterpret_changes": (200, 400, 700),
    "out_directives": (1000, 2000, 3500), "out_verify": (300, 700, 1200), "out_corpus": (8000, 12000, 16000),
    "cache_hit": (0.3, 0.6, 0.9),                 # 同じ Batch の中でキャッシュから読まれる割合（先書きなし。保証されない）
    "cache_hit_seq5m": (0.0, 0.3, 0.6),           # 次の Batch（数分〜1時間後）で 5 分のキャッシュが残っている割合
    "cache_hit_seq1h": (0.7, 0.9, 0.98),          # 次の Batch で 1 時間のキャッシュが残っている割合
    "cache_hit_primed": (0.8, 0.95, 0.99),        # 1件で先に書いた後の Batch で読まれる割合（公式の推奨手順）
    "verify_images": 6, "verify_image_tokens": 1800,
}
PRIME_MIN_GROUP = 6   # ai/anthropic_provider.PRIME_MIN_GROUP と同じ
SYS_TOKENS = 400       # 意味理解の共通の指示文（キャッシュの最小 512 未満なのでキャッシュされない）
INSTR = {"scene": 430, "synthesis": 420, "reinterpret": 250, "directives": 1130, "verify": 360}


def _a(name, c):
    v = ASSUME[name]
    return v[CASES.index(c)] if isinstance(v, tuple) else v


def usd(inp: float, out: float, batch: bool = True) -> float:
    k = 0.5 if batch else 1.0
    return k * (inp * PRICE["in"] + out * PRICE["out"]) / 1e6


def cached_cost_factor(hit: float, ttl_1h: bool = False) -> float:
    """キャッシュする先頭部分 1 トークンあたりの入力の倍率（書き込み 2 倍〔1 時間〕/1.25 倍、読み出し 0.05 倍）。"""
    write = 2.0 if ttl_1h else 1.25
    return (1 - hit) * write + hit * 0.05


def prefix_units(n: float, mode: str, case: str) -> float:
    """同じ先頭部分（P トークン）を持つ n 件の依頼で、先頭部分にかかる入力を「P の何倍か」で返す。
    mode: none（キャッシュなし）/ batch（同じ Batch、先書きなし）/ seq5m・seq1h（1件ずつ次の Batch で使い直す）/
    primed（1件で先に 1 時間のキャッシュを書き、残りを次の Batch で。件数が PRIME_MIN_GROUP 未満なら batch と同じ）"""
    if n <= 0:
        return 0.0
    if mode == "none":
        return n
    if mode == "batch" or (mode == "primed" and n < PRIME_MIN_GROUP):
        return n * cached_cost_factor(_a("cache_hit", case))
    if mode == "seq5m":
        return 1.25 + (n - 1) * cached_cost_factor(_a("cache_hit_seq5m", case))
    if mode == "seq1h":
        return 2.0 + (n - 1) * cached_cost_factor(_a("cache_hit_seq1h", case), ttl_1h=True)
    if mode == "primed":
        return 2.0 + (n - 1) * cached_cost_factor(_a("cache_hit_primed", case), ttl_1h=True)
    raise ValueError(mode)


def _semantic(db_path: Path, case: str, directive_prefix_cache: bool, prefix_layout: bool, compact: bool,
              batch: bool, verify_group: int = 1, parallel: bool = True, priming: bool = True,
              only: str | None = None, layout_scope: str = "both", reinterpret_delta: bool = False) -> dict:
    """意味理解の費用。parallel: 指示の抽出・照合を資料ごとに 1 回の Batch にまとめる（内容は同じ）。
    priming: 同じ先頭部分の依頼が多い Batch では 1 件で先に 1 時間のキャッシュを書く（内容は同じ）。
    parallel=False, priming=False が M24 より前の構成（指示の抽出は場面ごとに次の Batch で 5 分のキャッシュを使い直す）。"""
    from .db import DB
    from .semantic.material import build_scenes, lines_text
    tpc = _a("tok_per_char", case)
    G = _a("global_brief_chars", case) * tpc
    A = _a("scene_analysis_chars", case) * tpc
    think = _a("thinking_per_call", case)
    mat_factor = 0.85 if compact else 1.0
    rows: dict[str, dict] = {}

    def add(kind, n, inp, out, prefix=0.0, units=None):
        """n 件、1 件の入力 inp（うち先頭部分 prefix）。units は先頭部分にかかる入力の合計（P の何倍か）。"""
        r = rows.setdefault(kind, {"calls": 0, "in_eff": 0.0, "in": 0.0, "out": 0.0})
        r["calls"] += n
        r["in"] += n * inp
        r["out"] += n * out
        u = n if units is None else units
        r["in_eff"] += n * (inp - prefix) + prefix * u
    db = DB(db_path)
    docs = 0
    dir_mode = ("primed" if priming else "batch") if parallel else ("seq5m" if directive_prefix_cache else "none")
    pl_r = prefix_layout and layout_scope in ("both", "reinterpret")   # 候補C1 の再解釈の部分
    pl_v = prefix_layout and layout_scope in ("both", "verify")        # 候補C1 の照合の部分
    for f in db.q("SELECT * FROM files WHERE status='done'"):
        if only and only not in f["path"]:
            continue
        scenes = build_scenes(db, f)
        if not scenes:
            continue
        docs += 1
        n = len(scenes)
        rounds = _a("reinterpret_rounds", case)
        ver = min(_a("verify_per_doc", case), 40)
        units_per_scene = max(1.0, sum(sc.material.count("### 画面") or 1 for sc in scenes) / n)
        dir_P = INSTR["directives"] + G
        for i, sc in enumerate(scenes):
            L = min(len(lines_text(sc.lines)), 30000) * tpc
            M = units_per_scene * _a("reading_ai_chars_per_unit", case) * tpc * mat_factor
            nxt = min(len(lines_text(scenes[i + 1].lines)), 3000) * tpc if i + 1 < n else 0
            add("1 場面の理解", 1, SYS_TOKENS + INSTR["scene"] + min(L + M, 30000 * tpc) + 1500 * tpc + nxt,
                _a("out_scene", case) + think)
            Lr = min(L, 15000 * tpc)
            # 再解釈は場面の順に 1 件ずつ（次の Batch）。候補C1 では資料全体の理解を先頭に置き 1 時間キャッシュ
            add("1 再解釈", rounds, SYS_TOKENS + INSTR["reinterpret"] + Lr + A + G + 3000 * tpc,
                # 出力: 今の方式は変更の有無にかかわらず場面の解釈の全体を書き直す（＋変更点）。候補C5 は変わった項目だけ
                (_a("out_reinterpret_changes", case) * (1.5 if reinterpret_delta else 1)
                 + (0 if reinterpret_delta else _a("out_scene", case)) + think), prefix=G if pl_r else 0,
                units=rounds * prefix_units(n, "seq1h", case) / n if pl_r else None)
            Ld = min(L, 20000 * tpc) + min(M, 10000 * tpc) + 2 * min(L, 4000 * tpc)
            add("2 指示の抽出", 1, SYS_TOKENS + INSTR["directives"] + G + A + Ld + 800,
                _a("out_directives", case) + think,
                prefix=dir_P if dir_mode != "none" else 0, units=prefix_units(n, dir_mode, case) / n)
        add("1 全体の理解", 1 + rounds, SYS_TOKENS + INSTR["synthesis"] + n * A + G * 0.5, _a("out_synthesis", case) + think)
        win = min(sum(len(lines_text(sc.lines)) for sc in scenes) / n * 0.8, 20000) * tpc
        img = _a("verify_images", case) * _a("verify_image_tokens", case)
        ver_P = INSTR["verify"] + G
        if verify_group > 1:  # 候補C4: 同じ場面の指示（平均 2.5 件）をまとめる。範囲・画面は合わせて 1.6 倍
            k = min(verify_group, 2.5)
            calls = ver / k
            add("3 元資料との照合", calls, SYS_TOKENS + INSTR["verify"] + 500 * k + win * 1.6 + G + img * 1.6,
                _a("out_verify", case) * k + think,
                prefix=ver_P if pl_v else 0,
                units=prefix_units(calls, "primed" if (priming and parallel) else "batch", case) if pl_v else None)
        else:  # 従来は画像が先頭なのでキャッシュは効かない。候補C1 では指示文と資料全体の理解を画像の前に置く
            add("3 元資料との照合", ver, SYS_TOKENS + INSTR["verify"] + 500 + win + G + img,
                _a("out_verify", case) + think,
                prefix=ver_P if pl_v else 0,
                units=prefix_units(ver, ("primed" if priming else "batch") if parallel else "seq5m", case)
                if pl_v else None)
    db.close()
    if not only:
        add("4 資料横断の統合", 2, 120000, _a("out_corpus", case) + think)
    total = 0.0
    for r in rows.values():
        r["yen"] = usd(r["in_eff"], r["out"], batch) * YEN_PER_USD
        total += r["yen"]
    return {"docs": docs, "rows": rows, "yen": total}


def semantic(db_path: Path, case: str = "mid", **kw) -> dict:
    opts = {"directive_prefix_cache": True, "prefix_layout": False, "compact": False, "batch": True, "verify_group": 1,
            "parallel": True, "priming": True, "only": None, "layout_scope": "both", "reinterpret_delta": False}
    opts.update(kw)
    return _semantic(db_path, case, **opts)


def relocate(rec: dict, base: Path) -> dict:
    """成果物を別の場所に展開した読み取り結果の画像のパスを、展開先（base/frames, base/pages）に直す（見積もり用）。"""
    def fix(p):
        if not p or Path(p).exists():
            return p
        for sub in ("frames", "pages"):
            q = base / sub / Path(p).name
            if q.exists():
                return str(q)
        return p
    for sc in rec.get("scenes", []):
        sc["frame"] = fix(sc.get("frame"))
        for e in sc.get("events_with_frames", []) or []:
            e["frame"] = fix(e.get("frame"))
        for k in ("frames", "window_frames"):
            if isinstance(sc.get(k), list):
                sc[k] = [[x[0], fix(x[1])] if isinstance(x, list) and len(x) == 2 else fix(x) if isinstance(x, str) else x
                         for x in sc[k]]
    for p in rec.get("pages", []):
        p["render"] = fix(p.get("render"))
    return rec


def report(reading_dirs: list[Path], db_path: Path | None, out: Path) -> dict:
    """工程ごと・最適化ごとの費用（low / mid / high）を書く。"""
    from .reading import ai_read
    recs = {}
    for d in reading_dirs:
        for rp in sorted(Path(d).rglob("reading.json")):
            r = json.loads(rp.read_text(encoding="utf-8"))
            if r.get("path") and not r.get("error"):
                recs.setdefault(r["path"], relocate(r, rp.parent))
    res: dict = {"assumptions": {k: v for k, v in ASSUME.items()}, "reading": {}, "semantic": {}, "savings": {}}
    plan = ai_read.estimate_plan(recs, batch=True, prefix_factor=lambda n: prefix_units(n, "primed", "mid") / max(n, 1))
    before = ai_read.estimate_plan(recs, batch=True)
    res["reading"] = {f["path"]: round(f["usd"] * YEN_PER_USD) for f in plan["files"]}
    res["reading_total_mid"] = round(plan["total"]["usd"] * YEN_PER_USD)
    res["reading_total_mid_before_M24"] = round(before["total"]["usd"] * YEN_PER_USD)
    if db_path and Path(db_path).exists():
        for c in CASES:
            s = semantic(db_path, c)
            res["semantic"][c] = {"total": round(s["yen"]), "rows": {k: {"calls": round(v["calls"]), "yen": round(v["yen"])}
                                                                     for k, v in s["rows"].items()}}
        mid = semantic(db_path, "mid")["yen"]
        res["semantic_before_M24"] = {c: round(semantic(db_path, c, parallel=False, priming=False)["yen"]) for c in CASES}
        res["savings"] = {
            "M24 指示の抽出・照合をまとめて送る＋キャッシュの先書き（採用・内容同じ）":
                round(semantic(db_path, "mid", parallel=False, priming=False)["yen"] - mid),
            "候補C1 資料全体の理解を先頭に置く（再解釈・照合。品質の比較が必要）":
                round(mid - semantic(db_path, "mid", prefix_layout=True)["yen"]),
            "候補C2 短い書き方（品質の比較が必要）": round(mid - semantic(db_path, "mid", compact=True)["yen"]),
            "候補C5 再解釈は変わった項目だけ返す（品質の比較が必要）":
                round(mid - semantic(db_path, "mid", reinterpret_delta=True)["yen"]),
            "候補C4 照合のまとめ（品質の比較が必要）": round(mid - semantic(db_path, "mid", verify_group=5)["yen"]),
            "Batch API（採用済み）": round(semantic(db_path, "mid", batch=False)["yen"] - mid),
        }
    out.mkdir(parents=True, exist_ok=True)
    (out / "costs.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return res
