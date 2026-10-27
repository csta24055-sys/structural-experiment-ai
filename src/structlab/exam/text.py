"""日本語テキストの簡単な処理（形態素解析器に依存しない）。"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

TERM = re.compile(r"[一-龠々〆ヵヶ]{2,}|[ァ-ヴー]{3,}|[A-Za-z][A-Za-z0-9]{2,}")
STOP = {"です", "ます", "ください", "について", "ここ", "これ", "それ", "今日", "次回", "前回", "授業", "資料",
        "スライド", "説明", "問題", "場合", "以下", "以上", "使用", "必要", "可能", "注意", "確認", "部分", "内容",
        "試験", "テスト", "重要", "覚え", "計算", "理解", "出題", "範囲", "大丈夫", "ページ", "the", "and", "for"}
#: 式: 等号の両側に記号・数字を含む行（「〜とは＝」のような文章は除く）
FORMULA = re.compile(r"([A-Za-zα-ωΑ-Ωσετδλμρθφψ][A-Za-z0-9_α-ωΑ-Ω·・^′'()\s]*)\s*[=＝]\s*([^=＝。、]{1,80})")


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def terms(text: str) -> list[str]:
    return [t for t in TERM.findall(norm(text)) if t not in STOP and not t.isdigit()]


def bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", norm(text))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similarity(a: str, b: str) -> float:
    """文字 2-gram の重なり（短い方に対する割合）。0〜1。"""
    x, y = bigrams(a), bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / min(len(x), len(y))


def top_terms(texts: list[str], n: int = 8) -> list[str]:
    c = Counter(t for tx in texts for t in set(terms(tx)))
    return [t for t, _ in c.most_common(n)]


def formulas(text: str) -> list[str]:
    out = []
    for line in norm(text).split("\n"):
        line = line.strip()
        if len(line) > 120 or not FORMULA.search(line):
            continue
        if re.search(r"[ぁ-ん]{4,}", line):  # 文章中の「=」は式として扱わない
            continue
        out.append(line)
    return out


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。．！？!?])\s*|\n+", norm(text))
    return [p.strip() for p in parts if len(p.strip()) >= 6]
