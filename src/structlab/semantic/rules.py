"""日本語の言い回しに基づく指示の分類と、訂正・補足・例外・条件変更の検出（AIを使わない規則）。

ここでの判定は「明示された言い回し」の検出であり、意味理解ではない。役割は次の3つ:
  1. AIを使わない場合の指示一覧（網羅の下限）
  2. AIが抽出した指示の見落とし検査（規則で見つかったのにAIが拾っていない候補）
  3. AIが指示の強さ・向き（してよい/いけない）を勝手に変えていないかの検査
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 指示の分類（A〜H）
DIRECTIVE_CLASSES: dict[str, str] = {
    "prohibited": "A. 禁止事項",
    "discouraged": "B. 非推奨事項",
    "mandatory": "C. 必須事項",
    "request": "D. 要望",
    "recommended": "E. 推奨事項",
    "optional": "F. 任意事項",
    "conditional": "G. 条件付き指示",
    "other": "H. その他の注意事項・判断不能",
}
NEGATIVE = {"prohibited", "discouraged"}
POSITIVE = {"mandatory", "request", "recommended", "optional"}
# 強さの順位（同じ向きの中での比較用）
STRENGTH = {"prohibited": 3, "discouraged": 1, "mandatory": 3, "request": 2, "recommended": 1, "optional": 0}

# 判定順が重要（否定・強い表現を先に見る）
_PATTERNS: list[tuple[str, str]] = [
    ("optional", r"なくても(よい|良い|いい|構わない|かまわない)|しなくて(も)?(よい|良い|いい)|しなくても大丈夫"),
    # 話し言葉の「〜しないとダメ」「〜しなきゃいけない」は必須（否定語を含むので禁止より先に判定）
    ("mandatory", r"ないと(ダメ|だめ|いけない|いけません|まずい)|なきゃ(ダメ|だめ|いけない|いけません)|なくては(ならない|いけない|なりません)"),
    ("prohibited", r"ちゃ(ダメ|だめ|いけない|いけません)|じゃ(ダメ|だめ|いけない)|ないでね|ないで(おいて|ほしい|欲しい)"
                   r"|(して|し|やって|使って|書いて|消して|入れて|変えて|触って|動かして)は(いけない|いけません|ならない|なりません|ダメ|だめ)"
                   r"|てはいけない|てはいけません|てはならない|ではいけない|禁止|厳禁|しないこと|しないでください|しないで下さい"
                   r"|ないでください|ないで下さい|ないように(して)?(ください|下さい|すること)|絶対に?.{0,12}(ない|ません)(で|。|$)|やめてください|不可(です|。|$)"),
    ("discouraged", r"(しない|ない)(方|ほう)が(よい|良い|いい|いいです|望ましい)|避けた(方|ほう)が|避けるのが(望ましい|よい)"
                    r"|お?(すすめ|勧め|薦め)(しません|できません)|望ましくない|好ましくない|あまり.{0,10}(しない|ません)(ように|で)"),
    ("mandatory", r"必ず|しなければ(ならない|なりません|いけない|いけません)|なければ(ならない|なりません|いけない|いけません)"
                  r"|する必要が(ある|あります)|必要があります|必須|しなさい|厳守|義務|すること(。|$)|ること(。|$)|忘れずに|漏れなく"),
    ("optional", r"(て|で)も(よい|良い|いい|構わない|構いません|かまわない|かまいません|大丈夫|OK)"
                 r"|どちらでも|任意|自由に|好きな"),
    ("recommended", r"(した|する|る|た|といた|ておいた)(方|ほう)が(よい|良い|いい|いいです|望ましい)|と(よい|良い|いい)(です|でしょう)?"
                    r"|が望ましい|のが望ましい|お?(すすめ|勧め|薦め)(します|です)|推奨"),
    ("request", r"てね(。|$|！)|といて|ておいて|てくれる|てもらえますか|てほしい|て欲しい|てもらいたい|てもらえると|お願いします|お願いしたい|してください|して下さい"
                r"|ください|下さい|ように(して|しよう)|しましょう|しておいて"),
]
_COMPILED = [(c, re.compile(p)) for c, p in _PATTERNS]
_CONDITION = re.compile(r"(?P<cond>[^。、]{1,40}?(場合は?|ときは?|時は?|際は?|ならば?|であれば|でなければ|以外は|に限り|限りは?))")
_CAUTION = re.compile(r"注意|気をつけ|気を付け|ポイント|重要|大事|大切|間違えやすい|ミスしやすい|よくある間違い")

# 談話標識（訂正・補足・例外・条件変更・強調）
DISCOURSE: dict[str, re.Pattern] = {
    "correction": re.compile(r"訂正|間違え(まし|てい)|間違いでした|言い間違|さっき.{0,20}(言いました|言った|説明した)(が|けど|けれど)"
                             r"|先ほど.{0,20}(言いました|説明しました|述べました)(が|けど)|ではなくて|じゃなくて|修正します|撤回|取り消"),
    "supplement": re.compile(r"補足|付け加え|付け足|追加で|加えて|もう一つ|もうひとつ|それから"),
    "exception": re.compile(r"ただし|但し|例外|を除いて|を除き|以外は"),
    "condition_change": re.compile(r"に変更|変更になり|変更します|変わります|変わりました|からは.{0,10}(する|します)"),
    "emphasis": re.compile(r"何度も言いますが|繰り返しますが|もう一度言います|強調|重要|大事なこと"),
}

_SENT = re.compile(r"(?<=[。．！？!?])\s*|\n+")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", "", s)


def classify(text: str) -> tuple[str | None, str | None]:
    """(分類, 根拠となった表現)。指示表現が無ければ (None, None)。"""
    t = norm(text)
    for cls, pat in _COMPILED:
        m = pat.search(t)
        if m:
            return cls, m.group(0)
    if _CAUTION.search(t):
        return "other", _CAUTION.search(t).group(0)
    return None, None


def polarity(cls: str | None) -> str | None:
    if cls in NEGATIVE:
        return "negative"
    if cls in POSITIVE:
        return "positive"
    return None


def discourse_markers(text: str) -> list[str]:
    t = norm(text)
    return [k for k, p in DISCOURSE.items() if p.search(t)]


def condition_of(text: str) -> str | None:
    m = _CONDITION.search(norm(text))
    return m.group("cond") if m else None


def split_clauses(sentence: str) -> list[str]:
    """1つの発言に複数の指示が含まれる場合に分ける。
    「Aして、Bしないでください」→ ["Aして", "Bしないでください"] のように、
    指示表現または て形で終わる節ごとに区切る。"""
    parts = re.split(r"(?<=[、,])", sentence)
    out: list[str] = []
    buf = ""
    for p in parts:
        buf += p
        core = buf.rstrip("、, ")
        if classify(core)[0] or re.search(r"(て|で)$", core):
            out.append(core)
            buf = ""
    if buf.strip("、, "):
        if out:
            out[-1] = out[-1] + "、" + buf.rstrip("、, ")
        else:
            out.append(buf.rstrip("、, "))
    return out


@dataclass
class RuleDirective:
    content: str
    directive_class: str
    base_strength: str | None
    marker: str
    condition: str | None
    discourse: list[str] = field(default_factory=list)
    certainty: str = "medium"
    note: str = ""


def directives_in(text: str) -> list[RuleDirective]:
    """テキスト（1発言・1文〜数文）から規則で指示候補を取り出す。"""
    out: list[RuleDirective] = []
    carry: list[str] = []  # 指示を含まない直前の文の談話標識（例:「訂正します。」）を次の指示に引き継ぐ
    for sent in [s.strip() for s in _SENT.split(text or "") if s and s.strip()]:
        clauses = split_clauses(sent)
        sent_markers = sorted(set(discourse_markers(sent)) | set(carry))
        if not any(classify(c)[0] for c in clauses) and not any(re.search(r"(て|で)$", c) for c in clauses[:-1]):
            carry = discourse_markers(sent)
            continue
        carry = []
        final_cls = classify(clauses[-1])[0] if clauses else None
        for i, cl in enumerate(clauses):
            cls, marker = classify(cl)
            note = ""
            certainty = "medium"
            if cls is None and re.search(r"(て|で)$", cl) and i < len(clauses) - 1 \
                    and final_cls in ("request", "mandatory", "prohibited"):
                # 「Aして、Bしてください／Bしないでください」の A。後続の述語から推定するが確実ではない
                cls, marker = ("mandatory" if final_cls == "mandatory" else "request"), "（後続の述語から推定）"
                if re.search(r"ないで$|ずに$", cl):
                    cls = "prohibited"
                certainty = "low"
                note = "文中の て形の指示。強さは後続の述語から推定しており要確認"
            if cls is None:
                continue
            cond = condition_of(cl)
            base = None
            if cond and cls != "other":
                base, cls = cls, "conditional"
            if cls == "other":
                certainty = "low"
            out.append(RuleDirective(content=cl, directive_class=cls, base_strength=base, marker=marker or "",
                                     condition=cond, discourse=sent_markers, certainty=certainty, note=note))
    return out


def bigrams(s: str) -> set[str]:
    t = norm(s)
    t = re.sub(r"[。、,.!?！？「」『』（）()・]", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


_MODAL_TAIL = re.compile(r"(してはいけない|しないでください|してください|すること|しなければならない|した方がいい|してもよい"
                         r"|しないこと|ください|ません|ます|です|ない)$")


def target_of(content: str) -> str:
    """指示の対象部分（述語の言い回しを除いた部分）。向きの異なる指示の比較に使う。"""
    t = norm(content)
    t = re.sub(r"(は|を|も)?(必ず|絶対に?)", "", t)
    for _ in range(3):
        t2 = _MODAL_TAIL.sub("", t)
        if t2 == t:
            break
        t = t2
    return t


_WORD = re.compile(r"[一-龥々〆ヵヶ]+|[ァ-ヴー]{2,}|[A-Za-z][A-Za-z0-9.]*|[0-9]+(?:\.[0-9]+)?")
_STOP = {"絶対", "必", "今日", "次", "最後", "全部", "全", "場合", "時", "方", "事", "物", "様"}


def content_words(s: str) -> set[str]:
    """内容語（漢字・カタカナ・英数字の連続）。言い回し（ひらがなの述語・助詞）を除いて対象を比べるために使う。"""
    return {w for w in _WORD.findall(norm(s)) if w not in _STOP}


def target_similarity(a: str, b: str) -> float:
    """2つの指示が同じ対象（作業・物）について述べているかの目安。"""
    A, B = content_words(a), content_words(b)
    if A and B:
        return len(A & B) / len(A | B)
    return similarity(target_of(a), target_of(b))


def similarity(a: str, b: str) -> float:
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)
