# CLAUDE.md

建築構造実験資料の解析ツール `structlab`（`src/structlab/`）。ユーザーは主に iPhone の Claude アプリから操作する。

## 「解析を開始して」と頼まれたら
1. `scripts/run_analysis.sh` をバックグラウンドで実行する（設定は `configs/dropbox.yaml`。AIは使わない・無料）
   - 有料のAI解析（`--allow-external-ai`）は、ユーザーの承認なしに開始しない。まず AI なしで実行し、最後に表示される
     `structlab estimate` の呼び出し回数・概算費用と、送信される内容（docs/SETUP_AI.md「送信される内容」）を伝えて承認を得てから
     `scripts/run_analysis.sh --allow-external-ai` を実行する
2. 終わったら `python3 -m structlab -c configs/dropbox.yaml status` の要点（完了数・失敗・アップロード待ち・AI使用量）を短く報告する
3. 結果は Dropbox の `/鋼材実験レポート用_解析結果/results/` にあると伝える（指示一覧は `semantic/directives.md`、
   網羅率は `semantic/coverage.md`。網羅率は「処理した時間の割合」であり「指示を漏れなく理解できた割合」ではないことも伝える）
- 認証情報（DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN, ANTHROPIC_API_KEY）が無い場合は、チャットに貼らせず、
  GitHub Secrets またはクラウド環境の環境変数に登録してもらう（docs/SETUP_DROPBOX.md 手順A。iPhone では1手順ずつ案内する）
- このセッションに認証情報が無くても、GitHub Actions 側で実行できる: `ops/requests/connection-check.txt`（接続確認・読み取りのみ）や
  `ops/requests/analyze.txt`（解析）を変更して push し、ワークフローのログを GitHub のツールで読んで報告する。
  analyze.txt の `allow_external_ai: true` は有料なので、見積もりを伝えて承認を得てからにする
- Dropbox のリフレッシュトークンは `ops/requests/dropbox-auth.txt`（`mode: preflight` → 本人が許可してコードを Secret
  `DROPBOX_AUTH_CODE` に登録 → `mode: exchange`）で取得し、Actions が `DROPBOX_REFRESH_TOKEN` に登録する（PAT `GH_SECRETS_TOKEN` を使用）
- トークンやキーの値を表示・保存・コミットしない（`cat` で中身を見ない。接続確認の出力は伏せ字になる）
- 途中で止まっても同じコマンドで続きから再開できる

## チェック・再検証・文章スタイル・フィードバック
- チェックは全体設計（`src/structlab/checks/catalog.py`、要件 R1〜R15 × 工程）に沿って各工程に組み込んである。
  新しいチェックを足すときはカタログに登録する（未登録だとテストが失敗する）
- 結果は `results/checks/catalog.md`（要件×工程と最新の結果）と `checks.md`（明細）。「未検証」は成功ではないと伝える
- `structlab reverify` は規則のみなら無料。`--allow-external-ai` を付ける場合は有料なので事前に承認を得る
- 本人の文章スタイルPDFは最終工程（確定した文章の表現調整）にだけ使う。解析・指示判断・計算・評価には使わない。
  PDFと `work/style/profile.json` は個人情報なのでコミットしない（Dropbox の解析対象フォルダの外か作業ディレクトリに置く）
- 教授のフィードバックは `structlab feedback add` で受け取ったまま記録し、`structlab trace` で原因の工程を一緒に確認する。
  フィードバックを受け取る前に、その内容を推測して実装しない
- 話者識別はしていない。動画の音声を「先生の指示」と断定しない（利用者が確認した場合のみ `semantic.video_speaker: teacher`）

## 守ること
- 実際の資料・解析結果（`work/`）・`config.yaml`・`.env` をコミットしない
- 元資料フォルダ（`/鋼材実験レポート用`）には書き込まない
- 実験レポートや Excel の提出物は作成しない（ユーザーの指示）

## 開発
- テスト: `python3 -m pytest -q`（ダミーデータを自動生成。`-m "not slow"` で短縮）
- 新しいAIプロバイダ: `src/structlab/ai/base.py` の `AIProvider` を実装し `ai/__init__.py` の `make_provider` に登録
- 新しいファイル形式: `src/structlab/processors/` に `Processor`（plan / extract）を追加し `processors/__init__.py` に登録
