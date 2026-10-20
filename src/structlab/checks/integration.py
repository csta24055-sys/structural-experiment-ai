"""複数資料の統合時のチェック: 動画・PDF・Excel の間で、数値・提出条件・指示・作業手順の一致と矛盾。

- 数値: 「断面積は78.5 mm2」のような 項目＋数値＋単位 を資料ごとに抜き出し、同じ項目・単位で値が違えば不一致
- Excel: 見出し行（例:「断面積(mm2)」）の下に数値が1つだけある列を定数として、資料の数値と照合
- 提出条件: 提出形式・期限・枚数・有効数字（既存の keyed_facts を利用）
- 指示: 資料間で向きが逆の指示（既存の関連付けの結果を集計）
- 作業手順: 統合理解の手順が実在する資料を根拠にしているか
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict

from ..classify import keyed_facts
from ..db import DB
from .base import clear, record
from .formula import close_enough

STAGE = "integration"
UNIT_NORM = {"mm²": "mm2", "mm^2": "mm2", "N/mm²": "N/mm2", "N/mm^2": "N/mm2", "㎜": "mm", "㎝": "cm", "％": "%"}
_UNITS = r"(?:N/mm2|N/mm²|N/mm\^2|mm2|mm²|mm\^2|mm|cm|kN|MPa|GPa|N|kg|με|%|％|秒|分)"
_FACT = re.compile(r"(?P<term>[一-龥ァ-ヴーA-Za-z]{2,12}?)(?:は|が|：|:|=|＝)\s*(?P<num>[-+]?\d+(?:\.\d+)?)\s*(?P<unit>" + _UNITS + r")")
_HEAD = re.compile(r"^(?P<term>[^()（）]+?)\s*[（(](?P<unit>[^()（）]+)[)）]\s*$")


def _unit(u: str) -> str:
    u = unicodedata.normalize("NFKC", u or "")
    return UNIT_NORM.get(u, u)


def numeric_facts(text: str) -> list[tuple[str, float, str]]:
    out = []
    for m in _FACT.finditer(unicodedata.normalize("NFKC", text or "")):
        out.append((m.group("term"), float(m.group("num")), _unit(m.group("unit"))))
    return out


def excel_constants(db: DB, fid: int, fhash: str) -> list[tuple[str, float, str, str]]:
    """見出し「項目(単位)」の列に数値が1つだけある場合、その数値を定数とみなす。(項目, 値, 単位, セル)"""
    out = []
    for u in db.q("SELECT extract_json FROM units WHERE file_id=? AND file_hash=? AND unit_type='excel_sheet' AND status='done'",
                  (fid, fhash)):
        data = (json.loads(u["extract_json"] or "{}").get("data") or {})
        sheet = data.get("sheet")
        rec = json.loads(u["extract_json"] or "{}")
        cells = {}
        for ln in (rec.get("ai_text") or "").splitlines():
            m = re.match(r"^([A-Z]{1,3})(\d+): (.*)$", ln)
            if m:
                cells[(m.group(1), int(m.group(2)))] = m.group(3)
        cols = defaultdict(list)
        for (c, r), v in cells.items():
            cols[c].append((r, v))
        for c, vs in cols.items():
            vs.sort()
            head = vs[0][1].strip("'\"") if vs and vs[0][0] == 1 else None
            if not head:
                continue
            m = _HEAD.match(unicodedata.normalize("NFKC", head))
            nums = []
            for r, v in vs[1:]:
                v = v.split(" → ")[-1]
                try:
                    nums.append((r, float(v)))
                except ValueError:
                    pass
            if m and len(nums) == 1:
                out.append((m.group("term").strip(), nums[0][1], _unit(m.group("unit")), f"{sheet}!{c}{nums[0][0]}"))
    return out


def check_corpus(db: DB, run_id: int | None = None) -> None:
    from ..semantic.corpus import best_docs
    clear(db, STAGE)
    docs = best_docs(db)
    files = db.q("SELECT * FROM files WHERE status IN ('done','partial')")
    facts: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    keyed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in files:
        texts = [json.loads(u["extract_json"] or "{}").get("text", "") for u in db.q(
            "SELECT extract_json FROM units WHERE file_id=? AND file_hash=? AND status='done'", (f["id"], f["content_hash"] or ""))]
        seen = set()
        for t in texts:
            for term, v, unit in numeric_facts(t):
                if (term, unit, v) not in seen:
                    seen.add((term, unit, v))
                    facts[(term, unit)].append((v, f["path"]))
            for k, v in keyed_facts(t):
                if (k, v, f["path"]) not in seen:
                    seen.add((k, v, f["path"]))
                    keyed[k].append((v, f["path"]))
        if f["kind"] == "excel":
            for term, v, unit, cell in excel_constants(db, f["id"], f["content_hash"] or ""):
                facts[(term, unit)].append((v, f"{f['path']} {cell}"))
    with db.tx():
        for (term, unit), vals in sorted(facts.items()):
            srcs = {s.split(" ")[0] for _, s in vals}
            if len(srcs) < 2:
                continue  # 1つの資料にしか出てこない数値は照合できない
            distinct = []
            for v, _ in vals:
                if not any(close_enough(v, d, rel=1e-6, abs_tol=1e-9) for d in distinct):
                    distinct.append(v)
            record(db, stage=STAGE, check_type="numeric", target_type="fact", target_id=f"{term}[{unit}]",
                   status="ok" if len(distinct) == 1 else "mismatch",
                   expected=None, actual=[{"value": v, "source": s} for v, s in vals],
                   detail=f"{term}（{unit}）: " + " / ".join(f"{v:g} ({s})" for v, s in vals[:10]), run_id=run_id)
        for k, vals in sorted(keyed.items()):
            srcs = {s for _, s in vals}
            if len(srcs) < 2:
                continue
            distinct = sorted({v for v, _ in vals})
            record(db, stage=STAGE, check_type="submission", target_type="fact", target_id=k,
                   status="ok" if len(distinct) == 1 else "mismatch", actual=[{"value": v, "source": s} for v, s in vals],
                   detail=f"{k}: " + " / ".join(f"{v} ({s})" for v, s in vals[:10]), run_id=run_id)
        conf = db.q("SELECT * FROM directive_links WHERE scope='corpus' AND relation='conflicts_with'")
        pairs = {frozenset((l["from_id"], l["to_id"])) for l in conf}
        record(db, stage=STAGE, check_type="directive_conflicts", target_type="corpus", target_id="all",
               status="mismatch" if pairs else ("ok" if len(docs) >= 2 else "unverified"),
               detail=(f"資料間で矛盾する指示 {len(pairs)} 組（conflicts.md 参照。どちらが有効かは判断していない）" if pairs else
                       "資料間で矛盾する指示は検出されなかった" if len(docs) >= 2 else "資料が1つのため照合対象なし"), run_id=run_id)
        same = db.q("SELECT * FROM directive_links WHERE scope='corpus' AND relation='same_as'")
        record(db, stage=STAGE, check_type="common_directives", target_type="corpus", target_id="all",
               status="ok" if len(docs) >= 2 else "unverified", run_id=run_id,
               detail=(f"複数資料に共通する指示の関連付け {len(same)} 組" if len(docs) >= 2 else "資料が1つのため照合対象なし"))
        from ..semantic.engine import ELIGIBLE_KINDS
        elig = [f for f in files if f["kind"] in ELIGIBLE_KINDS]
        modes = {d["file_id"]: d["mode"] for d in docs}
        missing = [f["path"] for f in elig if f["id"] not in modes]
        local_only = [f["path"] for f in elig if modes.get(f["id"]) == "local"]
        others = db.q("SELECT path, status FROM files WHERE status IN ('discovered','ready','failed','unsupported','skipped') "
                      "AND parent_id IS NULL")
        st = "needs_review" if missing or others else ("unverified" if local_only else "ok")
        record(db, stage=STAGE, check_type="corpus_completeness", target_type="corpus", target_id="all", status=st,
               run_id=run_id, actual={"included": len(docs), "missing": missing, "local_only": local_only,
                                      "not_processed": [dict(r) for r in others]},
               detail=f"統合に含めた資料 {len(docs)}／対象 {len(elig)}"
                      + (f"；指示抽出が未実施 {len(missing)}" if missing else "")
                      + (f"；未解析・失敗・未対応のファイル {len(others)}" if others else "")
                      + (f"；AIによる意味理解なし（規則のみ） {len(local_only)}" if local_only else ""))
        cr = db.q1("SELECT * FROM sem_understanding WHERE scope='corpus' ORDER BY id DESC LIMIT 1")
        if cr is None:
            record(db, stage=STAGE, check_type="procedure", target_type="corpus", target_id="all", status="unverified",
                   detail="資料横断の統合理解（AI）が未生成のため、作業手順の照合は未実施", run_id=run_id)
        else:
            data = json.loads(cr["data_json"])
            names = {d["path"] for d in docs}
            unknown, nosrc = [], []
            steps = {x.get("step") for x in data.get("procedure") or []}
            baddep = [f"{x.get('step')}→{d}" for x in data.get("procedure") or [] for d in x.get("depends_on") or [] if d not in steps]
            if baddep:
                unknown.append("存在しない手順への依存: " + ", ".join(baddep[:5]))
            for x in data.get("procedure") or []:
                srcs = x.get("sources") or []
                if not srcs:
                    nosrc.append(x.get("step"))
                unknown += [s for s in srcs if s not in names]
            st = "mismatch" if unknown or data.get("_dropped_invalid_ids") else "needs_review" if nosrc else "ok"
            record(db, stage=STAGE, check_type="procedure", target_type="corpus", target_id="all", status=st,
                   detail=("存在しない資料を根拠にした手順: " + "、".join(sorted(set(unknown))[:10]) + "；" if unknown else "")
                          + (f"存在しない指示番号の参照を除外: {data['_dropped_invalid_ids']}；" if data.get("_dropped_invalid_ids") else "")
                          + (f"根拠資料のない手順: {nosrc}" if nosrc else "")
                          or "手順の根拠資料はすべて実在", run_id=run_id)
