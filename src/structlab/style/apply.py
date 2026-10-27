"""最終工程: 確定した文章の表現だけを本人の文章の特徴に寄せる。

前提: レポートの内容・構成・計算・考察が確定していること。この工程では内容を変えない。
- 適用前の文章を必ず style_runs に保存する
- 適用後に「内容が変わっていないか」を機械的に再検証する
    不一致（数値・数式・引用の変化）→ 採用しない（元の文章を最終版にする）
    要確認（否定・断定の強さ・結論文・新しい語・文の数の大きな変化）→ 既定では採用しない（style.accept_needs_review）
- スタイルが無効、またはPDF（プロファイル）が無ければ、元の文章をそのまま返す（処理は止めない）
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

from ..ai import AIError, AIRequest
from ..db import DB
from .profile import load_profile, select_rules

STYLE_SYSTEM = """あなたは、すでに内容・構成・計算・考察が確定した実験レポートの文章について、表現だけを書き手本人の文章の特徴に寄せる編集者です。
絶対に守ること:
- 数値・単位・数式・計算結果・実験結果・根拠・引用（「」内）・教授の指示を一字一句変えない。
- 文章の論理構造（主張と理由の対応・順序）と結論を変えない。主張の強さ（断定・推測・可能性）を変えない。否定を肯定にしない。
- 書き手が経験していないこと、考えていないこと、感想、成果、原体験を書き足さない。
- 示された特徴を機械的にすべて当てはめない。学術的な正確性と提出条件を優先し、合わない特徴は使わない。
- 迷ったら元の表現を残す。
- 出力は日本語。
"""
SCHEMA = {"type": "object", "properties": {
    "rewritten": {"type": "string"},
    "changes": {"type": "array", "items": {"type": "object", "properties": {
        "before": {"type": "string"}, "after": {"type": "string"}, "rule_id": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["before", "after", "rule_id", "reason"], "additionalProperties": False}},
    "not_applied": {"type": "array", "items": {"type": "string"}}},
    "required": ["rewritten", "changes", "not_applied"], "additionalProperties": False}

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*(?:N/mm2|mm2|mm|cm|kN|MPa|GPa|N|kg|με|%|℃|°)?")
_FORMULA = re.compile(r"[A-Za-zσεΔπ][A-Za-z0-9_σεΔπ]*\s*=\s*[^。、\n]+|=[A-Z]+\([^)]*\)")
_QUOTE = re.compile(r"「([^」]+)」")
_NEG = re.compile(r"ない|ません|ず[、。]|ぬ[、。]|ではな")
_HEDGE = re.compile(r"と考えられる|と考える|可能性|かもしれない|と思われる|と推測|であろう|らしい")
_ASSERT = re.compile(r"である[。]|明らかに|必ず|確実に|間違いなく")
_WORD = re.compile(r"[一-龥々]{2,}|[ァ-ヴー]{3,}|[A-Za-z]{3,}")
_SENT = re.compile(r"(?<=[。．！？!?])")


def _n(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _sentences(s: str) -> list[str]:
    return [x.strip() for x in _SENT.split(_n(s)) if x.strip()]


def verify_preservation(original: str, styled: str) -> dict:
    """適用前後で内容が変わっていないかの機械的な検査。"""
    o, s = _n(original), _n(styled)
    res: dict = {"mismatch": [], "needs_review": [], "ok": []}

    def cmp(label: str, a: Counter, b: Counter, level: str) -> None:
        if a == b:
            res["ok"].append(label)
        else:
            res[level].append(f"{label}が変わった（消えた: {list((a - b).elements())[:8]} / 増えた: {list((b - a).elements())[:8]}）")

    cmp("数値・単位", Counter(x.replace(" ", "") for x in _NUM.findall(o)), Counter(x.replace(" ", "") for x in _NUM.findall(s)), "mismatch")
    cmp("数式", Counter(x.replace(" ", "") for x in _FORMULA.findall(o)), Counter(x.replace(" ", "") for x in _FORMULA.findall(s)), "mismatch")
    cmp("引用（「」内）", Counter(_QUOTE.findall(o)), Counter(_QUOTE.findall(s)), "mismatch")
    if len(_NEG.findall(o)) != len(_NEG.findall(s)):
        res["needs_review"].append(f"否定表現の数が変わった（{len(_NEG.findall(o))}→{len(_NEG.findall(s))}。意味が反転した可能性）")
    else:
        res["ok"].append("否定表現の数")
    if (len(_HEDGE.findall(o)), len(_ASSERT.findall(o))) != (len(_HEDGE.findall(s)), len(_ASSERT.findall(s))):
        res["needs_review"].append("断定・推測の表現の数が変わった（主張の強さが変わった可能性）")
    else:
        res["ok"].append("断定・推測の強さ")
    wo, ws = set(_WORD.findall(o)), set(_WORD.findall(s))
    lost = wo - ws
    new = ws - wo
    if wo and len(lost) / len(wo) > 0.1:
        res["needs_review"].append(f"元の語の {len(lost) / len(wo):.0%} が消えた: {sorted(lost)[:10]}")
    if ws and len(new) / len(ws) > 0.15:
        res["needs_review"].append(f"元に無い語が {len(new) / len(ws):.0%}（書き足し・創作の可能性）: {sorted(new)[:10]}")
    if not (wo and len(lost) / len(wo) > 0.1) and not (ws and len(new) / len(ws) > 0.15):
        res["ok"].append("専門用語・内容語")
    so, ss = _sentences(o), _sentences(s)
    if so and ss:
        lo, ls = set(_WORD.findall(so[-1])), set(_WORD.findall(ss[-1]))
        if lo and len(lo & ls) / len(lo) < 0.5:
            res["needs_review"].append(f"結論（最後の文）が変わった可能性: 「{so[-1][:40]}」→「{ss[-1][:40]}」")
        else:
            res["ok"].append("結論の文")
        if not (0.6 <= len(ss) / len(so) <= 1.6):
            res["needs_review"].append(f"文の数が大きく変わった（{len(so)}→{len(ss)}）")
    res["status"] = "mismatch" if res["mismatch"] else "needs_review" if res["needs_review"] else "ok"
    return res


def split_sections(md: str) -> list[dict]:
    """Markdown の見出しごとに分ける。見出し・表・数式ブロックは文章調整の対象にしない。"""
    out, cur = [], {"heading": "", "lines": []}
    for ln in md.splitlines():
        if ln.startswith("#"):
            if cur["lines"] or cur["heading"]:
                out.append(cur)
            cur = {"heading": ln, "lines": []}
        else:
            cur["lines"].append(ln)
    out.append(cur)
    res = []
    for c in out:
        body = "\n".join(c["lines"]).strip("\n")
        kind = "discussion" if re.search("考察|結論|まとめ", c["heading"]) else "explanation"
        locked = bool(re.search(r"^\s*\|", body, re.M)) or "$$" in body or "```" in body
        res.append({"heading": c["heading"], "text": body, "kind": kind, "locked": locked})
    return res


def apply_style(db: DB, cfg, text: str, *, provider=None, section: str = "", kind: str = "explanation") -> dict:
    st = cfg.data.get("style", {})
    ihash = hashlib.sha256(text.encode()).hexdigest()[:16]

    def save(status: str, styled: str | None, final: str, rules=None, ver=None, analyzer="", phash="") -> dict:
        cur = db.x("INSERT INTO style_runs(created_at,profile_hash,input_hash,section,original,styled,final,status,rules_json,"
                   "verification_json,analyzer) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (time.time(), phash, ihash, section, text, styled, final, status, json.dumps(rules or [], ensure_ascii=False),
                    json.dumps(ver or {}, ensure_ascii=False), analyzer))
        from ..checks.base import record
        v = ver or {}
        cst = {"applied": "ok", "kept_original": v.get("status", "mismatch"), "skipped": "unverified", "failed": "failed"}[status]
        record(db, stage="style", check_type="preservation", target_type="style_run", target_id=int(cur.lastrowid), status=cst,
               detail=f"{section or '文章'}: " + {"applied": "内容の保存を確認して適用",
                                                  "kept_original": "内容が変わった疑いがあるため元の文章を採用",
                                                  "skipped": "未適用（" + str(v.get("note", "")) + "）",
                                                  "failed": "適用に失敗したため元の文章を採用"}[status]
               + ("：" + "；".join(v.get("mismatch", []) + v.get("needs_review", [])) if status == "kept_original" else ""))
        return {"id": int(cur.lastrowid), "status": status, "final": final, "styled": styled, "verification": ver or {}}

    if not st.get("enabled"):
        return save("skipped", None, text, ver={"note": "文章スタイルの適用は無効（style.enabled: false）"})
    prof = load_profile(cfg.work_dir / "style" / "profile.json")
    if prof is None:
        return save("skipped", None, text, ver={"note": "文章スタイルのPDFが未提供（structlab style profile で読み込む）"})
    if not text.strip():
        return save("skipped", None, text, ver={"note": "空の文章"}, phash=prof["source_sha256"][:16])
    if provider is None:
        return save("skipped", None, text, ver={"note": "AI未使用（--allow-external-ai なし）。元の文章のまま"},
                    phash=prof["source_sha256"][:16])
    rules = select_rules(prof, int(st.get("max_rules", 120)), kind)
    rule_txt = "\n".join(f"- [{r['id']}] {r['text']}" for r in rules)
    req = AIRequest(task="style_rewrite", source_label=section or "文章", system=STYLE_SYSTEM, schema=SCHEMA,
                    text=(f"## 書き手の文章の特徴（参考。合わないものは使わない）\n{rule_txt}\n\n"
                          f"## 種類\n{'考察・結論' if kind == 'discussion' else '説明文'}\n\n"
                          f"## 調整する文章（内容は確定済み・変更禁止）\n{text}\n"))
    try:
        resp = provider.analyze(req)
    except AIError as e:
        return save("failed", None, text, rules=[r["id"] for r in rules], ver={"note": f"AI呼び出しに失敗: {e}"},
                    phash=prof["source_sha256"][:16])
    styled = str((resp.data or {}).get("rewritten") or "")
    ver = verify_preservation(text, styled)
    ver["changes"] = (resp.data or {}).get("changes", [])
    accept = ver["status"] == "ok" or (ver["status"] == "needs_review" and st.get("accept_needs_review"))
    return save("applied" if accept else "kept_original", styled, styled if accept else text,
                rules=[r["id"] for r in rules], ver=ver, analyzer=f"{provider.name}:{resp.model}", phash=prof["source_sha256"][:16])


def apply_to_markdown(db: DB, cfg, md: str, *, provider=None) -> tuple[str, list[dict]]:
    out, runs = [], []
    for sec in split_sections(md):
        if sec["heading"]:
            out.append(sec["heading"])
        if sec["locked"] or not sec["text"].strip():
            out.append(sec["text"])
            runs.append({"section": sec["heading"], "status": "locked" if sec["locked"] else "empty"})
            continue
        r = apply_style(db, cfg, sec["text"], provider=provider, section=sec["heading"], kind=sec["kind"])
        out.append(r["final"])
        runs.append({"section": sec["heading"], **{k: r[k] for k in ("id", "status", "verification")}})
    return "\n".join(out) + "\n", runs


def ensure_profile(cfg, db: DB | None = None) -> dict | None:
    """設定の PDF からプロファイルを作る（PDFが無ければ None。処理は止めない）。"""
    from .profile import build_profile
    st = cfg.data.get("style", {})
    out = cfg.work_dir / "style" / "profile.json"
    pdf = None
    if st.get("pdf_path"):
        p = Path(st["pdf_path"])
        pdf = p if p.is_absolute() else (cfg.base_dir / p)
    elif st.get("dropbox_path"):
        from ..sources.dropbox_source import _with_retry, make_client
        pdf = cfg.work_dir / "style" / "source.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        dbx = make_client()
        _with_retry(lambda: dbx.files_download_to_file(str(pdf), st["dropbox_path"]), "文章スタイルPDFの取得")
    if pdf is None or not pdf.exists():
        return None
    return build_profile(pdf, out, db=db)
