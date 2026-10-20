# iPhone からクラウドで実行する

iPhone は操作と結果の閲覧だけに使い、解析はクラウド上で行います。
処理状況（`state.db`）と結果は Dropbox の出力フォルダに保存されるので、どの方式でも途中から再開できます。

## A. Claude Code on the web（推奨）

### 初回だけ
1. [SETUP_DROPBOX.md](SETUP_DROPBOX.md) と [SETUP_AI.md](SETUP_AI.md) に従い、クラウド環境の設定の環境変数に
   `DROPBOX_APP_KEY`・`DROPBOX_APP_SECRET`・`DROPBOX_REFRESH_TOKEN`・`ANTHROPIC_API_KEY` を登録
   （GitHub Secrets とは別の場所です。GitHub Actions だけで運用する場合は不要）
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

| ワークフロー | 内容 | 起動 |
|---|---|---|
| `connection-check.yml`（Connection check） | 接続確認（読み取りのみ・課金なし） | `ops/requests/connection-check.txt` を変更して push |
| `analyze.yml`（Analyze materials） | 解析（結果は Dropbox の出力フォルダへ） | `ops/requests/analyze.txt` を変更して push |
| `tests.yml` | ダミーデータでのテスト | push のたび |

- いずれも既定ブランチ（main）に取り込んだ後は、Actions タブ → Run workflow でも起動できます
  （GitHub の仕様で、Run workflow ボタンは既定ブランチにあるワークフローにしか出ません。それまでは push で起動します）
- push による起動は Claude Code が代わりに行い、ログも読んで報告できます。利用者の操作は Secrets の登録だけです

1. Secrets を登録（[SETUP_DROPBOX.md](SETUP_DROPBOX.md) 手順A-4。`DROPBOX_APP_KEY`・`DROPBOX_APP_SECRET`・`DROPBOX_REFRESH_TOKEN`、
   AIを使う場合 `ANTHROPIC_API_KEY`）
2. 接続確認（Connection check）
3. 解析結果の保存のため書き込み権限を追加（SETUP_DROPBOX.md 手順B）
4. 解析: `ops/requests/analyze.txt` に次のように書いて push
   ```
   allow_external_ai: false      # true は有料の Claude 解析。見積もり（structlab estimate）を承認した後だけ
   max_runtime_minutes: 330
   request: 2026-10-20 1回目      # 内容を変えるたびに起動する
   ```
   330 分で安全に停止し、結果と状態を Dropbox に保存します。続きは同じファイルを再度変更して push

注意:
- 非公開リポジトリの Actions 実行時間には無料枠の上限があります（Free プランで月2,000分）。長い動画を CPU で文字起こしすると時間がかかります
- 解析結果は Dropbox にのみ保存し、GitHub（Artifacts を含む）には保存しません
- Secrets の値はログ上で GitHub により伏せ字になり、プログラムも表示しません

## C. 自分の PC

```bash
cp .env.example .env   # 値を入れる
set -a; source .env; set +a
STRUCTLAB_CONFIG=configs/dropbox.yaml scripts/run_analysis.sh --allow-external-ai
```
