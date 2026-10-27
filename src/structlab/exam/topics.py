"""論点の抽出と、論点ごとの根拠（過去問・課題・反復・強調）の照合。

論点 = 配布資料のページ見出し（無ければ動画画面の見出し、さらに無ければ教員の手がかりの文脈）。
同じ科目で見出しが同じ・内容が近いものはまとめるが、同じ記号の式が食い違うものはまとめない。
"""
from __future__ import annotations

import hashlib
import re

from . import text as T

QUESTION_SPLIT = re.compile(r"(?=(?:^|\n)\s*(?:問\s*\d+|第\s*\d+\s*問|\[\s*\d+\s*\]|【\s*\d+\s*】|[（(]\s*\d+\s*[)）]|\d+\s*[.．]\s))")
SCOPE_RANGE = re.compile(r"第?\s*(\d{1,2})\s*回?\s*(?:から|〜|~|－|-|―)\s*第?\s*(\d{1,2})\s*回")
LECTURE_MENTION = re.compile(r"第\s*(\d{1,2})\s*回")
EMPH_WORDS = re.compile(r"重要|ポイント|注意|必須|頻出|★")
COVER = re.compile(r"^\s*第\s*\d+\s*回")


def _tid(subject: str, title: str, n: int) -> str:
    return "T" + hashlib.sha1(f"{subject}|{title}|{n}".encode()).hexdigest()[:8]


def _lhs(f: str) -> str:
    return re.split(r"[=＝]", f, maxsplit=1)[0].strip()


def _conflicting(fa: list[str], fb: list[str]) -> bool:
    """同じ左辺で右辺が違う式があれば、別の条件・変更された説明の可能性があるのでまとめない。"""
    da = {_lhs(f): re.sub(r"\s", "", f) for f in fa}
    return any(_lhs(f) in da and da[_lhs(f)] != re.sub(r"\s", "", f) for f in fb)


def build_topics(subject: str, mats: list[dict], readings: dict[str, dict], cls: dict[str, dict]) -> list[dict]:
    pages: list[dict] = []
    for m in mats:
        if m["role"] in ("past_exam", "answer", "assignment", "exercise", "quiz", "scope"):
            continue
        lec = cls[m["id"]]["lecture"]
        for u in readings.get(m["id"], {}).get("units", []):
            if u["kind"] in ("page", "text") or (u["kind"] == "scene" and u.get("text", "").strip()):
                txt = u.get("text", "")
                head = u.get("heading") or next((x for x in T.norm(txt).split("\n") if len(x.strip()) >= 2), "")
                if len(T.norm(txt).strip()) < 15:
                    continue
                if u.get("page") == 1 and COVER.search(T.norm(head)):
                    continue  # 表紙（「第3回 …」）は論点にしない
                pages.append({"material": m["id"], "name": m["name"], "lecture": lec, "kind": u["kind"],
                              "page": u.get("page"), "start": u.get("start"), "end": u.get("end"),
                              "heading": T.norm(head).strip()[:60], "text": txt})
    topics: list[dict] = []
    for p in pages:
        f = T.formulas(p["text"])
        hit = None
        for t in topics:
            if T.similarity(p["heading"], t["title"]) >= 0.85 and not _conflicting(t["formulas"], f):
                hit = t
                break
        src = {k: p[k] for k in ("material", "name", "lecture", "kind", "page", "start", "end")}
        if hit:
            hit["sources"].append(src)
            hit["texts"].append(p["text"])
            hit["formulas"] += [x for x in f if x not in hit["formulas"]]
            if p["lecture"] and p["lecture"] not in hit["lectures"]:
                hit["lectures"].append(p["lecture"])
        else:
            topics.append({"id": _tid(subject, p["heading"], len(topics)), "subject": subject, "title": p["heading"],
                           "lectures": [p["lecture"]] if p["lecture"] else [], "sources": [src],
                           "texts": [p["text"]], "formulas": f})
    for t in topics:
        sents = [s for tx in t["texts"] for s in T.sentences(tx)]
        t["key_sentences"] = sents[:12]
        t["summary"] = " ".join(sents[:4])[:400]
        t["terms"] = T.top_terms(t["texts"], 8)
        t["emphasis"] = bool(EMPH_WORDS.search(" ".join(t["texts"])))
        t["evidence"] = []
        t["lectures"].sort()
    return topics


def topic_from_cue(subject: str, cue, n: int) -> dict:
    """スライドに結びつかない発言（動画だけの授業）から論点を作る。題名は文脈の主な用語。"""
    terms = T.top_terms([cue.context or cue.text], 4)
    title = "・".join(terms[:3]) or cue.text[:30]
    return {"id": _tid(subject, "cue:" + cue.text, n), "subject": subject, "title": title,
            "lectures": [cue.lecture] if cue.lecture else [], "sources": [
                {"material": cue.material, "name": "", "lecture": cue.lecture, "kind": "speech",
                 "page": None, "start": cue.start, "end": cue.end}],
            "texts": [cue.context or cue.text], "formulas": list(cue.formulas),
            "key_sentences": T.sentences(cue.context or cue.text)[:8], "summary": (cue.context or cue.text)[:400],
            "terms": terms, "emphasis": False, "evidence": [], "from_speech_only": True}


def split_questions(text: str) -> list[str]:
    parts = [p.strip() for p in QUESTION_SPLIT.split(T.norm(text)) if len(p.strip()) >= 8]
    return parts or ([T.norm(text).strip()] if text.strip() else [])


def match_topic(q: str, topics: list[dict], min_sim: float = 0.3) -> tuple[dict | None, float]:
    best, score = None, 0.0
    for t in topics:
        body = t["title"] + " " + t["summary"] + " " + " ".join(t["formulas"]) + " " + " ".join(t["terms"])
        s = max(T.similarity(q, body), 1.2 * T.similarity(q, t["title"]) if len(t["title"]) >= 3 else 0)
        f_hit = any(re.sub(r"\s", "", f) in re.sub(r"\s", "", q) for f in t["formulas"])
        s = max(s, 0.9 if f_hit else 0)
        if s > score:
            best, score = t, s
    return (best, score) if score >= min_sim else (None, score)


def attach_document_evidence(topics: list[dict], mats: list[dict], readings: dict[str, dict],
                             cls: dict[str, dict]) -> dict:
    """過去問・課題・演習・試験範囲の資料を論点に照合する。戻り値: 科目全体の情報（範囲・未照合の設問）"""
    info: dict = {"scope_lectures": None, "scope_sources": [], "unmatched_questions": [], "past_exam_years": []}
    for m in mats:
        role = m["role"]
        units = readings.get(m["id"], {}).get("units", [])
        text = "\n".join(u.get("text", "") for u in units if u["kind"] in ("page", "text"))
        if role == "scope" or "試験範囲" in text[:500]:
            r = SCOPE_RANGE.search(T.norm(text))
            if r:
                lo, hi = sorted((int(r.group(1)), int(r.group(2))))
                info["scope_lectures"] = list(range(lo, hi + 1))
                info["scope_sources"].append({"material": m["id"], "name": m["name"], "text": r.group(0)})
        if role not in ("past_exam", "assignment", "exercise", "quiz"):
            continue
        year = m.get("year")
        if role == "past_exam" and year and year not in info["past_exam_years"]:
            info["past_exam_years"].append(year)
        for u in units:
            if u["kind"] not in ("page", "text"):
                continue
            for q in split_questions(u.get("text", "")):
                t, s = match_topic(q, topics)
                if t is None:
                    if role == "past_exam":
                        info["unmatched_questions"].append({"material": m["name"], "page": u.get("page"),
                                                            "text": q[:200], "year": year})
                    continue
                t["evidence"].append({"type": "past_exam" if role == "past_exam" else "assignment",
                                      "role": role, "material": m["id"], "name": m["name"], "page": u.get("page"),
                                      "year": year, "text": q[:300], "match": round(s, 2),
                                      "basis": "確認済み（資料の照合）"})
    return info


def attach_repetition(topics: list[dict], all_units: list[tuple[str, int | None, dict]]) -> None:
    """同じ論点が別の講義回・資料・発言に再登場する回数（反復）を数える。"""
    for t in topics:
        own = {(s["material"], s.get("page"), s.get("start")) for s in t["sources"]}
        seen = set()
        keys = [k for k in t["terms"][:5] if len(k) >= 2]
        if not keys:
            continue
        for mid, lec, u in all_units:
            if (mid, u.get("page"), u.get("start")) in own:
                continue
            txt = T.norm(u.get("text", ""))
            hits = sum(1 for k in keys if k in txt)
            if hits >= min(3, len(keys)) or (t["formulas"] and any(
                    re.sub(r"\s", "", f) in re.sub(r"\s", "", txt) for f in t["formulas"])):
                seen.add((mid, lec))
        lectures = {lec for _, lec in seen if lec is not None and lec not in t["lectures"]}
        t["repetition"] = {"other_places": len(seen), "other_lectures": sorted(lectures)}
