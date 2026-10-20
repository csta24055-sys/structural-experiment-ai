"""将来の提出物（Excel・レポート）作成時のチェック。

このツールは提出物を作らない。利用者（または将来の作成工程）が用意した下書きを、元資料・計算結果・
指示と照合するだけ。自動で判定できないもの（考察の妥当性など）は「未検証」として一覧にし、成功扱いにしない。

  Excel: 数式の再計算、列見出しの単位、グラフの種類・近似曲線・軸タイトル・凡例と指示の照合
  文章:  数値が元資料・計算結果で確認できるか、単位の欠落、数式が資料に存在するか、
         必須の章（考察など）の有無、提出形式・ページ数などの提出条件、必須・禁止・条件付きの指示の確認リスト
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from ..classify import keyed_facts
from ..db import DB
from ..semantic.rules import DIRECTIVE_CLASSES, NEGATIVE
from .base import clear, record
from .extraction import load_workbook_cells, recalc_workbook
from .integration import numeric_facts

STAGE = "deliverable"
_NUM = re.compile(r"(?<![0-9A-Za-z.])([-+]?\d+(?:\.\d+)?)(?:\s*(N/mm2|N/mm²|mm2|mm²|mm|cm|kN|MPa|GPa|N|kg|με|%))?")
SECTIONS = ["目的", "方法", "結果", "考察", "結論", "参考文献", "表紙", "感想"]


def draft_text(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        import pymupdf
        with pymupdf.open(str(path)) as d:
            return "\n".join(p.get_text() for p in d)
    if suf == ".docx":
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        return "\n".join("".join(t.text or "" for t in p.iter(f"{w}t")) for p in root.iter(f"{w}p"))
    return path.read_text(encoding="utf-8", errors="replace")


def _effective_directives(db: DB) -> list:
    from ..semantic.corpus import best_docs
    ids = [d["id"] for d in best_docs(db)]
    if not ids:
        return []
    return db.q(f"SELECT * FROM directives WHERE doc_id IN ({','.join('?' * len(ids))}) AND effective IN "
                "('effective','unresolved_conflict')", ids)


def _source_numbers(db: DB) -> list[tuple[float, str, str]]:
    """元資料の数値（項目付きの数値・Excelの数値セル）。(値, 単位, 出典)"""
    import json
    out = []
    for f in db.q("SELECT * FROM files WHERE status IN ('done','partial')"):
        for u in db.q("SELECT extract_json FROM units WHERE file_id=? AND file_hash=? AND status='done'",
                      (f["id"], f["content_hash"] or "")):
            rec = json.loads(u["extract_json"] or "{}")
            for term, v, unit in numeric_facts(rec.get("text", "")):
                out.append((v, unit, f"{f['path']}（{term}）"))
            for ln in (rec.get("ai_text") or "").splitlines():
                m = re.match(r"^([A-Z]{1,3}\d+): (?:=.* → )?(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)$", ln)
                if m:
                    out.append((float(m.group(2)), "", f"{f['path']} {m.group(1)}"))
    return out


def _matches(v: float, decimals: int, pool: list[tuple[float, str, str]]) -> list[tuple[float, str, str]]:
    return [p for p in pool if round(p[0], decimals) == round(v, decimals) or abs(p[0] - v) < 1e-9]


def check_draft(db: DB, cfg, path: Path, *, fmt: str | None = None, pages: int | None = None,
                run_id: int | None = None) -> dict[str, int]:
    name = path.name
    clear(db, STAGE, check_type=None, target_type=f"draft:{name}")
    tt = f"draft:{name}"
    counts: dict[str, int] = {}

    def rec(check_type: str, tid: Any, status: str, detail: str, expected: Any = None, actual: Any = None) -> None:
        counts[status] = counts.get(status, 0) + 1
        record(db, stage=STAGE, check_type=check_type, target_type=tt, target_id=f"{name}:{tid}", status=status,
               expected=expected, actual=actual, detail=detail, run_id=run_id)

    dirs = _effective_directives(db)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        _excel(db, cfg, path, dirs, rec, run_id)
    else:
        text = unicodedata.normalize("NFKC", draft_text(path))
        _text(db, text, dirs, rec, fmt or path.suffix.lstrip(".").upper(), pages, path)
    # 必須・禁止・条件付きの指示の確認リスト（自動では判定しない）
    for d in dirs:
        if d["directive_class"] in ("mandatory", "prohibited", "conditional"):
            rec("directive_compliance", d["id"], "unverified",
                f"[{DIRECTIVE_CLASSES[d['directive_class']]}] {d['content']}"
                + (f"（条件: {d['condition']}）" if d["condition"] else "")
                + f" — {d['source_path']} — 下書きが守っているかは人が確認する")
    return counts


def _excel(db: DB, cfg, path: Path, dirs, rec, run_id) -> None:
    from ..processors.excel import workbook_structure
    recalc_workbook(db, cfg, {"id": -1, "path": path.name}, path, run_id, stage=STAGE,
                    target_type=f"draft:{path.name}", prefix=path.name)
    try:
        stc = workbook_structure(path)
    except Exception as e:  # noqa: BLE001
        rec("charts", "all", "failed", f"グラフ定義を読めない: {e}")
        return
    charts = stc["charts"]
    pos = [d for d in dirs if d["directive_class"] not in NEGATIVE]
    neg = [d for d in dirs if d["directive_class"] in NEGATIVE]

    def need(pattern: str, ds) -> list:
        return [d for d in ds if re.search(pattern, d["content"])]

    for d in need("散布図", pos):
        ok = any("散布図" in c["types"] for c in charts)
        rec("chart_type", d["id"], "ok" if ok else "mismatch", f"指示 #{d['id']}「{d['content']}」: 散布図 {'あり' if ok else 'なし'}")
    for d in need("近似(曲線|直線)", pos):
        ok = any(s.get("trendline") for c in charts for s in c["series"])
        rec("trendline", d["id"], "ok" if ok else "mismatch", f"指示 #{d['id']}「{d['content']}」: 近似曲線 {'あり' if ok else 'なし'}")
    for d in need("凡例", neg):
        ok = all(c.get("legend") for c in charts) if charts else False
        rec("legend", d["id"], "ok" if ok else "mismatch", f"指示 #{d['id']}「{d['content']}」: 凡例 {'あり' if ok else 'なし（または消されている）'}")
    for i, c in enumerate(charts):
        empty = [a["type"] for a in c["axes"] if not a.get("title")]
        rec("axis_titles", i, "needs_review" if empty or not c["axes"] else "ok",
            f"グラフ「{c['title'] or i}」: " + (f"軸タイトルなし {empty}" if empty else "軸タイトルあり"))
    formulas, values, _ = load_workbook_cells(path, int(cfg.data.get("checks", {}).get("max_excel_cells", 300000)))
    heads = {}
    for (sh, cell), v in values.items():
        m = re.match(r"([A-Z]+)1$", cell)
        if m and isinstance(v, str):
            heads[(sh, m.group(1))] = v
    numeric_cols = {(sh, re.match(r"([A-Z]+)", c).group(1)) for (sh, c), v in values.items()
                    if isinstance(v, (int, float)) and not re.match(r"[A-Z]+1$", c)}
    for sh, col in sorted(numeric_cols):
        h = heads.get((sh, col))
        if h is None:
            rec("units", f"{sh}!{col}", "needs_review", f"{sh}!{col} 列: 見出しがない（単位を確認できない）")
        elif not re.search(r"[（(][^()（）]+[)）]|\[[^\]]+\]", h):
            rec("units", f"{sh}!{col}", "needs_review", f"{sh}!{col} 列「{h}」: 見出しに単位がない")
        else:
            rec("units", f"{sh}!{col}", "ok", f"{sh}!{col} 列「{h}」")


def _text(db: DB, text: str, dirs, rec, fmt: str, pages: int | None, path: Path) -> None:
    pool = _source_numbers(db)
    with_unit = {}
    for v, unit, src in pool:
        if unit:
            with_unit.setdefault(v, (unit, src))
    seen = set()
    for m in _NUM.finditer(text):
        s, unit = m.group(1), m.group(2)
        if s in seen:
            continue
        seen.add(s)
        v = float(s)
        dec = len(s.split(".")[1]) if "." in s else 0
        hits = _matches(v, dec, pool)
        if hits:
            rec("numbers", s, "ok", f"{s}{unit or ''} は元資料・計算結果と一致: {hits[0][2]}")
            src_unit = next((h[1] for h in hits if h[1]), None)
            if src_unit and not unit:
                rec("units", s, "needs_review", f"{s} に単位がない（元資料では {src_unit}）")
        elif dec == 0 and abs(v) < 10:
            continue  # 章番号・個数などの小さな整数は照合しない
        else:
            rec("numbers", s, "unverified", f"{s}{unit or ''} を元資料・計算結果で確認できない（計算の再現手順の確認が必要）")
    for d in dirs:
        eff = d["base_strength"] if d["directive_class"] == "conditional" else d["directive_class"]
        if eff not in ("mandatory", "request"):
            continue  # 任意・推奨・禁止の指示からは「章が必要」とは判断しない
        for sec in SECTIONS:
            if sec in d["content"] and re.search(r"(書|つけ|付け|入れ|記載|まとめ)", d["content"]):
                ok = sec in text
                rec("sections", f"{d['id']}:{sec}", "ok" if ok else "mismatch",
                    f"指示 #{d['id']}「{d['content']}」: 下書きに「{sec}」{'あり' if ok else 'なし'}")
    # 提出条件（資料の提出形式・ページ数）
    src_facts: dict[str, set] = {}
    for d in dirs:
        for k, v in keyed_facts(d["content"]):
            src_facts.setdefault(k, set()).add(v)
    if pages is None and path.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(str(path)) as doc:
            pages = doc.page_count
    for k, vals in src_facts.items():
        if len(vals) > 1:
            rec("submission", k, "needs_review", f"{k}: 資料間で異なる指定 {sorted(vals)}（どれに従うか要確認）")
            continue
        want = next(iter(vals))
        if k == "提出形式":
            ok = fmt.upper() == want.upper()
            rec("submission", k, "ok" if ok else "mismatch", f"提出形式: 指定 {want} ／ 下書き {fmt}")
        elif k == "ページ数・枚数" and pages is not None:
            ok = pages <= int(want)
            rec("submission", k, "ok" if ok else "mismatch", f"ページ数: 指定 {want} 以内 ／ 下書き {pages}")
        else:
            rec("submission", k, "unverified", f"{k}: 指定 {want}（自動では確認できない）")
