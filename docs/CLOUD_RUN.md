# iPhone からクラウドで実行する

iPhone は操作と結果の閲覧だけに使い、解析はクラウド上で行います。
処理状況（`state.db`）と結果は Dropbox の出力フォルダに保存されるので、どの方式でも途中から再開できます。

## A. Claude Code on the web（推奨）

### 初回だけ
1. [SETUP_DROPBOX.md](SETUP_DROPBOX.md) と [SETUP_AI.md](SETUP_AI.md) に従い、クラウド環境の設定に
   `DROPBOX_APP_KEY`・`DROPBOX_REFRESH_TOKEN`・`ANTHROPIC_API_KEY` を登録
2. （任意）環境のセットアップスクリプトに次を登録すると、毎回のインストールが不要になります
   ```bash
   pip install -r requirements.txt && pip install -e . && pip install faster-whisper
   apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-jpn || true
   ```

### 資料がそろったら
Claude アプリでこのリポジトリのセッションを開き、例えば次のように頼みます。

> scripts/run_analysis.sh --allow-external-ai をバックグラウンドで実行して、終わったら status を教えて

- 外部AIを使わない場合は `--allow-external-ai` を付けません
- セッションが終了・中断しても、同じ依頼をもう一度すれば続きから再開します
- 進み具合は「structlab status を見せて」、または Dropbox アプリで `/鋼材実験レポート用_解析結果/results/status.md` を開いて確認できます
- 資料を追加でアップロードした後に同じコマンドを実行すると、新しいファイル・更新されたファイルだけを処理します

## B. GitHub Actions

`.github/workflows/analyze.yml` を使います。

1. リポジトリの Settings → Secrets and variables → Actions に `DROPBOX_APP_KEY`・`DROPBOX_REFRESH_TOKEN`・（AIを使う場合）`ANTHROPIC_API_KEY` を登録
2. GitHub アプリ（またはブラウザ）で Actions → **Analyze materials** → Run workflow
   - `allow_external_ai`: Claude による解析を行うか（既定 false）
   - `max_runtime_minutes`: 既定 330 分。GitHub の上限（6時間）より前に安全に停止し、結果と状態を Dropbox に保存
3. 時間内に終わらなかった場合は、もう一度 Run workflow を押すと続きから処理します

注意:
- 非公開リポジトリの Actions 実行時間には無料枠の上限があります（Free プランで月2,000分）。長い動画を CPU で文字起こしすると時間がかかります
- 解析結果は Dropbox にのみ保存し、GitHub（Artifacts を含む）には保存しません
- このワークフローは本リポジトリでは実際の資料・認証情報で実行していません（未検証）

## C. 自分の PC

```bash
cp .env.example .env   # 値を入れる
set -a; source .env; set +a
STRUCTLAB_CONFIG=configs/dropbox.yaml scripts/run_analysis.sh --allow-external-ai
```
