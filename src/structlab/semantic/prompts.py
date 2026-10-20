"""意味理解層の指示文と出力スキーマ。

出力スキーマは Anthropic の構造化出力（json_schema）向けに、全プロパティ required・
additionalProperties false で記述している。null を許す値は anyOf で表す。
"""
from __future__ import annotations

from ..ai.prompts import OUTPUT_SCHEMA as LEGACY_SCHEMA
from .rules import DIRECTIVE_CLASSES

SEMANTIC_VERSION = "sem-1"

_N = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_S = {"type": "string"}
_SN = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_I = {"type": "integer"}
_AS = {"type": "array", "items": _S}
_AI = {"type": "array", "items": _I}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


CLASS_ENUM = list(DIRECTIVE_CLASSES)
STRENGTH_ENUM = {"anyOf": [{"type": "string", "enum": [c for c in CLASS_ENUM if c not in ("conditional", "other")]},
                           {"type": "null"}]}
CERTAINTY = {"type": "string", "enum": ["high", "medium", "low"]}
DISCOURSE_TYPES = ["correction", "supplement", "exception", "condition_change", "emphasis"]

COMMON_SYSTEM = """あなたは建築構造実験（鋼材の引張試験など）の授業動画・資料を、学生の立場で深く理解する解析者です。
目的は、文字起こし・OCR・画像説明の羅列や短い要約ではなく、先生が「何を」「なぜ」説明し、
資料全体で何を伝え、学生に何をしてほしいのか・してはいけないのかを、根拠付きで理解することです。

厳守事項:
- 与えられた発話・画面・資料・前後の文脈だけを根拠にする。読み取れないこと・聞き取れないことを推測で補わない。
- 「資料や発言に明示されていること」と「あなたの推論」を必ず区別する。推論は推論として書く。
- 不確かな解釈は不確かと明記し、uncertain / unconfirmed / open_questions に残す。
- 前半と後半の説明が食い違う場合、訂正・補足・例外・条件変更として説明できる根拠（発言）がない限り、
  どちらが正しいかを決めず、未解決の矛盾として記録する。
- 時刻は動画先頭からの秒数で答える。分からなければ null。
- 実験レポートやExcelの提出物そのものは作らない。
- 出力は日本語。
"""

# ---------------- 第1回: 場面の局所理解 ----------------
SCENE_ANALYSIS = _obj({
    "speech": _S,          # 先生が何を話しているか（要旨＋重要な原文）
    "screen": _S,          # 画面に何が表示されているか
    "operations": _S,      # どのような操作・実験が行われているか
    "meaning": _S,         # その説明・操作が何を意味しているか
    "purpose": _S,         # なぜその操作・説明を行っているのか
    "relation_prev": _S,   # 直前の説明との関係
    "relation_next": _S,   # 直後の説明との関係
    "audience": _S,        # 誰に対する指示・説明か
    "task": _S,            # 何の作業についての説明か
    "deliverables": _AS,   # 関係する提出物
    "key_points": {"type": "array", "items": _obj({
        "text": _S, "time_sec": _N, "basis": {"type": "string", "enum": ["speech", "screen", "both", "document", "inference"]}})},
    "discourse": {"type": "array", "items": _obj({
        "type": {"type": "string", "enum": DISCOURSE_TYPES}, "description": _S, "time_sec": _N, "evidence": _S})},
    "uncertain": _AS,
    "unreadable": _AS,
    "items": LEGACY_SCHEMA["properties"]["items"],   # 従来の10分類（knowledge.md 用）
})
SCENE_SCHEMA = _obj({"analysis": SCENE_ANALYSIS})

SCENE_INSTRUCTION = """## 作業（第1回: 場面の局所理解）
次の「対象場面」について、音声（発話）と映像（画面・操作）を時刻で対応付けて統合し、場面の意味を理解してください。
1. 先生が何を話しているか 2. 画面に何が表示されているか 3. どのような操作・実験か 4. その意味 5. なぜそれを行うのか
6. 直前・直後の説明との関係 7. 誰に対する指示か 8. 何の作業についてか 9. どの作業・提出物に関係するか
Excelを操作しながらの説明であれば、セル・数式・表・グラフ・操作手順も読み取ってください。
その場の発言だけで決めつけず、「直前の場面の理解」「直後の場面の発話」も踏まえてください。
訂正・補足・例外・条件変更・強調があれば discourse に根拠の発言とともに記録してください。
items には従来の10分類（目的・手順・測定データ・Excel・レポート・計算式・提出物・先生の指示・注意事項・不明点）で重要事項を入れてください。
"""

# ---------------- 第1回: 全体理解 ----------------
_SCENE_REFS = _AI
GLOBAL_SCHEMA = _obj({
    "theme": _S, "purpose": _S, "experiment_purpose": _S, "message": _S,
    "flow": {"type": "array", "items": _obj({"order": _I, "description": _S, "scene_seqs": _SCENE_REFS})},
    "logic_structure": _S,
    "scene_roles": {"type": "array", "items": _obj({"scene_seq": _I, "role": _S})},
    "procedure": {"type": "array", "items": _obj({
        "step": _I, "action": _S, "purpose": _S, "depends_on": _AI, "scene_seqs": _SCENE_REFS})},
    "conclusion": _S,
    "important_points": {"type": "array", "items": _obj({"text": _S, "scene_seqs": _SCENE_REFS})},
    "changes": {"type": "array", "items": _obj({
        "type": {"type": "string", "enum": DISCOURSE_TYPES}, "description": _S,
        "earlier_scene": {"anyOf": [_I, {"type": "null"}]}, "later_scene": {"anyOf": [_I, {"type": "null"}]},
        "evidence": _S})},
    "repeated_cautions": {"type": "array", "items": _obj({"text": _S, "scene_seqs": _SCENE_REFS})},
    "apparent_contradictions": {"type": "array", "items": _obj({
        "description": _S, "scene_seqs": _SCENE_REFS, "resolved": {"type": "boolean"},
        "resolution": _S, "resolution_evidence": _S})},
    "final_requirements": {"type": "array", "items": _obj({"text": _S, "scene_seqs": _SCENE_REFS})},
    "understand_goals": _AS,
    "action_goals": _AS,
    "open_questions": _AS,
    "explanation": _S,
})
GLOBAL_INSTRUCTION = """## 作業（第1回: 資料全体の意味理解）
各場面の理解結果を統合し、資料（動画）全体で先生が何を説明し、何を伝えようとしているのかを理解してください。
テーマ・目的・実験と提出物の目的・伝えたい内容・説明の流れ・論理構造・各場面の役割・作業の正しい順序と依存関係・結論・
重要事項・訂正/補足/例外/条件変更・繰り返し強調された注意・一見矛盾する説明の整合性・最終的に守るべき要件・
学生に理解してほしいこと・実行してほしいことを、場面番号の根拠付きで答えてください。
apparent_contradictions は、発言の根拠で整合が説明できる場合のみ resolved=true とし、その根拠を resolution_evidence に原文で書くこと。
explanation には、資料を見ていない人にも内容・目的・手順・重要事項を詳しく説明できる文章を書いてください（要約ではなく、説明のつながりと理由まで）。
"""

# ---------------- 第1回: 全体理解を使った再解釈 ----------------
REINTERPRET_SCHEMA = _obj({
    "revised": {"type": "boolean"},
    "changes": {"type": "array", "items": _obj({"field": _S, "before": _S, "after": _S, "reason": _S})},
    "analysis": SCENE_ANALYSIS,
})
REINTERPRET_INSTRUCTION = """## 作業（局所理解と全体理解の相互照合）
資料全体の理解結果を踏まえて、この場面の解釈を見直してください。
後の場面での訂正・補足・例外・条件変更によって、この場面の説明の意味や有効性が変わる場合は、その内容を反映し、
変更点を changes に（変更前・変更後・理由）記録してください。変更がなければ revised=false とし analysis は現在の解釈のまま返してください。
全体理解の側が誤っていると考えられる場合も、その旨を uncertain に書いてください。
"""

# ---------------- 第2回: 指示の重点抽出 ----------------
DIRECTIVE_ITEM = _obj({
    "content": _S,
    "directive_class": {"type": "string", "enum": CLASS_ENUM},
    "base_strength": STRENGTH_ENUM,
    "condition": _SN,
    "target_task": _S, "deliverable": _S, "audience": _S,
    "t_start": _N, "t_end": _N,
    "screen": _S, "operation": _S, "speech_quote": _S,
    "context_before": _S, "context_after": _S,
    "certainty": CERTAINTY, "unconfirmed": _S,
    "origin": {"type": "string", "enum": ["explicit", "ai_inference"]},
    "relation_type": {"type": "string", "enum": ["none", *DISCOURSE_TYPES]},
    "relation_refers_to": _S,
    "matched_candidates": _AI,
})
DIRECTIVE_SCHEMA = _obj({
    "directives": {"type": "array", "items": DIRECTIVE_ITEM},
    "dismissed_candidates": {"type": "array", "items": _obj({"candidate": _I, "reason": _S})},
})
CLASS_GUIDE = "\n".join(f"- {k}: {v}" for k, v in DIRECTIVE_CLASSES.items())
DIRECTIVE_INSTRUCTION = f"""## 作業（第2回: 指示・禁止事項・注意事項の重点抽出）
資料全体の理解と場面の理解を踏まえ、対象場面に含まれる先生の指示を、漏れなく、1つずつ独立して抽出してください。
分類:
{CLASS_GUIDE}
規則:
- 言葉遣いだけでなく前後の説明と全体の文脈から意味を判断する。ただし強さ（禁止/非推奨/必須/要望/推奨/任意）を
  あなたの推測で変えない。発言の言い回しが示す強さと異なる分類にする場合は、その根拠となる発言を speech_quote に引用し、
  certainty を下げ unconfirmed に理由を書く。禁止を推奨に、推奨を必須に変えるようなことはしない。
- 条件付きの指示は directive_class=conditional とし、条件内での強さを base_strength、条件を condition に書く。
- 1つの発言に複数の指示があれば別々に、1つの指示に複数の分類が関わる場合は unconfirmed に関係を書く。
- 判断できないものは other とし、unconfirmed に理由を書く。
- origin: 発言・画面・資料に明示されていれば explicit、あなたの推論なら ai_inference。
- speech_quote には根拠となる発言・資料の原文をそのまま引用する（要約しない）。
- 訂正・補足・例外・条件変更であれば relation_type と、何に対するものか relation_refers_to を書く。
- 「規則による候補」は言い回しから機械的に拾ったもので、見落とし防止のために必ず全件確認すること。
  指示として採用したものは matched_candidates に番号を入れ、指示でないと判断したものは dismissed_candidates に理由とともに入れる。
"""

# ---------------- 第3回: 元資料との照合 ----------------
VERIFY_SCHEMA = _obj({
    "verdict": {"type": "string", "enum": ["confirmed", "revised", "rejected", "unresolved"]},
    "reason": _S,
    "evidence_quote": _S,
    "revised": {"anyOf": [_obj({
        "content": _S, "directive_class": {"type": "string", "enum": CLASS_ENUM}, "base_strength": STRENGTH_ENUM,
        "condition": _SN, "t_start": _N, "t_end": _N, "certainty": CERTAINTY}), {"type": "null"}]},
    "additional_directives": {"type": "array", "items": DIRECTIVE_ITEM},
})
VERIFY_INSTRUCTION = """## 作業（第3回: 元の映像・音声・資料との照合）
「確認対象」の指示を、広げた範囲の元の発話・画面・資料と照合し、見落とし・誤解・分類ミス・条件の見落としがないか確認してください。
- 元の発話・資料で確認できれば confirmed、内容・分類・条件・時刻の修正が必要なら revised（修正後を revised に）、
  指示ではない・根拠が見つからなければ rejected、判断できなければ unresolved。
- evidence_quote には照合に使った原文をそのまま引用する。
- 分類（強さ）を修正する場合は、原文の言い回しに基づくこと。推測で強めたり弱めたりしない。
- 確認範囲内に、まだ一覧に無い指示を見つけたら additional_directives に入れる。
"""

# ---------------- 資料横断の統合理解 ----------------
_IDS = _AI
CORPUS_SCHEMA = _obj({
    "experiment_purpose": _S, "experiment_content": _S,
    "procedure": {"type": "array", "items": _obj({"step": _I, "action": _S, "sources": _AS, "depends_on": _AI})},
    "data_processing": _S, "excel_method": _S, "report_method": _S,
    "work_order": _AS,
    "dependencies": {"type": "array", "items": _obj({"before": _S, "after": _S, "reason": _S})},
    "deliverables": {"type": "array", "items": _obj({"name": _S, "requirements": _AS, "directive_ids": _IDS, "sources": _AS})},
    "requirements": {"type": "array", "items": _obj({"text": _S, "directive_ids": _IDS, "sources": _AS})},
    "common_directive_groups": {"type": "array", "items": _obj({"summary": _S, "directive_ids": _IDS})},
    "supplements": {"type": "array", "items": _obj({"description": _S, "directive_ids": _IDS})},
    "corrections": {"type": "array", "items": _obj({"description": _S, "directive_ids": _IDS, "evidence": _S})},
    "contradictions": {"type": "array", "items": _obj({"description": _S, "directive_ids": _IDS})},
    "open_questions": _AS,
    "explanation": _S,
})
CORPUS_INSTRUCTION = """## 作業（複数資料を横断した実験全体の統合理解）
複数の動画・PDF・Excel説明・レポート説明・事前資料の理解結果と指示一覧を横断し、実験全体として
目的・内容・手順・データ処理・Excel作成方法・レポート作成方法・作業の順序と依存関係・最終的な提出物と要件を理解してください。
- 同じ指示が複数資料にあれば common_directive_groups で関連付ける（指示は #番号で参照）。
- 資料間の補足・訂正・条件変更は根拠とともに記録する。資料間で矛盾する指示は、どちらかを勝手に採用せず contradictions に双方の番号を入れる。
- 各項目に根拠となる資料名（sources）を書く。
"""


def scene_prompt(source: str, scene_label: str, material: str, prev_summary: str, next_material: str) -> str:
    return (f"{SCENE_INSTRUCTION}\n## 資料\n{source}\n\n## 対象場面 {scene_label}\n{material}\n\n"
            f"## 直前の場面の理解（参考）\n{prev_summary or '（なし：最初の場面）'}\n\n"
            f"## 直後の場面の発話・内容（参考）\n{next_material or '（なし：最後の場面）'}\n")
