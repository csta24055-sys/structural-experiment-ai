"""指示同士の関連付け（訂正・補足・例外・条件変更・繰り返し・矛盾）。資料内と資料横断の両方。

方針:
- 「訂正」で前の指示を無効（superseded）にするのは、訂正の言い回し（「訂正します」「さっき〜と言いましたが」等）が
  原文に明示されている場合だけ。AIの判断だけの訂正は候補（unresolved）として残し、前の指示は無効にしない。
- 向きが逆（してよい／いけない）で対象が似ている指示は、訂正として説明できない限り「未解決の矛盾」として記録し、
  どちらが正しいかは決めない。
"""
from __future__ import annotations

import json
from collections import defaultdict

from ..db import DB
from ..classify import keyed_facts
from .rules import NEGATIVE, POSITIVE, content_words, discourse_markers, similarity, target_similarity

REL_NAME = {"correction": "correction_of", "supplement": "supplement_to", "exception": "exception_to",
            "condition_change": "condition_change_of"}
REL_LABEL = {"correction": "訂正", "supplement": "補足", "exception": "例外", "condition_change": "条件変更"}
SIM_REL = 0.3
SIM_CONFLICT = 0.5
SIM_SAME = 0.6
SIM_SAME_CORPUS = 0.6
ACTIVE = ("effective", "unresolved_conflict", "superseded")


def _pol(d) -> str | None:
    c = d["base_strength"] if d["directive_class"] == "conditional" else d["directive_class"]
    if c in NEGATIVE:
        return "negative"
    if c in POSITIVE:
        return "positive"
    return None


def _order(d):
    return (d["t_start"] if d["t_start"] is not None else -1, d["page"] or 0, d["id"])


def _link(db: DB, a: int, b: int, rel: str, scope: str, basis: str, note: str, status: str = "recorded") -> None:
    db.x("INSERT OR IGNORE INTO directive_links(from_id,to_id,relation,scope,basis,note,status) VALUES(?,?,?,?,?,?,?)",
         (a, b, rel, scope, basis, note, status))


def _candidates(ds: list) -> dict[int, set[int]]:
    """内容語を共有する組だけを比較対象にする（全組み合わせ比較を避ける）。"""
    index: dict[str, list[int]] = defaultdict(list)
    tg = {}
    for i, d in enumerate(ds):
        tg[i] = content_words(d["content"])
        for g in tg[i]:
            index[g].append(i)
    pairs: dict[int, set[int]] = defaultdict(set)
    for i in range(len(ds)):
        cnt: dict[int, int] = defaultdict(int)
        for g in tg[i]:
            if len(index[g]) > 200:
                continue  # ありふれた2文字は手掛かりにしない
            for j in index[g]:
                if j > i:
                    cnt[j] += 1
        pairs[i] = {j for j, c in cnt.items() if c >= 1}
    return pairs


def value_conflict(a: str, b: str) -> str | None:
    """同じ項目（提出形式・期限・枚数・有効数字）で値が異なれば説明を返す。"""
    fa, fb = dict(keyed_facts(a)), dict(keyed_facts(b))
    for k in fa.keys() & fb.keys():
        if fa[k] != fb[k]:
            return f"{k}の値が異なる（{fa[k]} ⇔ {fb[k]}）"
    return None


def link_document(db: DB, doc_id: int) -> None:
    with db.tx():
        ids = [r["id"] for r in db.q("SELECT id FROM directives WHERE doc_id=?", (doc_id,))]
        if not ids:
            return
        qs = ",".join("?" * len(ids))
        db.x(f"DELETE FROM directive_links WHERE scope='document' AND relation!='corroborated_by' AND from_id IN ({qs})", ids)
        db.x("UPDATE directives SET effective='effective' WHERE doc_id=? AND effective IN ('superseded','unresolved_conflict')",
             (doc_id,))
        ds = sorted(db.q("SELECT * FROM directives WHERE doc_id=? AND effective='effective'", (doc_id,)), key=_order)
        superseded: set[int] = set()
        corr_pairs: set[tuple[int, int]] = set()
        for i, d in enumerate(ds):
            extra = json.loads(d["extra_json"] or "{}")
            text_for_markers = " ".join(x for x in (d["speech"], d["evidence"], d["content"]) if x)
            explicit = set(discourse_markers(text_for_markers)) | set(extra.get("explicit_discourse") or [])
            if d["extracted_by"].startswith("rule"):
                explicit |= set(json.loads(d["discourse"] or "[]"))  # 規則が原文から検出した標識
            claimed = set(json.loads(d["discourse"] or "[]")) | explicit
            unresolved_notes = []
            for rel in ("correction", "exception", "condition_change", "supplement"):
                if rel not in claimed:
                    continue
                refers = extra.get("relation_refers_to") or ""
                best, best_s = None, 0.0
                for e in ds[:i]:
                    s = target_similarity(d["content"], e["content"])
                    if refers:
                        s = max(s, similarity(refers, e["content"]))
                    if s > best_s:
                        best, best_s = e, s
                if best is None or best_s < SIM_REL:
                    prev = ds[i - 1] if i > 0 else None
                    near = prev is not None and (
                        (d["t_start"] is not None and prev["t_start"] is not None and d["t_start"] - prev["t_start"] <= 30)
                        or (d["t_start"] is None and d["page"] is not None and d["page"] == prev["page"]))
                    if rel != "correction" and near:
                        # 「ただし」「補足すると」は直前の説明に係ることが多い。推定として記録し、無効化などはしない
                        _link(db, d["id"], prev["id"], REL_NAME[rel], "document", "rule",
                              "対象語の一致がないため直前の指示に対するものと推定（要確認）", "unresolved")
                    else:
                        unresolved_notes.append(f"{REL_LABEL[rel]}の対象となる前の指示を特定できない")
                    continue
                is_explicit = rel in explicit
                status = "recorded"
                note = f"類似度 {best_s:.2f}"
                if rel == "correction":
                    corr_pairs.add((best["id"], d["id"]))
                    if is_explicit:
                        status = "resolved_explicit"
                        superseded.add(best["id"])
                        note += "／原文に訂正の言い回しあり"
                    else:
                        status = "unresolved"
                        note += "／訂正の言い回しが原文にないため、前の指示は無効にしていない（要確認）"
                _link(db, d["id"], best["id"], REL_NAME[rel], "document", "rule" if is_explicit else "ai", note, status)
            extra["relation_unresolved"] = unresolved_notes
            db.x("UPDATE directives SET extra_json=? WHERE id=?", (json.dumps(extra, ensure_ascii=False), d["id"]))
        for sid in superseded:
            db.x("UPDATE directives SET effective='superseded' WHERE id=?", (sid,))
        pairs = _candidates(ds)
        conflicted: set[int] = set()
        for i, js in pairs.items():
            a = ds[i]
            for j in js:
                b = ds[j]
                pa, pb = _pol(a), _pol(b)
                s = target_similarity(a["content"], b["content"])
                vc = value_conflict(a["content"], b["content"])
                if ((pa and pb and pa != pb and s >= SIM_CONFLICT) or vc) and s >= SIM_REL:
                    if (a["id"], b["id"]) in corr_pairs or (b["id"], a["id"]) in corr_pairs \
                            or a["id"] in superseded or b["id"] in superseded:
                        continue
                    note = vc or f"対象が似ていて向きが逆（類似度 {s:.2f}）"
                    if a["condition"] != b["condition"]:
                        note += f"／適用条件が異なる（{a['condition'] or '条件なし'} ⇔ {b['condition'] or '条件なし'}）"
                    _link(db, b["id"], a["id"], "conflicts_with", "document", "rule", note, "unresolved")
                    conflicted |= {a["id"], b["id"]}
                elif pa and pa == pb and s >= SIM_SAME and _order(a)[:2] != _order(b)[:2] \
                        and (a["id"], b["id"]) not in corr_pairs and (b["id"], a["id"]) not in corr_pairs:
                    _link(db, b["id"], a["id"], "same_as", "document", "rule", f"同じ指示の繰り返し（類似度 {s:.2f}）")
        for cid in conflicted:
            db.x("UPDATE directives SET effective='unresolved_conflict' WHERE id=? AND effective='effective'", (cid,))


def link_corpus(db: DB, doc_ids: list[int]) -> None:
    """資料横断: 同じ指示の関連付けと、資料間で向きが逆の指示（矛盾候補）の記録。"""
    with db.tx():
        db.x("DELETE FROM directive_links WHERE scope='corpus' AND basis='rule'")
        if not doc_ids:
            return
        qs = ",".join("?" * len(doc_ids))
        ds = sorted(db.q(f"SELECT * FROM directives WHERE doc_id IN ({qs}) AND effective IN ('effective',"
                         f"'unresolved_conflict')", doc_ids), key=lambda d: (d["source_path"], _order(d)))
        for i, js in _candidates(ds).items():
            a = ds[i]
            for j in js:
                b = ds[j]
                if a["doc_id"] == b["doc_id"]:
                    continue
                pa, pb = _pol(a), _pol(b)
                s = target_similarity(a["content"], b["content"])
                vc = value_conflict(a["content"], b["content"])
                if ((pa and pb and pa != pb and s >= SIM_CONFLICT) or vc) and s >= SIM_REL:
                    _link(db, b["id"], a["id"], "conflicts_with", "corpus", "rule",
                          (vc or f"資料間で向きが逆（類似度 {s:.2f}）") + "。どちらが有効かは判断していない", "unresolved")
                elif (pa == pb) and s >= SIM_SAME_CORPUS:
                    _link(db, b["id"], a["id"], "same_as", "corpus", "rule", f"複数資料に共通する指示（類似度 {s:.2f}）")
