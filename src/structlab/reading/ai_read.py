"""AI による画像の読み取り（有料・明示的な許可があるときだけ）: PDF のページ・動画の場面を、見えるとおりに構造化して書き起こす。

解釈・要約ではなく「書き起こし」が目的。数値・記号は画像のとおりに写し、グラフから読んだ値は概算（approx）と明記させる。
ローカルで得た文字（PDF のテキスト層・OCR）は手がかりとして渡すが、結果はあとで verify.py が独立に照合する。
"""
from __future__ import annotations

from pathlib import Path

from ..ai.base import AIImage, AIRequest

SYSTEM = """あなたは建築構造実験の授業資料（PDFのページ、講義動画の画面）を正確に書き起こす担当者です。
解釈・要約・補足はせず、画像に見えている内容だけを構造化して書き起こしてください。

厳守:
- 文字・数値・単位・記号は画像のとおりに写す（全角/半角・上付き・下付きもできるだけ保つ。勝手に丸めない・換算しない）
- 読めない・判別できない箇所は推測で埋めず unreadable に書く
- 表は行と列の対応を保って書き起こす（結合セルは同じ値を繰り返さず、見出しの位置が分かるように）
- 数式は latex と、日本語の平文（plain）の両方で書く
- グラフ: 軸ラベル・単位・目盛りの範囲・凡例・注記を書き起こす。曲線から読んだ値は values_read に approx=true で書く
  （目盛りや注記として印字されている値は approx=false）
- 図・写真: 何が写っているかを客観的に description に書き、図中の文字・寸法・記号は labels にすべて写す
- 動画の画面: 再生アプリやブラウザの表示（時計、URL、ボタン）は書き起こさない。資料の部分だけ
- 操作の画面（Excel など）が続けて渡された場合は、画面の差から行われた操作を operations に時刻付きで書く
  （例: セル B3 に =A3*2 を入力、グラフの種類を散布図に変更）。見て分からない操作は推測しない
- 参考として渡すテキスト（PDFの文字情報・OCR）は誤りを含むことがある。画像と食い違う場合は画像を優先し、notes に書く"""

_STR = {"type": "string"}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "text_blocks", "tables", "formulas", "figures", "operations", "unreadable", "notes"],
    "properties": {
        "title": _STR,
        "text_blocks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "role"],
            "properties": {"text": _STR, "role": {"type": "string", "enum": [
                "heading", "body", "bullet", "label", "caption", "callout", "note", "other"]}}}},
        "tables": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["caption", "header", "rows", "notes"],
            "properties": {"caption": _STR, "header": {"type": "array", "items": _STR},
                           "rows": {"type": "array", "items": {"type": "array", "items": _STR}}, "notes": _STR}}},
        "formulas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["latex", "plain", "where"],
            "properties": {"latex": _STR, "plain": _STR, "where": _STR}}},
        "figures": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "caption", "description", "axes", "legend", "labels", "values_read"],
            "properties": {
                "kind": {"type": "string", "enum": ["graph", "diagram", "drawing", "photo", "table_image", "other"]},
                "caption": _STR, "description": _STR,
                "axes": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["axis", "label", "unit", "range"],
                    "properties": {"axis": _STR, "label": _STR, "unit": _STR, "range": _STR}}},
                "legend": {"type": "array", "items": _STR},
                "labels": {"type": "array", "items": _STR},
                "values_read": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["what", "value", "approx"],
                    "properties": {"what": _STR, "value": _STR, "approx": {"type": "boolean"}}}}}}},
        "operations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["time", "action", "target", "value"],
            "properties": {"time": _STR, "action": _STR, "target": _STR, "value": _STR}}},
        "unreadable": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["where", "reason"],
            "properties": {"where": _STR, "reason": _STR}}},
        "notes": _STR,
    },
}

#: 1回あたりの概算トークン（見積もり用。画像は 1568px 程度に縮小して送る）
EST_IMAGE_TOKENS = 1600
EST_FIXED_INPUT = 2200     # 指示文・スキーマ
EST_OUTPUT = {"pdf_page": 1500, "video_scene": 1100, "video_ops": 1300, "speech_fix": 1200}
EST_SPEECH_INPUT = 6000    # 文字起こし 120 区間＋用語


def pdf_page_request(file_label: str, page: dict) -> AIRequest:
    hint = "\n".join(page["rows"])[:6000]
    text = (f"# 資料: {file_label} の {page['page']} ページ\n"
            f"## 参考: PDF の文字情報（位置から見た目の行にまとめたもの。図・写真の中の文字は含まれない）\n{hint or '（なし）'}\n\n"
            "このページの画像を、指示どおりに書き起こしてください。")
    return AIRequest(task="read_pdf_page", source_label=f"{file_label} p.{page['page']}", text=text,
                     images=[AIImage(Path(page["render"]), f"p.{page['page']}")], system=SYSTEM, schema=SCHEMA,
                     max_tokens=6000)


def video_scene_request(file_label: str, scene: dict, fmt_time) -> AIRequest:
    imgs = [AIImage(Path(scene["frame"]), f"{fmt_time(scene['frame_time'])} の画面（場面の代表）")]
    for e in scene.get("events_with_frames", []):
        imgs.append(AIImage(Path(e["frame"]), f"{fmt_time(e['time'])} の画面（場面内の変化）"))
    ocr = (scene.get("ocr") or "")[:3000]
    text = (f"# 資料: {file_label} の動画 {fmt_time(scene['start'])}–{fmt_time(scene['end'])} の場面\n"
            f"## 参考: OCR（誤りを含む）\n{ocr or '（なし）'}\n\n"
            + ("最初の画像が場面の代表画面、続く画像は同じ場面の中で画面が変化した時点です。操作があれば operations に書いてください。\n"
               if len(imgs) > 1 else "")
            + "画面の資料部分を、指示どおりに書き起こしてください。")
    return AIRequest(task="read_video_scene", source_label=f"{file_label} {fmt_time(scene['start'])}", text=text,
                     images=imgs, t_start=scene["start"], t_end=scene["end"], system=SYSTEM, schema=SCHEMA,
                     max_tokens=6000)


def estimate_calls(records: dict[str, dict]) -> dict:
    """ローカルの読み取り結果から、AI の呼び出し回数と概算トークンを数える（APIは呼ばない）。"""
    calls = {"pdf_page": 0, "video_scene": 0, "video_ops": 0, "speech_fix": 0}
    images = 0
    for rec in records.values():
        if rec["kind"] == "pdf":
            calls["pdf_page"] += rec["n_pages"]
            images += rec["n_pages"]
        elif rec["kind"] == "video":
            for sc in rec["scenes"]:
                if sc.get("same_as") is not None:
                    continue
                n_ev = len(sc.get("events_with_frames", []))
                calls["video_ops" if n_ev else "video_scene"] += 1
                images += 1 + n_ev
            calls["speech_fix"] += len(speech_chunks(rec.get("speech", [])))
    n = sum(calls.values())
    n_img = n - calls["speech_fix"]
    in_tok = n_img * EST_FIXED_INPUT + images * EST_IMAGE_TOKENS + n_img * 600 + calls["speech_fix"] * EST_SPEECH_INPUT
    out_tok = sum(EST_OUTPUT[k] * v for k, v in calls.items())
    return {"calls": calls, "total_calls": n, "images": images, "input_tokens": in_tok, "output_tokens": out_tok}


# ---------------- 文字起こしの専門用語の訂正（テキストのみ・画像なし） ----------------
SPEECH_SYSTEM = """あなたは建築構造実験の講義音声の文字起こしを校正する担当者です。
音声認識が専門用語を同音・類音の別の語に誤っている箇所だけを直してください（例: 梁→「針」、加力→「火力」）。

厳守:
- 直してよいのは、渡した「資料に出てくる用語」にある語への訂正だけ。用語リストに無い語へは直さない
- 言い回し・文法・句読点・話し言葉は直さない。内容を補ったり要約したりしない
- 判断できない箇所は直さず uncertain に書く
- original は該当区間の文字をそのまま（部分文字列として）写す"""

SPEECH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["corrections", "uncertain"],
    "properties": {
        "corrections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["segment", "original", "corrected", "reason"],
            "properties": {"segment": {"type": "integer"}, "original": _STR, "corrected": _STR, "reason": _STR}}},
        "uncertain": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["segment", "text", "reason"],
            "properties": {"segment": {"type": "integer"}, "text": _STR, "reason": _STR}}},
    },
}
SPEECH_CHUNK = 120


def speech_chunks(speech: list[dict]) -> list[tuple[int, list[dict]]]:
    return [(i, speech[i:i + SPEECH_CHUNK]) for i in range(0, len(speech), SPEECH_CHUNK)]


def speech_fix_request(file_label: str, start_index: int, segs: list[dict], vocab: str, fmt_time) -> AIRequest:
    body = "\n".join(f"[{start_index + k}] ({fmt_time(s['start'])}) {s['text']}" for k, s in enumerate(segs))
    text = (f"# 資料: {file_label} の音声の文字起こし（区間 {start_index}〜{start_index + len(segs) - 1}）\n"
            f"## 資料（スライド・PDF）に出てくる用語\n{vocab[:4000]}\n\n## 文字起こし\n{body}\n\n"
            "指示どおりに、専門用語の誤認識だけを訂正してください。")
    return AIRequest(task="fix_speech", source_label=f"{file_label} 音声", text=text, system=SPEECH_SYSTEM,
                     schema=SPEECH_SCHEMA, max_tokens=4000)
