"""読み取り結果の照合（無料）。AIの書き起こしを、独立に得た文字（PDFのテキスト層・OCR・対応するPDFのページ）と突き合わせる。

- 網羅: 元の文字（テキスト層・OCR）の行が、書き起こしに含まれているか（取りこぼしの検出）
- 正確さ: 書き起こしの数値が、元の文字に存在するか（読み違い・作り出しの検出）
  グラフから読んだ概算値（approx）は元の文字に無いのが普通なので照合対象外とし、「概算」と明示する
- 動画の画面が PDF のページと同じ内容なら、PDF のテキスト層（正確な文字）とも照合する
照合できない（元の文字が無い）ものは「未照合」とし、成功には数えない。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

NUM = re.compile(r"\d+(?:\.\d+)?(?:/\d+)?")
DASHES = str.maketrans({"−": "-", "－": "-", "ー": "-", "―": "-", "‐": "-", "–": "-", "—": "-", "～": "~",
                        "，": ",", "、": ",", "。": ".", "：": ":", "（": "(", "）": ")"})


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(DASHES)
    return re.sub(r"\s+", "", s)


def numbers(s: str) -> list[str]:
    return NUM.findall(unicodedata.normalize("NFKC", s or ""))


def ai_text(ai: dict, include_approx: bool = False) -> str:
    parts = [ai.get("title", "")]
    parts += [b.get("text", "") for b in ai.get("text_blocks", [])]
    for t in ai.get("tables", []):
        parts += [t.get("caption", ""), " ".join(t.get("header", []))] + [" ".join(r) for r in t.get("rows", [])]
    for f in ai.get("formulas", []):
        parts += [f.get("plain", ""), f.get("latex", "")]
    for g in ai.get("figures", []):
        parts += [g.get("caption", "")] + g.get("labels", []) + g.get("legend", [])
        for a in g.get("axes", []):
            parts += [a.get("label", ""), a.get("unit", ""), a.get("range", "")]
        parts += [v.get("value", "") for v in g.get("values_read", []) if include_approx or not v.get("approx")]
    return "\n".join(p for p in parts if p)


def line_coverage(ref_lines: list[str], text: str, threshold: float = 0.8) -> tuple[float | None, list[str]]:
    """ref_lines のうち text に（ほぼ）含まれる行の割合と、含まれない行。"""
    t = norm(text)
    refs = [ln for ln in (norm(x) for x in ref_lines) if len(ln) >= 2]
    if not refs:
        return None, []
    missing = []
    for r in refs:
        if r in t:
            continue
        sm = SequenceMatcher(None, r, t, autojunk=False)
        hit = sum(b.size for b in sm.get_matching_blocks() if b.size >= 2)
        if hit / len(r) < threshold:
            missing.append(r)
    return 1 - len(missing) / len(refs), missing


def similarity(a: str, b: str) -> float:
    """3文字単位の一致度（Jaccard）。動画の画面と PDF のページの対応付けに使う。"""
    def grams(s):
        s = norm(s)
        return {s[i:i + 3] for i in range(max(0, len(s) - 2))}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def check_numbers(ai: dict, sources: list[str]) -> dict:
    """書き起こしの数値（概算を除く）が、照合元のどれかに含まれるか。"""
    pool = set()
    for s in sources:
        pool.update(numbers(s))
    got = numbers(ai_text(ai, include_approx=False))
    verified = [n for n in got if n in pool]
    unverified = [n for n in got if n not in pool]
    approx = [v for g in ai.get("figures", []) for v in g.get("values_read", []) if v.get("approx")]
    return {"n_numbers": len(got), "verified": len(verified), "unverified": sorted(set(unverified)),
            "approx_values": len(approx)}


def verify_pdf_page(page: dict, ai: dict | None) -> dict:
    res: dict = {"page": page["page"], "text_layer_chars": len(norm(page["text_layer"]))}
    if ai is None:
        res["status"] = "unverified" if page.get("needs_vision") else "text_only"
        res["detail"] = ("図・写真・グラフは画像のため、AIの読み取りを行うまで内容は未確認"
                         if page.get("needs_vision") else "文字情報のみのページ（テキスト層で正確に取得）")
        return res
    lines = [ln["text"] for ln in page["lines"]]
    cov, missing = line_coverage(lines, ai_text(ai, include_approx=True))
    nums = check_numbers(ai, [page["text_layer"], page.get("ocr", "")])
    res.update({"text_layer_coverage": cov, "missing_lines": missing[:20], "numbers": nums})
    bad = (cov is not None and cov < 0.95) or nums["unverified"]
    res["status"] = "needs_review" if bad else "ok"
    res["detail"] = (f"テキスト層の行の {cov:.0%} を書き起こしで確認" if cov is not None else "テキスト層なし") + \
        f"、数値 {nums['verified']}/{nums['n_numbers']} 件をテキスト層・OCRで確認" + \
        (f"（確認できない数値: {', '.join(nums['unverified'][:10])}）" if nums["unverified"] else "") + \
        (f"、グラフからの概算値 {nums['approx_values']} 件（照合対象外）" if nums["approx_values"] else "")
    return res


def link_scenes_to_pdf(scenes: list[dict], pdf_pages: list[tuple[str, dict]], threshold: float = 0.25) -> None:
    """動画の場面の文字（OCR）と PDF の各ページの文字を比べ、同じ内容のページを結び付ける。"""
    for sc in scenes:
        if sc.get("same_as") is not None or not sc.get("ocr"):
            continue
        best = max(((similarity(sc["ocr"], p["text_layer"]), f, p["page"]) for f, p in pdf_pages),
                   default=(0.0, None, None))
        if best[0] >= threshold:
            sc["pdf_match"] = {"file": best[1], "page": best[2], "similarity": round(best[0], 3)}


def verify_video_scene(scene: dict, ai: dict | None, pdf_page: dict | None = None) -> dict:
    res: dict = {"index": scene["index"], "start": scene["start"], "end": scene["end"]}
    if ai is None:
        res["status"] = "unverified"
        res["detail"] = "画面の内容はローカルOCRのみ（精度の目安は報告書の OCR 精度を参照）。AIの読み取り前"
        return res
    text = ai_text(ai, include_approx=True)
    ocr_lines = [ln for ln in (scene.get("ocr") or "").splitlines() if len(norm(ln)) >= 4]
    cov, missing = line_coverage(ocr_lines, text, threshold=0.6)
    sources = [scene.get("ocr", "")]
    if pdf_page is not None:
        sources.append(pdf_page["text_layer"])
        pcov, pmissing = line_coverage([ln["text"] for ln in pdf_page["lines"]], text)
        res["pdf_coverage"] = pcov
        res["pdf_missing"] = pmissing[:20]
    nums = check_numbers(ai, sources)
    res.update({"ocr_coverage": cov, "ocr_missing": missing[:20], "numbers": nums})
    strong = pdf_page is not None
    bad = nums["unverified"] or (strong and (res.get("pdf_coverage") or 0) < 0.9)
    res["status"] = "needs_review" if bad else ("ok" if strong or nums["n_numbers"] == nums["verified"] else "needs_review")
    res["verified_against"] = "PDFのテキスト層＋OCR" if strong else "OCR"
    res["detail"] = (f"照合元: {res['verified_against']}、数値 {nums['verified']}/{nums['n_numbers']} 件を確認"
                     + (f"（確認できない数値: {', '.join(nums['unverified'][:10])}）" if nums["unverified"] else "")
                     + (f"、PDFの行の {res['pdf_coverage']:.0%} を確認" if strong and res.get("pdf_coverage") is not None else ""))
    return res


def apply_speech_corrections(speech: list[dict], corrections: list[dict], vocab_text: str) -> dict:
    """AIの訂正案を、根拠（資料の用語に訂正後の語があるか）を確かめてから適用する。元の文字起こしは text_raw に残す。"""
    vocab = norm(vocab_text)
    applied, rejected = [], []
    for c in corrections:
        i = c.get("segment")
        orig, new = c.get("original", ""), c.get("corrected", "")
        if not (isinstance(i, int) and 0 <= i < len(speech)) or not orig or orig == new:
            rejected.append({**c, "why": "区間・文字が不正"})
            continue
        seg = speech[i]
        seg.setdefault("text_raw", seg["text"])
        if orig not in seg["text"]:
            rejected.append({**c, "why": "元の文字が区間に見つからない"})
            continue
        if norm(new) not in vocab:
            rejected.append({**c, "why": "訂正後の語が資料（スライド・PDF）に無い"})
            continue
        seg["text"] = seg["text"].replace(orig, new, 1)
        seg.setdefault("corrections", []).append({"original": orig, "corrected": new, "reason": c.get("reason", "")})
        applied.append(c)
    return {"applied": len(applied), "rejected": rejected}
