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
- 認証情報（DROPBOX_APP_KEY, DROPBOX_REFRESH_TOKEN, ANTHROPIC_API_KEY）が無い場合は、チャットに貼らせず、クラウド環境の設定に環境変数として登録してもらう（docs/SETUP_DROPBOX.md）
- 途中で止まっても同じコマンドで続きから再開できる

## チェック・再検証・文章スタイル・フィードバック
- 各工程のチェック結果は `results/checks/checks.md`。「未検証」は成功ではないと伝える
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
