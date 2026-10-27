"""科目・講義回の自動分類。

ファイル名・フォルダ名・資料の表紙と見出し・専門用語・発言を合わせて判定し、
根拠が弱い・食い違う場合は「要確認」にする。元ファイルは動かさず、結果はメタデータとして持つ。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from . import text as T

UNKNOWN = "要確認"
GENERIC_FOLDERS = re.compile(
    r"^(授業動画|動画|録画|資料|講義資料|配布資料|スライド|過去問|課題|演習|小テスト|解答|試験|試験範囲|"
    r"その他|input|inputs|data|材料|共有|share|shared|video|videos|slides?|docs?|\.|local|dropbox|"
    r"\d{4}(年度?)?|前期|後期|第?\d+回)$", re.I)
LECTURE = [
    (re.compile(r"第\s*(\d{1,2})\s*回"), 3, "「第N回」"),
    (re.compile(r"(?<![\d])(\d{1,2})\s*回目?"), 2, "「N回」"),
    (re.compile(r"(?:lec(?:ture)?|class|week)[_\- #]?(\d{1,2})(?!\d)", re.I), 2, "lecture N"),
    (re.compile(r"#(\d{1,2})(?!\d)"), 1, "#N"),
    (re.compile(r"^(\d{1,2})(?:[_\-. ]|$)"), 1, "ファイル名先頭の番号"),
]


def lecture_of(name: str, folder: str, head_text: str = "") -> tuple[int | None, str]:
    for src, s in (("名前", PurePosixPath(name).stem), ("フォルダ", folder), ("表紙", head_text[:300])):
        for rx, _, label in LECTURE:
            if src == "表紙" and label in ("#N", "ファイル名先頭の番号"):
                continue
            m = rx.search(s)
            if m and 1 <= int(m.group(1)) <= 30:
                return int(m.group(1)), f"{src}の{label}"
    return None, ""


def _catalog_hits(cat: list[dict], path: str, text: str) -> list[tuple[float, str, list[str]]]:
    out = []
    p = T.norm(path)
    tx = T.norm(text)
    for s in cat:
        names = [s["name"], *s.get("aliases", [])]
        score, ev = 0.0, []
        for n in names:
            if T.norm(n) and T.norm(n) in p:
                score += 5
                ev.append(f"ファイル名・フォルダ名に「{n}」")
                break
        for n in names:
            if T.norm(n) and T.norm(n) in tx:
                score += 3
                ev.append(f"本文に「{n}」")
                break
        kw = [k for k in s.get("keywords", []) if T.norm(k) in tx]
        if kw:
            score += min(len(kw), 5) * 1.0  # 専門用語 3 語以上で判定できる
            ev.append(f"専門用語 {kw[:5]}")
        for t in s.get("teachers", []):
            if t in tx or t in p:
                score += 1.5
                ev.append(f"教員名「{t}」")
        if score:
            out.append((score, s["name"], ev))
    return sorted(out, reverse=True)


def _folder_subject(folder: str) -> str | None:
    for part in PurePosixPath(folder).parts:
        part = part.strip()
        if part and not GENERIC_FOLDERS.match(part) and not re.match(r"^第?\d+回", part):
            return part
    return None


def classify(materials: list[dict], readings: dict[str, dict], catalog: list[dict]) -> dict[str, dict]:
    """戻り値: material id → {subject, confidence, evidence, lecture, lecture_evidence}"""
    out: dict[str, dict] = {}
    for m in materials:
        units = readings.get(m["id"], {}).get("units", [])
        body = " ".join(u.get("text", "") for u in units[:40])
        head = " ".join(u.get("text", "") for u in units[:2])
        path = f"{m['folder']}/{m['name']}"
        hits = _catalog_hits(catalog, path, body)
        subject, conf, ev = UNKNOWN, "低", []
        if hits:
            top = hits[0]
            second = hits[1][0] if len(hits) > 1 else 0
            if top[0] >= 3 and top[0] >= second * 1.5:
                subject, ev = top[1], top[2]
                conf = "高" if top[0] >= 5 else "中"
            else:
                ev = [f"候補が拮抗: {[h[1] for h in hits[:3]]}"]
        if subject == UNKNOWN:
            fs = _folder_subject(m["folder"])
            if fs:
                subject, conf, ev = fs, "中", ev + [f"フォルダ名「{fs}」"]
        lec, lev = lecture_of(m["name"], m["folder"], head)
        out[m["id"]] = {"subject": subject, "confidence": conf, "evidence": ev, "lecture": lec,
                        "lecture_evidence": lev}
    _propagate_by_content(materials, readings, out)
    return out


def _propagate_by_content(materials: list[dict], readings: dict[str, dict], out: dict[str, dict]) -> None:
    """科目が不明の資料を、内容が似ている分類済み資料（同じ用語が多い）に合わせる。"""
    profile: dict[str, Counter] = {}
    for m in materials:
        c = out[m["id"]]
        if c["subject"] == UNKNOWN:
            continue
        txt = " ".join(u.get("text", "") for u in readings.get(m["id"], {}).get("units", []))
        profile.setdefault(c["subject"], Counter()).update(set(T.terms(txt)))
    if not profile:
        return
    for m in materials:
        c = out[m["id"]]
        if c["subject"] != UNKNOWN:
            continue
        mine = set(T.terms(" ".join(u.get("text", "") for u in readings.get(m["id"], {}).get("units", []))))
        if len(mine) < 5:
            continue
        scores = sorted(((len(mine & set(p)) / len(mine), s) for s, p in profile.items()), reverse=True)
        if scores[0][0] >= 0.3 and (len(scores) == 1 or scores[0][0] >= scores[1][0] * 1.5):
            c.update(subject=scores[0][1], confidence="中",
                     evidence=c["evidence"] + [f"内容が「{scores[0][1]}」の資料と共通（用語の {scores[0][0]:.0%}）"])
