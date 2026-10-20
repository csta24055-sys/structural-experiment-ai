# Dropbox 接続設定

このツールは Dropbox API を直接使います（Claude アプリの Dropbox コネクタとは別物です）。
必要なのは **アプリキー** と **リフレッシュトークン** の2つで、どちらも環境変数で渡します。

## 1. Dropbox アプリを作る（1回だけ・ブラウザで可）

1. https://www.dropbox.com/developers/apps を開き「Create app」
2. 「Scoped access」→ アクセス範囲は **Full Dropbox**
   （資料が `/鋼材実験レポート用` という Dropbox 直下のフォルダにあるため。App folder を選ぶ場合は資料をアプリ用フォルダへ移す必要があります）
3. 名前は任意（例: `structlab-yourname`）
4. 作成後「Permissions」タブで次にチェックして **Submit**
   - `files.metadata.read`（一覧の取得）
   - `files.content.read`（ダウンロード）
   - `files.content.write`（**結果と state.db を出力フォルダへ保存する場合のみ**）
     - プログラム側で出力フォルダ（`output.dropbox_path`）以外へは書き込まないよう制限しています
     - 完全に読み取り専用で運用したい場合はチェックせず、`output.dropbox_path: ""` にしてください（その場合、クラウドの使い捨て環境では結果と再開情報が残りません。PC 実行向け）
5. 「Settings」タブの **App key** を控える（App secret は PKCE 方式では不要）

> 権限を後から変更した場合は、トークンを取り直す必要があります。

## 2. リフレッシュトークンを取得する

リフレッシュトークンは長期間有効な秘密情報です。**チャットや GitHub に貼らないでください。**

### 方法1: PC のターミナル（推奨）

```bash
export DROPBOX_APP_KEY=（App key）
python3 -m structlab dropbox-auth start
#  → 表示された URL をブラウザで開き「許可」→ 表示されたコードをコピー
python3 -m structlab dropbox-auth finish （コード）
#  → ~/.config/structlab/dropbox_refresh_token に保存されます（権限600）
cat ~/.config/structlab/dropbox_refresh_token   # 値を環境変数の設定画面に貼り付ける
```

### 方法2: iPhone だけで行う（未検証）

iPhone のターミナルアプリ（a-Shell など Python が動くもの）で方法1と同じ操作を行えます。
ブラウザで URL を開いて許可し、表示されたコードを `finish` に渡してください。

## 3. 実行環境に設定する

| 変数名 | 値 |
|---|---|
| `DROPBOX_APP_KEY` | App key |
| `DROPBOX_REFRESH_TOKEN` | 手順2で得たリフレッシュトークン |
| `DROPBOX_APP_SECRET` | （任意）PKCE で取得した場合は不要 |

- **Claude Code on the web**: セッション画面上部のクラウド環境メニュー → 編集 → 環境変数（またはシークレット）に追加。新しいセッションから有効になります
- **GitHub Actions**: リポジトリの Settings → Secrets and variables → Actions → New repository secret に同じ名前で登録
- **PC**: `.env.example` を `.env` にコピーして値を入れ、`set -a; source .env; set +a`

短期のアクセストークン（App Console の「Generate access token」、約4時間で失効）を `DROPBOX_ACCESS_TOKEN` に入れても動きますが、長時間の処理では途中で失効するため推奨しません。

## 4. 確認

```bash
python3 -m structlab -c configs/dropbox.yaml check
```

`[OK ] Dropbox 接続・フォルダ '/鋼材実験レポート用' の読み取り` と出れば接続できています。
`check` は一覧の先頭数件を読むだけで、何も書き込みません。

## 5. 対象フォルダの変更

`configs/dropbox.yaml`（または自分の `config.yaml`）の `source.path` を変更してください。
結果の保存先 `output.dropbox_path` は、解析対象フォルダの **外側** にする必要があります。
