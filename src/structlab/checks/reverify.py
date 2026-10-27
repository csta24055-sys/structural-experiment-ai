"""独立した再検証（structlab reverify）。

各工程内のチェックとは別に、すでに出ている結論（指示とその分類・条件）を正解として扱わず、
元の発話・画面・資料の該当範囲だけから指示を取り出し直して、機械的に突き合わせる。

  1. 規則による再抽出（常に・無料）: 元の範囲の文から言い回しで指示を拾い直す
  2. AIによる再抽出（--allow-external-ai のときだけ・有料）: 最初の解釈・全体理解・対象の指示を一切見せず、
     元の範囲だけを渡して指示を列挙させる（盲検）
  3. 突き合わせ: 対象の近い指示が見つかるか、分類・条件が一致するか
     食い違いは revisions に「修正案（未適用）」として前後と理由を残す。自動で書き換えない。
     どちらの再抽出でも確認できないものは「未検証」とし、検証済みにしない。

優先順位: 要確認・不一致・見落とし候補 → 必須・禁止・条件付き（重要） → 確実性が低いもの。
同じ入力で再検証済みのものは繰り返さない（既存の照合との無意味な重複を避ける）。
"""
from __future__ import annotations

import json
import time

from ..ai import AIError, AIImage, AIRequest
from ..db import DB
from ..models import fmt_time
from ..semantic.material import build_scenes, lines_text, window_lines
from ..semantic.prompts import COMMON_SYSTEM, STRENGTH_ENUM, _obj, _N, _S, _SN, CLASS_ENUM
from ..semantic.report import OPEN_VER
from ..semantic.rules import DIRECTIVE_CLASSES, directives_in, polarity, target_similarity
from .base import existing_hash, input_hash, record

STAGE = "reverify"
BLIND_SCHEMA = _obj({"directives": {"type": "array", "items": _obj({
    "content": _S, "directive_class": {"type": "string", "enum": CLASS_ENUM}, "base_strength": STRENGTH_ENUM,
    "condition": _SN, "quote": _S, "t_start": _N})}})
BLIND_INSTRUCTION = f"""## 作業（独立した再検証: 指示の列挙）
次の元の発話・画面・資料だけを読み、ここに含まれる指示（禁止・非推奨・必須・要望・推奨・任意・条件付き・その他）を
1件ずつすべて列挙してください。他の解析結果は与えていません。原文に無いことを推測で加えないでください。
quote には根拠の原文をそのまま引用してください。分類:
{chr(10).join(f"- {k}: {v}" for k, v in DIRECTIVE_CLASSES.items())}
"""
SIM_MATCH = 0.4


def _eff(cls: str | None, base: str | None) -> str | None:
    return base if cls == "conditional" else cls


def _compare(target, cand_cls: str | None, cand_base: str | None, cand_cond: str | None) -> tuple[str, str]:
    t_eff, c_eff = _eff(target["directive_class"], target["base_strength"]), _eff(cand_cls, cand_base)
    if cand_cls == target["directive_class"] and (cand_base or None) == (target["base_strength"] or None):
        if bool(cand_cond) != bool(target["condition"]):
            return "needs_review", "分類は一致、条件の有無が異なる"
        return "ok", "分類・条件が一致"
    if polarity(t_eff) and polarity(c_eff) and polarity(t_eff) != polarity(c_eff):
        return "mismatch", f"向きが逆（{DIRECTIVE_CLASSES[target['directive_class']]} ⇔ {DIRECTIVE_CLASSES.get(cand_cls, cand_cls)}）"
    return "needs_review", f"分類が異なる（{DIRECTIVE_CLASSES[target['directive_class']]} ⇔ {DIRECTIVE_CLASSES.get(cand_cls, cand_cls)}）"


def targets(db: DB, limit: int) -> list:
    from ..semantic.corpus import best_docs
    ids = [d["id"] for d in best_docs(db)]
    if not ids:
        return []
    ds = db.q(f"SELECT * FROM directives WHERE doc_id IN ({','.join('?' * len(ids))}) AND effective IN "
              "('effective','unresolved_conflict')", ids)
    flagged = {r["target_id"] for r in db.q("SELECT target_id FROM checks WHERE target_type='directive' AND "
                                            "status IN ('mismatch','needs_review') AND stage!='reverify'")}

    def prio(d) -> int:
        if d["verification"] in OPEN_VER or str(d["id"]) in flagged:
            return 0
        if d["directive_class"] in ("mandatory", "prohibited", "conditional"):
            return 1
        if d["certainty"] == "low" or d["directive_class"] == "other":
            return 2
        return 3
    ds = sorted(ds, key=lambda d: (prio(d), d["source_path"], d["t_start"] if d["t_start"] is not None else -1, d["id"]))
    return [d for d in ds if prio(d) < 3][:limit]


def reverify(db: DB, cfg, *, provider=None, budget=None, run_id: int | None = None, limit: int | None = None,
             expand: float = 30.0) -> dict[str, int]:
    limit = limit or int(cfg.data.get("checks", {}).get("reverify_limit", 30))
    max_img = int(cfg.data["ai"]["max_images_per_call"])
    counts: dict[str, int] = {}
    scene_cache: dict[int, list] = {}
    for d in targets(db, limit):
        frow = db.q1("SELECT * FROM files WHERE id=?", (d["file_id"],))
        if d["file_id"] not in scene_cache:
            scene_cache[d["file_id"]] = build_scenes(db, frow)
        scenes = scene_cache[d["file_id"]]
        win = window_lines(scenes, d["t_start"], d["t_end"], d["page"], expand)
        if not win:
            win = next((s.lines for s in scenes if s.unit_id == d["unit_id"]), [])
        wtext = lines_text(win)
        h = input_hash(d["content"], d["directive_class"], d["base_strength"], d["condition"], wtext,
                       provider.name if provider else "rules")
        if existing_hash(db, STAGE, "directive", "directive", d["id"]) == h:
            counts["skipped_same_input"] = counts.get("skipped_same_input", 0) + 1
            continue
        results = {}
        # 1. 規則による再抽出（AIの出力を使わない）
        cands = [(rd, ln) for ln in win for rd in directives_in(ln.text)]
        best = max(cands, key=lambda c: target_similarity(d["content"], c[0].content), default=None)
        if best and target_similarity(d["content"], best[0].content) >= SIM_MATCH:
            st, why = _compare(d, best[0].directive_class, best[0].base_strength, best[0].condition)
            results["rules"] = {"status": st, "why": why, "found": best[0].content,
                                "class": best[0].directive_class, "base": best[0].base_strength}
        else:
            results["rules"] = {"status": "unverified", "why": "言い回しの規則では同じ指示を拾えない（規則の範囲外）"}
        # 2. AIによる盲検の再抽出（許可時のみ）
        if provider is not None:
            results["blind_ai"] = _blind(db, provider, budget, run_id, frow, d, win, wtext, max_img)
        else:
            results["blind_ai"] = {"status": "unverified", "why": "AI未使用（--allow-external-ai なし）"}
        sts = [r["status"] for r in results.values()]
        final = ("mismatch" if "mismatch" in sts else "needs_review" if "needs_review" in sts
                 else "ok" if "ok" in sts else "unverified")
        where = fmt_time(d["t_start"]) if d["t_start"] is not None else (f"p.{d['page']}" if d["page"] else "")
        record(db, stage=STAGE, check_type="directive", target_type="directive", target_id=d["id"], status=final,
               expected={"class": d["directive_class"], "base": d["base_strength"], "condition": d["condition"]},
               actual=results, detail=f"#{d['id']} {d['content'][:60]}（{d['source_path']} {where}）: "
               + "／".join(f"{k}: {v['why']}" for k, v in results.items()),
               file_id=d["file_id"], unit_id=d["unit_id"], run_id=run_id, ihash=h)
        for src, r in results.items():
            if r["status"] in ("mismatch", "needs_review") and r.get("class") and r["class"] != d["directive_class"]:
                db.x("INSERT INTO revisions(target_type,target_id,field,before,after,reason,source,status,created_at) "
                     "VALUES('directive',?,?,?,?,?,?,'proposed',?)",
                     (str(d["id"]), "directive_class", d["directive_class"], r["class"],
                      f"独立した再検証（{src}）: {r['why']}／根拠「{r.get('found', '')[:120]}」", f"reverify:{src}", time.time()))
        counts[final] = counts.get(final, 0) + 1
    return counts


def _blind(db: DB, provider, budget, run_id, frow, d, win, wtext: str, max_img: int) -> dict:
    if budget is not None:
        why = budget.check()
        if why:
            return {"status": "unverified", "why": f"AI使用量の上限: {why}"}
    imgs = []
    for ln in win:
        if ln.frame and len(imgs) < max_img and all(i.path.name != ln.frame.split("/")[-1] for i in imgs):
            from pathlib import Path
            if Path(ln.frame).exists():
                imgs.append(AIImage(Path(ln.frame), f"{fmt_time(ln.t_start)} の画面"))
    req = AIRequest(task="blind_directives", source_label=f"{frow['path']} 再検証", system=COMMON_SYSTEM,
                    schema=BLIND_SCHEMA, images=imgs, t_start=d["t_start"], t_end=d["t_end"],
                    text=f"{BLIND_INSTRUCTION}\n## 元の発話・画面・資料\n{wtext or '（なし）'}\n")
    try:
        resp = provider.analyze(req)
    except AIError as e:
        db.x("INSERT INTO api_usage(run_id,unit_id,provider,model,input_tokens,output_tokens,images,ok,created_at)"
             " VALUES(?,?,?,?,0,0,?,0,?)", (run_id, d["unit_id"], provider.name, provider.model, len(imgs), time.time()))
        return {"status": "unverified", "why": f"AI呼び出しに失敗: {e}"}
    db.x("INSERT INTO api_usage(run_id,unit_id,provider,model,input_tokens,output_tokens,images,ok,created_at)"
         " VALUES(?,?,?,?,?,?,?,1,?)", (run_id, d["unit_id"], provider.name, resp.model, resp.input_tokens,
                                         resp.output_tokens, len(imgs), time.time()))
    items = [x for x in (resp.data or {}).get("directives") or [] if x.get("directive_class") in DIRECTIVE_CLASSES]
    best = max(items, key=lambda x: max(target_similarity(d["content"], x.get("content", "")),
                                        target_similarity(d["content"], x.get("quote", ""))), default=None)
    if best is None or max(target_similarity(d["content"], best.get("content", "")),
                           target_similarity(d["content"], best.get("quote", ""))) < SIM_MATCH:
        return {"status": "needs_review", "why": "元の範囲だけから取り出し直すと、同じ指示が見つからない",
                "listed": [x.get("content") for x in items][:10]}
    st, why = _compare(d, best["directive_class"], best.get("base_strength"), best.get("condition"))
    return {"status": st, "why": why, "found": best.get("quote") or best.get("content"),
            "class": best["directive_class"], "base": best.get("base_strength"), "model": resp.model}


def summary_json(db: DB) -> list[dict]:
    return [dict(r) | {"actual": json.loads(r["actual"] or "null")} for r in
            db.q("SELECT * FROM checks WHERE stage=? ORDER BY status, id", (STAGE,))]
