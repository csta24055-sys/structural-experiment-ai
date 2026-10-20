# AI解析サービス・文字起こしの設定

## 1. 3つの動作モード

| `ai.provider` | 外部送信 | 内容 |
|---|---|---|
| `none`（既定） | なし | ローカル抽出のみ。PDFの本文・表・数式候補、Excelのセル・数式・グラフ定義、動画の文字起こし（設定時）・キーフレーム・画面OCR、規則による10分類の候補 |
| `anthropic` | **許可した場合のみ** | 上記に加えて、ページ画像・画像・動画フレームと抽出テキストを Claude に送り、図・グラフ・Excel操作・先生の指示を読み取る |
| `mock` | なし | テスト用 |

## 2. Anthropic（Claude）を使う

1. https://console.anthropic.com/ で API キーを作成（利用料金がかかります）
2. 実行環境の環境変数 `ANTHROPIC_API_KEY` に設定（Claude Code のクラウド環境設定、GitHub Secrets、PC の `.env` など。**チャットや設定ファイルには書かない**）
3. 設定で `ai.provider: anthropic` にする（`configs/dropbox.yaml` は設定済み）
4. **送信の許可**: 次のどちらかを明示的に行ったときだけ送信します
   - 実行時に `--allow-external-ai` を付ける（推奨。実行ごとに意思表示）
   - 設定で `ai.allow_external: true` にする

許可がない場合は「AI解析: 外部AIへの送信が許可されていません」と表示し、ローカル抽出のみを行います。
後から許可して実行すると、ローカル抽出済みの単位について AI 解析だけを追加で行います（抽出はやり直しません）。

### 主な設定（`ai:`）

| キー | 既定 | 説明 |
|---|---|---|
| `model` | `claude-opus-5-5` | 使用モデル |
| `effort` | `medium` | 思考の深さ（low / medium / high / xhigh / max）。上げると精度とコストが上がる |
| `max_output_tokens` | 8000 | 1回の応答の上限 |
| `use_fallbacks` | true | 安全分類器による拒否時に、サーバー側で推奨モデルへ自動フォールバック（`server-side-fallback-2026-07-01`） |
| `max_images_per_call` | 6 | 1回に送る画像の上限 |
| `max_image_edge` | 1568 | 送信前に画像の長辺をこのピクセル数まで縮小 |
| `max_calls_per_run` | 300 | 1回の実行での呼び出し上限（超えたら残りは次回） |
| `max_input_tokens_per_run` / `max_output_tokens_per_run` | 300万 / 60万 | 1回の実行でのトークン上限 |
| `max_calls_total` | 0（無制限） | 累計の呼び出し上限 |

使用量は `structlab status` と `results/status.md` で確認できます。

### 送信される内容
- PDF: 1ページの画像（`pdf.render_dpi`）と、そのページの抽出テキスト・表・数式候補
- 画像: 縮小した画像と OCR 結果
- 動画: 区間内のキーフレーム（最大 `max_images_per_call` 枚）と時刻付き文字起こし
- Excel: セル・数式の一覧とグラフ定義（画像は送らない）
- ファイルのパス名
- 意味理解（`semantic.ai: true`）を行う場合はさらに: 場面ごとの時刻付き発話・画面の文字・キーフレーム（局所理解）、
  場面の理解結果の一覧（全体理解・再解釈）、場面と前後の発話・規則候補（指示抽出）、指示の前後を広げた発話・画面（照合。元動画から取り直した画面を含む）、
  各資料の全体理解と指示一覧（資料横断の統合）。呼び出し回数と概算費用は `structlab estimate` で事前に確認できます

## 3. 別のAIに差し替える

`src/structlab/ai/base.py` の `AIProvider` を継承し `analyze(AIRequest) -> AIResponse` を実装します。
`AIResponse.data` は `src/structlab/ai/prompts.py` の `OUTPUT_SCHEMA` に従う JSON です。
`src/structlab/ai/__init__.py` の `make_provider` に分岐を追加し、`config.py` の `validate` で名前を許可してください。
外部サービスに送信するプロバイダは `external = True` とし、`allow_external` の確認を必ず通してください。

## 4. 音声の文字起こし（`transcription:`）

| `backend` | 内容 |
|---|---|
| `none` | 文字起こししない（動画は画面のみ解析） |
| `faster_whisper` | ローカルで Whisper を実行（外部送信なし）。`pip install faster-whisper`。初回にモデル（small で約500MB）を Hugging Face から取得 |

`model` は `tiny / base / small / medium / large-v3` など。大きいほど正確で遅くなります（CPU では small 程度が現実的）。
日本語の講義なら `language: ja`。

## 5. ローカルOCR（`ocr:`）

`backend: auto` で、`tesseract` コマンドがあれば画像・スキャンPDF・動画フレームの文字を読み取ります（`tesseract-ocr` と `tesseract-ocr-jpn` を OS に導入）。
OCR 結果は AI の根拠照合にも使われます。
