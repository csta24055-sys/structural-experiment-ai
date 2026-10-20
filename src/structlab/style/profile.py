"""本人の文章の特徴をまとめたPDFを読み込み、項目ごとに分解して「適用してよい項目」を選ぶ。

このPDFは最終工程（内容・構成・計算・考察が確定した後の文章表現の調整）にだけ使う。
資料の意味理解・指示の判断・計算・Excel作成・実験結果の評価には使わない（このモジュールはそれらから参照されない）。

PDFの全項目を機械的に適用しない:
- 入力時の特徴（誤字・脱字・助詞の脱落・句読点をほとんど使わない等）は正式なレポートの正確性を損なうため除外
- 就職活動の文書（ES・志望動機・自己PR）向けの項目は実験レポートには当てはまらないため除外
- 除外した項目と理由は profile.json にすべて残す
PDFはリポジトリに置かない（作業ディレクトリ work/style/ にだけ保存）。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

_PART = re.compile(r"^第\s*([1-9])\s*部\s*(.*)$")
_SECTION = re.compile(r"^(?P<label>[①-⑳]|[A-Z]\.|\d{1,2}\.)\s*(?P<title>.+?)\s*(?P<a>\d+)\s*[～~〜-]\s*(?P<b>\d+)\s*$")
_ITEM = re.compile(r"^(?P<no>\d{1,4})\.\s*(?P<text>.+)$")
_PAGENO = re.compile(r"^\d{1,3}$")

# 正式なレポートの正確性を損なう特徴（入力・会話の癖）
ACCURACY_EXCLUDE = re.compile(r"誤字|脱字|変換ミス|入力ミス|打ち間違|助詞.{0,6}(抜|欠|省)|句点.{0,8}(使わない|ほとんど|省)|"
                              r"読点.{0,6}少|絵文字|顔文字|疑問符|空白.{0,6}入れ|改行せず|ひらがな|入力速度")
# 就職活動の文書向け（実験レポートには当てはまらない）
GENRE_EXCLUDE = re.compile(r"ES|設問|面接|志望|採用|自己PR|自己ＰＲ|就活|企業|貢献したい|社会に貢献|人物像|応募")


@dataclass
class StyleItem:
    id: str
    part: int
    section: str
    no: int
    text: str
    applicable: bool
    reason: str


def default_policy() -> dict:
    return {
        # 部ごと: 第1部（入力・会話の基礎特徴）は正式なレポートには使わない
        "exclude_parts": [1],
        # 節の見出しに含まれる語で除外（就活文書向けの節）
        "exclude_section_words": ["自己PR", "志望", "就活", "ES", "面接", "応募"],
    }


def repeated_lines(pages: list[str], ratio: float = 0.3) -> set[str]:
    """多くのページに繰り返し出る行（ヘッダー・フッター）。"""
    from collections import Counter
    c = Counter()
    for p in pages:
        c.update({unicodedata.normalize("NFKC", ln).strip() for ln in p.splitlines() if ln.strip()})
    n = max(len(pages), 1)
    return {ln for ln, k in c.items() if n >= 3 and k / n >= ratio}


def parse_text(text: str, policy: dict | None = None, skip: set[str] | None = None) -> tuple[list[StyleItem], dict]:
    policy = policy or default_policy()
    skip = skip or set()
    items: list[StyleItem] = []
    part, section, sec_label = 0, "", ""
    declared: dict[str, int] = {}
    cur: StyleItem | None = None
    for raw in text.splitlines():
        ln = unicodedata.normalize("NFKC", raw).strip()
        if not ln or _PAGENO.match(ln) or ln in skip:
            continue
        m = _PART.match(ln)
        if m:
            part, section, cur = int(m.group(1)), "", None
            continue
        m = _SECTION.match(raw.strip()) or _SECTION.match(ln)  # 丸数字（①）は正規化前の行で判定する
        if m and part:
            sec_label = m.group("label").rstrip(".")
            section = f"{m.group('label')} {m.group('title')}"
            declared[f"{part}:{sec_label}"] = int(m.group("b")) - int(m.group("a")) + 1
            cur = None
            continue
        m = _ITEM.match(ln)
        if m and part and section:
            cur = StyleItem(id=f"P{part}-{sec_label}-{int(m.group('no')):04d}", part=part, section=section,
                            no=int(m.group("no")), text=m.group("text").strip(), applicable=True, reason="")
            items.append(cur)
            continue
        if cur is not None and part and not ln.startswith(("この部が表すもの", "使い方", "注:", "注：")):
            cur.text += ln  # 折り返された項目の続き
    for it in items:
        it.applicable, it.reason = _judge(it, policy)
    stats = {"parsed": len(items), "declared_total": sum(declared.values()),
             "by_part": {p: sum(1 for i in items if i.part == p) for p in sorted({i.part for i in items})},
             "applicable": sum(1 for i in items if i.applicable),
             "excluded": sum(1 for i in items if not i.applicable)}
    return items, stats


def _judge(it: StyleItem, policy: dict) -> tuple[bool, str]:
    if it.part in policy.get("exclude_parts", []):
        return False, "入力・会話の基礎特徴（正式なレポートには使わない）"
    if any(w in it.section for w in policy.get("exclude_section_words", [])):
        return False, "就職活動の文書向けの節（実験レポートには当てはまらない）"
    if ACCURACY_EXCLUDE.search(it.text):
        return False, "正式な文章の正確性を損なう特徴"
    if GENRE_EXCLUDE.search(it.text):
        return False, "就職活動の文書向けの項目"
    return True, ""


def build_profile(pdf: Path, out: Path, policy: dict | None = None) -> dict:
    import pymupdf
    data = pdf.read_bytes()
    with pymupdf.open(stream=data, filetype="pdf") as d:
        pages = [p.get_text() for p in d]
    items, stats = parse_text("\n".join(pages), policy, repeated_lines(pages))
    prof = {"source_name": pdf.name, "source_sha256": hashlib.sha256(data).hexdigest(), "stats": stats,
            "policy": policy or default_policy(), "items": [asdict(i) for i in items]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    return prof


def load_profile(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def select_rules(prof: dict, max_rules: int, kind: str = "") -> list[dict]:
    """適用可能な項目から、節ごとに均等に（決まった順で）選ぶ。全項目を一度に使わない。
    最終チェック用の第4部（過剰な整え・創作・均一化の抑止）を優先する。"""
    items = [i for i in prof["items"] if i["applicable"]]
    by_sec: dict[str, list] = {}
    for i in items:
        by_sec.setdefault(f"{i['part']}|{i['section']}", []).append(i)
    order = sorted(by_sec, key=lambda k: (0 if k.startswith("4|") else 1, k))
    out, k = [], 0
    while len(out) < max_rules and any(by_sec.values()):
        sec = order[k % len(order)]
        if by_sec[sec]:
            out.append(by_sec[sec].pop(0))
        k += 1
        if k > max_rules * len(order) + len(items):
            break
    return out
