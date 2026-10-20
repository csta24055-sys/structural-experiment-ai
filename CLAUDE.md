# CLAUDE.md

建築構造実験資料の解析ツール `structlab`（`src/structlab/`）。ユーザーは主に iPhone の Claude アプリから操作する。

## 「解析を開始して」と頼まれたら
1. `scripts/run_analysis.sh` をバックグラウンドで実行する（設定は `configs/dropbox.yaml`）
   - ユーザーが外部AI（Claude）での解析を明示的に許可した場合のみ `--allow-external-ai` を付ける。許可が不明なら付けずに実行し、付ければ何が送信されるか（docs/SETUP_AI.md「送信される内容」）を伝えて確認する
2. 終わったら `python3 -m structlab -c configs/dropbox.yaml status` の要点（完了数・失敗・アップロード待ち・AI使用量）を短く報告する
3. 結果は Dropbox の `/鋼材実験レポート用_解析結果/results/knowledge.md` にあると伝える
- 認証情報（DROPBOX_APP_KEY, DROPBOX_REFRESH_TOKEN, ANTHROPIC_API_KEY）が無い場合は、チャットに貼らせず、クラウド環境の設定に環境変数として登録してもらう（docs/SETUP_DROPBOX.md）
- 途中で止まっても同じコマンドで続きから再開できる

## 守ること
- 実際の資料・解析結果（`work/`）・`config.yaml`・`.env` をコミットしない
- 元資料フォルダ（`/鋼材実験レポート用`）には書き込まない
- 実験レポートや Excel の提出物は作成しない（ユーザーの指示）

## 開発
- テスト: `python3 -m pytest -q`（ダミーデータを自動生成。`-m "not slow"` で短縮）
- 新しいAIプロバイダ: `src/structlab/ai/base.py` の `AIProvider` を実装し `ai/__init__.py` の `make_provider` に登録
- 新しいファイル形式: `src/structlab/processors/` に `Processor`（plan / extract）を追加し `processors/__init__.py` に登録
