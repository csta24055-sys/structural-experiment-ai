"""AIへの指示文と出力スキーマ。"""
from __future__ import annotations

from ..models import CATEGORIES

SYSTEM_PROMPT = """あなたは建築構造実験（鋼材の引張試験など）の授業資料を整理する補助者です。
与えられた「資料の一部分」（PDFの1ページ、動画の一区間、Excelのシート範囲、画像1枚など）だけを根拠に、
学生がレポートとExcelを作成するために必要な情報を抽出し、指定のJSONで返してください。

厳守事項:
- 資料・画面・音声に実際に現れている内容だけを書く。読み取れない部分を推測で補完しない。
- 読み取れない・判読が不確かな部分は unreadable に記述する（例:「右下の表の数値が不鮮明」）。
- 各 item の evidence には、根拠となる原文（資料の文字、画面の文字、発言）をできるだけそのまま引用する。
- origin の区別:
  document_text = 資料（PDF・画像・Excel）に書かれている
  speech        = 音声で話されている
  screen        = 動画の画面に表示されている（Excel操作・数式・グラフなど）
  ai_inference  = 上記に明記されておらず、あなたが推測・補足したもの（必ずこれを使う）
- is_instructor_instruction は、先生（講師）の指示として資料・発言に明示されている場合のみ true。
  推測の場合は必ず false。
- 計算式は可能な限り原文の記号で書き、Excelの数式は「=AVERAGE(B2:B10)」のようにセル参照も書く。
- グラフは種類・縦軸・横軸・系列・近似曲線などを書く。
- 実験レポートやExcelの完成品は作らない。抽出と整理だけを行う。
- 他の部分との矛盾や不明点に気付いたら category を unknowns_contradictions にする。
- 動画では time_sec に該当時刻（動画先頭からの秒）を入れる。それ以外は null。
- 重要な場面（先生が強調・注意・提出要件を述べる、Excelの重要操作など）は important_moments に時刻と理由を書く。
- 出力は日本語。
"""

CATEGORY_GUIDE = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())

TASK_HINTS = {
    "pdf_page": "PDFの1ページです。本文・図・表・数式を取りこぼさないでください。図や表は何を示すかも書いてください。",
    "image": "画像1枚です。画像内の文字・図・数式・表を読み取ってください。",
    "video_segment": ("動画の一区間です。添付フレーム（時刻付き）と文字起こし（時刻付き）を対応付けて、"
                      "Excelの操作手順、画面内の文字、計算式、グラフ、先生の指示を抽出してください。"),
    "video_focus": ("動画の重要場面を高解像度・高頻度で切り出したものです。Excelのセル番地・数式・"
                    "メニュー操作・グラフ設定を詳細に読み取ってください。"),
    "excel_sheet": "Excelシートの一部（セル・数式・値）です。表の構造、計算式の意味、データの種類を整理してください。",
    "excel_workbook": "Excelブックの構造（シート一覧・グラフ定義・名前定義）です。グラフの作り方を整理してください。",
    "office_part": "Word/PowerPointの一部分です。本文・図・数式を取りこぼさないでください。",
    "text_chunk": "テキストファイルの一部分です。",
}

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES.keys())},
                    "statement": {"type": "string"},
                    "origin": {"type": "string",
                               "enum": ["document_text", "speech", "screen", "ai_inference"]},
                    "is_instructor_instruction": {"type": "boolean"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "time_sec": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                },
                "required": ["category", "statement", "origin", "is_instructor_instruction",
                             "evidence", "confidence", "time_sec"],
                "additionalProperties": False,
            },
        },
        "unreadable": {"type": "array", "items": {"type": "string"}},
        "important_moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"time_sec": {"type": "number"}, "reason": {"type": "string"}},
                "required": ["time_sec", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items", "unreadable", "important_moments"],
    "additionalProperties": False,
}


def build_user_text(task: str, source_label: str, text: str) -> str:
    return (
        f"## 分類カテゴリ\n{CATEGORY_GUIDE}\n\n"
        f"## 対象\n{source_label}\n\n"
        f"## 作業\n{TASK_HINTS.get(task, '')}\n\n"
        f"## ローカル抽出結果（機械抽出のため誤りを含む可能性あり）\n{text or '（抽出テキストなし）'}\n"
    )
