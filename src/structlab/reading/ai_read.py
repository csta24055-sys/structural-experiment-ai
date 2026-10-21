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
- スマホの通知のバナー・他のアプリの表示が重なっている場合、その内容は書き起こさない（個人の情報のため）。
  資料が隠れて読めない部分は unreadable に「通知で隠れている」と書く
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

#: 1回あたりの概算トークン（見積もり用。画像は長辺 1568px 程度に縮小して送る＝約 2,500 トークン）。
#: 出力には思考（thinking）の分も含む
EST_IMAGE_TOKENS = 2500
EST_FIXED_INPUT = 2600     # 指示文・スキーマ
EST_OUTPUT = {"pdf_page": 4000, "video_scene": 3000, "video_ops": 3500, "experiment_window": 3000,
              "speech_fix": 3000}
EST_SPEECH_INPUT = 7000    # 文字起こし 120 区間＋用語
EST_ESCALATION_RATE = 0.15  # 照合で要確認になり上位モデルで読み直す割合（見積もり上の仮定）


def pdf_page_request(file_label: str, page: dict) -> AIRequest:
    hint = "\n".join(page["rows"])[:6000]
    text = (f"# 資料: {file_label} の {page['page']} ページ\n"
            f"## 参考: PDF の文字情報（位置から見た目の行にまとめたもの。図・写真の中の文字は含まれない）\n{hint or '（なし）'}\n\n"
            "このページの画像を、指示どおりに書き起こしてください。")
    return AIRequest(task="read_pdf_page", source_label=f"{file_label} p.{page['page']}", text=text,
                     images=[AIImage(Path(page["render"]), f"p.{page['page']}")], system=SYSTEM, schema=SCHEMA,
                     max_tokens=16000)


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
                     max_tokens=16000)


def estimate_calls(records: dict[str, dict]) -> dict:
    """ローカルの読み取り結果から、AI の呼び出し回数と概算トークンを数える（APIは呼ばない）。
    escalation は照合で要確認になったものを上位モデルで読み直す分の見込み（EST_ESCALATION_RATE を仮定）。"""
    calls = {"pdf_page": 0, "video_scene": 0, "video_ops": 0, "experiment_window": 0, "speech_fix": 0}
    images = {k: 0 for k in calls}
    for rec in records.values():
        if rec["kind"] == "pdf":
            calls["pdf_page"] += rec["n_pages"]
            images["pdf_page"] += rec["n_pages"]
        elif rec["kind"] == "video":
            for sc in rec["scenes"]:
                if sc.get("same_as") is not None:
                    continue
                n_ev = len(sc.get("events_with_frames", []))
                if sc.get("mode") == "experiment":
                    key, extra = "experiment_window", 1  # 表面の基準画像を1枚添える
                else:
                    key, extra = ("video_ops" if n_ev else "video_scene"), 0
                calls[key] += 1
                images[key] += 1 + n_ev + extra
            calls["speech_fix"] += len(speech_chunks(rec.get("speech", [])))
    in_tok = sum((EST_FIXED_INPUT + 600) * n for k, n in calls.items() if k != "speech_fix") \
        + sum(images.values()) * EST_IMAGE_TOKENS + calls["speech_fix"] * EST_SPEECH_INPUT
    out_tok = sum(EST_OUTPUT[k] * v for k, v in calls.items())
    image_calls = sum(v for k, v in calls.items() if k != "speech_fix")
    image_in = sum(images.values()) * EST_IMAGE_TOKENS + image_calls * (EST_FIXED_INPUT + 600)
    image_out = sum(EST_OUTPUT[k] * v for k, v in calls.items() if k != "speech_fix")
    return {"calls": calls, "total_calls": sum(calls.values()), "images": sum(images.values()),
            "input_tokens": in_tok, "output_tokens": out_tok,
            "escalation": {"calls": round(image_calls * EST_ESCALATION_RATE),
                           "input_tokens": round(image_in * EST_ESCALATION_RATE),
                           "output_tokens": round(image_out * EST_ESCALATION_RATE)}}


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
                     schema=SPEECH_SCHEMA, max_tokens=12000)


# ---------------- 実験経過の動画（一定間隔の区間） ----------------
EXPERIMENT_SYSTEM = """あなたは鉄筋コンクリート梁の加力実験の経過を記録した動画を観察し、見えている事実だけを記録する担当者です。
推測や一般論で補わず、画像から確認できることだけを書いてください。

厳守:
- 試験体のどの面が映っているか: 基準画像（撮影開始時に映っていた面＝表面）と比べて、表面(front)・裏面(back)・
  両方/斜め(both)・判別できない(unknown) を判断し、根拠（形・配置・書き込み・ひび割れの一致など）を書く
- ひび割れ: 位置（支点・載荷点・中央などからの位置、上下）、向き（斜め・鉛直・水平）、新しく現れたか・伸びたかを、
  区間の最初と最後の画像を比べて書く。画像から分からないことは書かない
- 加力の様子・計測器の表示（荷重計・変位計の数値、マーカーの書き込み）は、読める数値だけを写す（approx の区別をする）
- 人の操作（ジャッキの操作、マーキング、撮影位置の移動）を書く
- 画面に重なった通知・他のアプリの表示は書かない
- 確実でない記述は uncertain に入れる"""

EXPERIMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["face", "face_evidence", "view", "specimen_state", "cracks", "loading", "readings", "actions",
                 "visible_text", "uncertain"],
    "properties": {
        "face": {"type": "string", "enum": ["front", "back", "both", "unknown", "not_specimen"]},
        "face_evidence": _STR,
        "view": _STR,
        "specimen_state": _STR,
        "cracks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["location", "direction", "change"],
            "properties": {"location": _STR, "direction": _STR,
                           "change": {"type": "string", "enum": ["new", "extended", "widened", "existing", "unclear"]}}}},
        "loading": _STR,
        "readings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["what", "value", "approx"],
            "properties": {"what": _STR, "value": _STR, "approx": {"type": "boolean"}}}},
        "actions": {"type": "array", "items": _STR},
        "visible_text": {"type": "array", "items": _STR},
        "uncertain": {"type": "array", "items": _STR},
    },
}


def experiment_window_request(file_label: str, scene: dict, fmt_time, reference: str | None,
                              who: str) -> AIRequest:
    imgs = []
    if reference:
        imgs.append(AIImage(Path(reference), "基準画像（撮影開始時に映っていた試験体の面＝表面）"))
    frames = sorted([(e["time"], e["frame"]) for e in scene.get("events_with_frames", [])]
                    + [(scene["frame_time"], scene["frame"])])
    for t, f in frames:
        imgs.append(AIImage(Path(f), f"{fmt_time(t)} の画面"))
    speech = " ".join(s["text"] for s in scene.get("speech", []))[:1500]
    text = (f"# 資料: {file_label}（{who}の実験経過動画） {fmt_time(scene['start'])}–{fmt_time(scene['end'])} の区間\n"
            "最初の画像は比較用の基準画像、続く画像はこの区間の画面（時刻順）です。\n"
            f"## 参考: この区間の音声の文字起こし（誤りを含む。画像で確認できないことは書かない）\n{speech or '（音声なし）'}\n\n"
            "指示どおりに、この区間で見えていることを記録してください。")
    return AIRequest(task="read_experiment_window", source_label=f"{file_label} {fmt_time(scene['start'])}", text=text,
                     images=imgs, t_start=scene["start"], t_end=scene["end"], system=EXPERIMENT_SYSTEM,
                     schema=EXPERIMENT_SCHEMA, max_tokens=12000)
