"""ローカル規則による分類候補の抽出と、AI結果の根拠照合。

AIを使わない場合でも、資料・発言の原文から10分類の「候補」を抜き出す。
これは要約ではなく原文の抜粋であり、推測で補完しない。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .models import Item

RULES: dict[str, list[str]] = {
    "purpose_overview": [r"目的", r"概要", r"目標", r"本実験では", r"ねらい", r"趣旨"],
    "procedure": [r"手順", r"方法", r"操作", r"準備", r"設置", r"載荷", r"加力", r"試験体", r"セットし",
                  r"まず", r"次に", r"最後に"],
    "measurement_data": [r"測定", r"計測", r"データ", r"荷重", r"変位", r"ひずみ", r"歪", r"\bkN\b",
                         r"\bmm\b", r"με", r"μ", r"読み取", r"記録"],
    "excel_method": [r"Excel", r"エクセル", r"セル", r"関数", r"シート", r"=\s*[A-Z]+\(", r"SUM", r"AVERAGE",
                     r"オートフィル", r"散布図", r"近似曲線", r"\$[A-Z]+\$?\d+"],
    "report_method": [r"レポート", r"考察", r"結論", r"書き方", r"表紙", r"図番号", r"表番号", r"章", r"緒言",
                      r"参考文献", r"文献"],
    "formulas_graphs": [r"式", r"計算", r"ヤング", r"弾性", r"応力", r"σ", r"ε", r"降伏", r"引張強さ",
                        r"断面積", r"グラフ", r"縦軸", r"横軸", r"[A-Za-zσεΔ]\s*=\s*[^=]"],
    "submission_requirements": [r"提出", r"締切", r"締め切り", r"期限", r"ファイル名", r"枚数", r"ページ数",
                                r"形式で", r"PDF", r"Word", r"A4", r"手書き"],
    "instructor_instructions": [r"してください", r"して下さい", r"すること", r"しなさい", r"しておいて",
                                r"必ず", r"指示", r"お願いします"],
    "cautions_prohibitions": [r"注意", r"禁止", r"してはいけない", r"してはならない", r"しないこと",
                              r"しないでください", r"危険", r"厳禁", r"ダメ", r"だめ", r"不可", r"減点"],
}
_COMPILED = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in RULES.items()}

_SENT_SPLIT = re.compile(r"(?<=[。．！？!?])\s*|\n+")


def split_sentences(text: str) -> list[str]:
    out = []
    for s in _SENT_SPLIT.split(text or ""):
        s = s.strip()
        if len(s) >= 4:
            out.append(s[:500])
    return out


def classify_sentence(s: str) -> list[str]:
    cats = []
    for cat, pats in _COMPILED.items():
        if any(p.search(s) for p in pats):
            cats.append(cat)
    return cats


def rule_items(segments: Iterable[dict], max_items: int = 400) -> list[Item]:
    """segments: {text, origin, page?, t_start?, t_end?}

    1つの文が複数分類に該当する場合は各分類に1件ずつ登録する。"""
    items: list[Item] = []
    for seg in segments:
        origin = seg.get("origin", "document_text")
        for s in split_sentences(seg.get("text", "")):
            for cat in classify_sentence(s):
                if cat == "instructor_instructions" and origin not in ("speech", "document_text"):
                    continue
                items.append(Item(
                    category=cat, statement=s, origin=origin, evidence=s, confidence="low",
                    is_instructor_instruction=(cat == "instructor_instructions"),
                    verification="rule_extracted", page=seg.get("page"),
                    t_start=seg.get("t_start"), t_end=seg.get("t_end"), analyzer="local-rules",
                ))
                if len(items) >= max_items:
                    return items
    return items


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s　「」『』\"'“”‘’、,。.・]", "", s).lower()


def verify_evidence(evidence: str, corpus: str) -> str:
    """AI が示した根拠（引用）がローカル抽出テキストに含まれるか照合する。"""
    if not evidence:
        return "evidence_not_found"
    c = normalize(corpus)
    if not c:
        return "no_local_text"
    e = normalize(evidence)
    if not e:
        return "evidence_not_found"
    if e in c:
        return "evidence_matched"
    # OCR・文字起こしの揺れを考慮し、長い引用は8文字以上の断片の過半一致で可とする
    if len(e) >= 16:
        frags = [e[i:i + 8] for i in range(0, len(e) - 7, 8)]
        hit = sum(1 for f in frags if f in c)
        if frags and hit / len(frags) >= 0.6:
            return "evidence_matched"
    return "evidence_not_found"


# ---- 矛盾検出（ローカル規則） ----
_KEYED_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("提出期限", re.compile(r"(?:提出期限|締切|締め切り|期限)[^0-9０-９]{0,12}([0-9０-９]{1,2}\s*[月/／]\s*[0-9０-９]{1,2}\s*日?)")),
    ("ページ数・枚数", re.compile(r"(?:ページ数|枚数|ページ以内|枚以内)[^0-9０-９]{0,8}([0-9０-９]{1,3})")),
    ("提出形式", re.compile(r"(PDF|Word|Excel|ワード|エクセル|手書き)\s*(?:形式|ファイル)?\s*で\s*提出")),
    ("有効数字", re.compile(r"有効数字[^0-9０-９]{0,6}([0-9０-９])\s*桁")),
]


def keyed_facts(text: str) -> list[tuple[str, str]]:
    out = []
    for key, pat in _KEYED_PATTERNS:
        for m in pat.finditer(text or ""):
            v = unicodedata.normalize("NFKC", m.group(1)).replace(" ", "").replace("／", "/")
            v = v.replace("ワード", "Word").replace("エクセル", "Excel")
            out.append((key, v))
    return out
