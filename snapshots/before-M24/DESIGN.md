# 全体設計書（before-M24 / コミット 9880681）

関数ごとの一覧は `MODULES.md`、利用者向けの詳しい説明は `source/docs/READING.md`・`source/docs/SEMANTIC.md`・
`source/docs/CHECKS.md`・`source/docs/COSTS.md` にあります。この文書は「全体がどうつながっているか」と
「コードだけでは分からない前提」をまとめます。

## 1. 設計思想

1. **読むだけ・書かない**: 元資料（Dropbox）は読み取り専用。結果は作業フォルダ（`work/`）か GitHub Actions の成果物（Artifacts、30 日）に置く
2. **無料の処理を先に、有料の処理は許可制**: ffmpeg・OCR（Tesseract）・文字起こし（faster-whisper）などローカルの処理で
   できることを先に全部行い、AI（Claude）に送る量を見積もる。AI は `--allow-external-ai`（Actions では
   `ops/requests/reading.txt` の `allow_external_ai: true`）があるときだけ呼ぶ
3. **成功と言えないものは成功にしない**: 照合元が無い書き起こしは「未照合」、AI が読めなかった箇所は「未確認」、
   規則だけの指示は「AI による理解は未生成」と明示する
4. **再開できる**: 段階ごとに結果をファイル／SQLite に確定させる。Batch API の送信 ID も保存し、中断後に二重送信しない。
   依頼の内容の指紋（`request_hash`）が同じときだけ保存済みの AI 結果を使い直す
5. **AI に届く情報を減らす削減は、比較で同等と確かめるまで使わない**（M19 以降）

## 2. 対象資料（[確認済み] v7 の plan ジョブ・棚卸し）

| フォルダ | 種類 | 役割（configs の reading.roles） |
|---|---|---|
| 事前説明1 | 画面録画 MP4（講義スライド＋音声） | 既定（場面分割＋代表画面） |
| 事前説明2 | 画面録画 MP4 | 既定 |
| 事前説明画像1/2 ほか | PDF（同じ内容が 3 部重複。代表 1 部だけ読む）＋別の PDF | PDF |
| 鋼材実験経過先生1 / 先生2 | 画面録画（先生側の実験の録画を再生している画面） | experiment（15 秒区間×3 枚、末尾除外、1→2 の順で連結） |
| 鋼材実験経過生徒1 | iPhone のカメラ動画 MOV（表面・裏面を交互に撮影） | experiment（表裏の判定） |
| エクセルの作り方1 | 画面録画（ブラウザ内の動画プレーヤーで Excel 操作の動画） | lecture_detail（最高画質・細部まで） |
| レポートの作り方1 / 2 | 画面録画（レポート作成の操作） | lecture_detail |

正確なファイル名は v7 の実行のジョブ名（例: `鋼材実験経過先生1/ScreenRecording_10-21-2026 06-19-06_1.MP4`）を参照。
元資料はこの保存データに含めない（大容量・個人の資料）。取得元は Dropbox `/鋼材実験レポート用`、Actions は
Secrets の Dropbox 認証で読み取る。

## 3. 処理の流れ（GitHub Actions「Read materials」= `.github/workflows/reading.yml`）

```
plan（1 ジョブ）
  structlab read --list-targets → 読む資料と、キャッシュの鍵 {path, base, key}
      key = v<READ_VERSION>-<内容ハッシュ16桁>-<役割設定のハッシュ>（設定を変えた役割の資料だけ読み直す）
read（資料ごと、最大 6 並列、各 355 分まで）
  1. Dropbox から 1 ファイルを取得（読み取りのみ）
  2. Actions のキャッシュ（key、無ければ base）から前回の読み取り結果を復元
  3. structlab read --only <path> [--allow-external-ai] [--count-tokens]
  4. 結果を Actions のキャッシュに保存（派生画像 frames/derived は除く）、成果物としてアップロード
summary（1 ジョブ）
  1. 全資料の結果を結合（reading/merge.py）→ summary_all.md（見積もり・実測トークン）
  2. 資料横断の整理（reading/corpus.py）
  3. 意味理解（reading/semantic_bridge.py → semantic/engine.py）。DB は Actions のキャッシュで引き継ぐ
  4. 費用の内訳（structlab cost）→ corpus/costs/costs.json
  5. 要求事項の台帳の照合（structlab requirements verify --stage analysis）
```

`scripts/run_analysis.sh` と `.github/workflows/analyze.yml` は、読み取り層より前に作った「解析パイプライン」
（`pipeline.py`、Dropbox の結果フォルダへの同期つき）。現在の本番は上の Read materials を使う。

## 4. 読み取り層（`src/structlab/reading/`）

### 4.1 動画（`video_read.py` + `media.py`）
- **入れ直し（remux）**: 再圧縮せず MKV へ（目次の不完全な録画でのシームを速くする）。普通の音声トラックを 1 本だけ。失敗したら元ファイル
- **切り出し領域**: 画面録画から資料の部分（スライド・動画プレーヤーの中）を検出（`media.content_box`。暗いプレーヤーの中の入れ子も）。
  **画面ごとに安全確認**（`crop_is_safe`）: 切り出し範囲の外に普段と違う表示（通知・コントロールセンター等）があればその画面は全体で保存（`_full`）
- **回転**: 端末を回して録画した区間を検出して正しい向きに（`ui_orientation`・`orientation_runs`）
- **場面**: 1 秒（lecture_detail は 0.5 秒）ごとの変化で分割、同じ画面に戻ったら `same_as`
- **lecture_detail（Excel・レポートの作り方）**: 元の解像度の可逆 WebP で保存。0.2 秒ごとの再走査（`dense_changes`）で短時間の表示も保存
  （0.6 秒以内に終わる変化は全部、続く変化は始め・終わり・1 秒ごと）
- **experiment**: 15 秒区間ごとに 3 枚（始め+0.5 秒、代表、終わり−0.5 秒）。先生側は末尾で録画冒頭の画面に戻った以降を除外
  （無音＋大きな切り替わり＋冒頭と一致の 3 条件。根拠の数値を `trim` に記録）
- **文字起こし**: faster-whisper large-v3-turbo（lecture_detail は large-v3）。用語の手がかりに PDF の語を与える。音声の無い時間は作らない
- **OCR**: Tesseract（jpn）で代表画面を読む（照合用。誤りを含む）

### 4.2 AI への依頼（`ai_read.py`、許可時のみ）
| task | 単位 | 画像 | 備考 |
|---|---|---|---|
| `read_pdf_page` | PDF 1 ページ | ページ画像 | テキスト層も添える |
| `read_video_scene` | 動画の場面 | 代表画面 | 発話・OCR を添える |
| `read_experiment_windows` | 実験 4 区間（`group: 4`） | 各区間 3 枚＋基準画像（キャッシュ） | 面・ひび割れ・加力・計測表示・操作・不確実な点 |
| `read_video_detail` | lecture_detail の 12 個の変化ごと | 全体 2 倍・内容のタイル 3 倍・変化部分 3 倍 | 発話の要点・表示された設定・拡大の要求 |
| `read_video_zoom` | 第 1 段で AI が求めた範囲（10 個ずつ） | 元の画素から 4 倍 | 読めないものは未確認 |
| `fix_speech` | 発話 120 区間 | なし | 用語だけの訂正（資料に無い語への訂正は不採用） |

- 出力は JSON スキーマで構造化。結果は `files/<資料>/ai/<key>.json` に保存（`req_hash` 付き）
- Batch API（50% 引き）。`batch_state.json` に送信 ID を保存して再開可能
- `count_tokens`（無料）で入力トークンを実測できる

### 4.3 照合（`verify.py`）と資料横断（`corpus.py`）
- PDF: テキスト層の行の取りこぼし、数値の読み違い。動画: OCR、同じ内容の PDF ページのテキスト層
- 資料横断: 内容の重なり・差分、先生 1→2 の連結時間軸（欠けた時間は補完しない）、生徒の表裏、先生と生徒の対応（時計ではなく内容で。AI 1 回）

### 4.4 データ形式: `files/<資料>/reading.json`（主なキー）
- 共通: `path`, `kind`(pdf/video), `content_hash`, `size`, `read_version`, `complete`, `role`
- PDF: `pages[]` = {`page`, `text_layer`, `rows`, `render`(画像), `needs_vision`, `ai`}
- 動画: `duration`, `has_audio`, `valid_end`（末尾除外後の終わり）, `trim`（除外の根拠）, `segments[]`（向き・切り出し領域・`dense_scan`）,
  `speech[]` = {`start`,`end`,`text`}, `speech_fix`, `scenes[]` = {`start`,`end`,`mode`(slide/operations/experiment/detail),
  `frame`, `frame_time`, `frames`/`events`（lecture_detail の各変化と画像）, `ocr`, `same_as`, `ai`, `speech`, `pdf_match`}
- `verification[]`, `ocr_vs_pdf`

## 5. 意味理解層（`src/structlab/semantic/`、詳細は `source/docs/SEMANTIC.md`）

- `semantic_bridge.load_records`: reading.json を SQLite（`files`/`units`/`timeline`）に入れる。動画は 120 秒ごとの「場面」。
  場面の材料（`ai_text`）は読み取り工程の AI の書き起こし（無ければ OCR）。PDF はページ単位。
  読み取り結果の指紋（`reading_fingerprint`）を版にし、同じなら途中から続ける
- 規則（無料）: `rules.py` で言い回しから指示候補を抽出（A 禁止〜H その他の 8 分類）
- AI（許可時）: 第1回 場面の理解（`scene`）→ 全体の理解（`synthesis`）→ 再解釈（`reinterpret`、最大 2 回）、
  第2回 指示の抽出（`directives`、規則の候補を全件確認）、第3回 照合（`verify`、1 資料 40 件まで。画像つき）、
  資料横断の統合（`corpus`）
- `BatchGate`: 資料ごとのスレッドからの依頼を集め、全員が待ちになったら Batch でまとめて送る（順序依存は保つ）
- 出力: `semantic/directives.md|json|csv`, `understanding/`, `scenes/`, `coverage.md`（網羅率は「処理した時間の割合」）, `conflicts.md`, `unconfirmed.md` など
- DB の主な表: `sem_docs`, `sem_scenes`, `sem_steps`, `sem_understanding`(scope=document/corpus), `directives`, `directive_links`

## 6. 要求事項の台帳（`requirements.py` + `requirements/process.yaml`）
- `process.yaml`: 利用者の指示ごとの出典（M0〜M23）と項目（P001〜、94 件）。工程（reading/semantic/analysis/excel/report）・種類・確認方法（`auto:<検査名>` か手作業）
- 照合の状態: 成功 / 不一致 / 要確認 / 未検証 / 未実施 / 矛盾 / 対象外。**成功以外は成功と報告しない**
- `verify --stage analysis`: 解析の完了時。自動検査（例: 先生の末尾除外の根拠があるか、モデルが最上位か、2,350 円構成か、候補が既定で無効か）
- `checklist/verify --stage excel|report`: 作成段階で同じ台帳＋先生の指示＋読めなかった箇所を再照合（記録場所の無い「成功」は要確認）

## 7. AI プロバイダ（`src/structlab/ai/`）
- `AIRequest`（task, text, system, images, schema, cache 指定, `cache_prefix_chars`）→ `AIResponse`
- `anthropic_provider.py`: Messages API。画像は WebP 可逆（3.7 MB を超えると JPEG q97 4:4:4）。キャッシュは 5 分（ephemeral）。
  16,000 を超える max_tokens はストリーミング。Batch（`analyze_batch`）。`count_input_tokens`
- モデル: `claude-opus-5-5`、effort high、思考は adaptive。料金 入力 $4 / 出力 $20（100 万トークン）、Batch で半額
- テストは `mock_provider.py` / `mock_semantic.py`（決まった応答）

## 8. 費用（`costmodel.py`・`pilot.py`、詳細は COSTS.md）
- 読み取り: `ai_read.estimate_plan`（画像サイズから画像トークン、仮定の出力）、`count_tokens` で入力を実測
- 理解・統合: 場面の数・文字数は実測、AI の出力量などは low/mid/high の仮定
- `pilot.py`: 候補（C1 先頭キャッシュ配置、C2 短い書き方、C4 照合のまとめ、C3 実験区間のまとめの確認）を、
  今の方式・今の方式の再実行・候補の 3 つで比べ、再実行の揺れ以内なら採用

## 9. コードだけでは分からない前提
- [確認済み] 先生2・エクセル・レポート2 はブラウザの動画プレーヤーの中の映像。切り出さないと資料の部分が小さく送られる
- [確認済み] 生徒の MOV には MKV に入れられない音声トラックがある（入れ直しは普通の音声 1 本だけ）
- [確認済み] 一部の録画は途中で解像度・向きが変わる（切り出しは向きの区間ごと、範囲は画面内に収める）
- [確認済み] 先生1 は 1797.0 秒、先生2 は 1220.0 秒以降が冒頭画面への戻り（無音）で除外（根拠つき）
- [推定] 出力トークン（思考を含む）は費用の大きな部分だが、実行するまで測れない
- [未確認] Batch の中でのキャッシュの当たり率（公式に保証されない）
- 話者識別はしていない（`semantic.video_speaker: unknown`）
