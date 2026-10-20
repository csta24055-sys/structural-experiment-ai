# Dropbox 接続設定

このツールは Dropbox API を直接使います（Claude アプリの Dropbox コネクタとは別物です）。
長期間の自動実行のため、**リフレッシュトークン方式**（アクセストークンを自動で取り直す）で接続します。

| 名前 | 値 | 秘密か |
|---|---|---|
| `DROPBOX_APP_KEY` | App Console の App key | 秘密扱い |
| `DROPBOX_APP_SECRET` | App Console の App secret（下の手順A-2のショートカットで取得したトークンの更新に必要） | **秘密** |
| `DROPBOX_REFRESH_TOKEN` | 手順A-2・A-3で得るリフレッシュトークン（取り消すまで有効） | **秘密** |

**どの値も、チャット・GitHub のコード・ログには貼らないでください。** 登録先は GitHub の Secrets（または Claude Code のクラウド環境の環境変数）だけです。
プログラムは値を表示せず、接続確認の出力でも秘密の値は伏せ字にします。

## 実行環境の違い

| | GitHub Actions | Claude Code on the web（このセッション） |
|---|---|---|
| 認証情報の渡し方 | リポジトリの **Secrets**（手順A-4） | クラウド環境の設定の **環境変数**（任意） |
| 接続確認 | `Connection check` ワークフロー（Claude Code が push で起動し、ログを読んで報告できる） | `structlab connect-check` |
| 解析の実行 | `Analyze materials` ワークフロー | `scripts/run_analysis.sh` |
| Secrets の登録 | 利用者が Safari で行う（Claude Code からは登録できない） | ― |

GitHub Secrets はワークフローの中でだけ使え、Claude Code のセッションからは読めません（逆も同じ）。
両方で使う場合はそれぞれに登録します。

## 手順A: iPhone だけで設定する（推奨）

### A-1. 権限を「読み取り専用」にする（Dropbox App Console）
1. Safari で https://www.dropbox.com/developers/apps を開き、作成済みのアプリを選ぶ
2. 「Settings」タブの **Permission type / Access type** が **Full Dropbox** であることを確認
   （「App folder」だと `/鋼材実験レポート用` が見えません。その場合は Full Dropbox でアプリを作り直す）
3. 「Permissions」タブで次の2つにチェック → 画面下の **Submit**
   - `files.metadata.read`（一覧の取得）
   - `files.content.read`（ダウンロード）
   - `files.content.write` は最初はチェック不要（手順B で解析結果の保存を始めるときに追加）

### A-2. 先にショートカットを作る（コードをリフレッシュトークンに交換する）
トークンを画面にもチャットにも出さず、直接クリップボードへ入れるためにショートカットを使います。
「ショートカット」App →「＋」で新規作成し、次の4つのアクションを順に追加します。

1. **入力を要求**（種類: テキスト、質問: 「コード」）
2. **URLの内容を取得**: URL に `https://api.dropboxapi.com/oauth2/token`
   →「表示を増やす」→ 方法: **POST**、本文を要求: **フォーム**、「新規フィールドを追加」→「テキスト」で4つ:
   - `code` ＝（変数「提供された入力」）
   - `grant_type` ＝ `authorization_code`
   - `client_id` ＝ App key
   - `client_secret` ＝ App secret
3. **辞書の値を取得**: キーに `refresh_token`
4. **クリップボードにコピー**

A-3 でコードをコピーした状態でショートカットを実行し、「コード」にペーストすると、リフレッシュトークンがクリップボードに入ります。
（終わったらショートカットは削除して構いません。App secret が中に残るため。）

### A-3. 許可してコードを得る（Safari）
次の URL の最後に App key を付けて Safari で開き、「続行」「許可」を押します。
URL の `scope=` で読み取りの2つだけを求めるので、アプリ側で書き込み権限を付けていても読み取り専用のトークンになります。

```
https://www.dropbox.com/oauth2/authorize?response_type=code&token_access_type=offline&scope=files.metadata.read%20files.content.read&client_id=
```

表示された **アクセスコード** をコピーし、すぐに A-2 のショートカットを実行します（コードは一度しか使えず、数分で失効します）。
コードはチャットに貼らないでください。

**Full Dropbox か App folder かの見分け方**（Settings に Access type が出ない場合）
- 許可画面で、アクセス先が「アプリ（Apps）」内の専用フォルダと書かれていれば App folder
- 許可した後、Dropbox の最上位に「アプリ」（Apps）フォルダとその中に「鋼材実験」フォルダが新しくできていれば App folder
- 確実なのは A-5 の接続確認: `/鋼材実験レポート用` が見えれば Full Dropbox。App folder の場合は「何も見えない」と表示されます


### A-2/A-3 の代わり（推奨）: GitHub Actions で交換し、Secrets に自動登録する（ショートカット不要・本人認証は1回）
GitHub Actions の自動トークン（GITHUB_TOKEN）は Secrets を書き込めないため、
**このリポジトリの Secrets 読み書きだけ**を許可した fine-grained PAT を使います。

1. PAT を作る（Safari）: https://github.com/settings/personal-access-tokens/new
   - Token name: `structlab-secrets-writer`、Expiration: 7 days
   - Repository access: **Only select repositories** → `structural-experiment-ai`
   - Permissions → Repositories → **Secrets: Read and write**（他は付けない）→ Generate token → 表示された値をコピー
2. それを Secret `GH_SECRETS_TOKEN` に登録（下の A-4 と同じ画面）
3. Claude Code が **Dropbox auth**（mode: preflight）を実行: App key/secret の確認、PAT で試験用の Secret を書いて消せるか
4. 本人認証（1回だけ）: A-3 の URL で「許可」→ コードを Secret `DROPBOX_AUTH_CODE` に登録（数分で失効するのですぐ）→「登録した」と伝える
5. Claude Code が **Dropbox auth**（mode: exchange）を実行: コードを交換 → `DROPBOX_REFRESH_TOKEN` に登録（GitHub の公開鍵で暗号化）
   → 使い終わった `DROPBOX_AUTH_CODE` を削除 → 接続確認。続けて Connection check で Secrets からの接続も確認
6. 終わったら PAT は不要です（期限で失効。すぐ消すなら https://github.com/settings/personal-access-tokens で Delete）。
   リフレッシュトークンは取り消すまで有効なので、以後は認証のやり直しは不要です

### A-4. GitHub Secrets に登録する（Safari）
GitHub アプリでは Secrets を編集できないので Safari で開きます:
https://github.com/csta24055-sys/structural-experiment-ai/settings/secrets/actions/new

「Name」と「Secret」を入れて **Add secret** を3回行います。

| Name | Secret |
|---|---|
| `DROPBOX_REFRESH_TOKEN` | A-2 のショートカットでクリップボードに入った値をペースト（**最初に**登録） |
| `DROPBOX_APP_KEY` | App key |
| `DROPBOX_APP_SECRET` | App secret |

Claude API キーは `ANTHROPIC_API_KEY` の名前を推奨します。`CLAUDE_API_KEY`・`ANTHROPIC_KEY`・`CLAUDE_KEY` の名前で登録済みでもそのまま使えます。

### A-5. 接続確認
Claude Code に「接続確認して」と頼むと、`ops/requests/connection-check.txt` を更新して push し、
GitHub Actions の **Connection check** を起動してログを読み、結果を報告します（読み取りのみ・課金なし）。
確認する内容: Secrets の登録の有無、トークンの更新、`/鋼材実験レポート用` の存在、ファイル一覧（種類別の件数・フォルダ別）、
アップロード完了待ちの判定、動画・PDF・Excel を1件ずつ取得してハッシュ照合と解析単位への分割、Claude API キーの有効性（モデル一覧の取得のみ）。
結果は GitHub アプリの Actions → Connection check → 実行 → Summary でも見られます。

## 手順B: 解析結果の保存を始めるとき（書き込み権限の追加）

解析結果と処理状況（state.db）は Dropbox の `/鋼材実験レポート用_解析結果` に保存します（元資料のフォルダには書き込みません。
プログラムが出力フォルダの外への書き込みを拒否します）。クラウドの使い捨て環境で再開できるようにするため必要です。

1. App Console の「Permissions」で `files.content.write` にもチェック → Submit
2. A-3 の URL の `scope=` を `files.metadata.read%20files.content.read%20files.content.write` にして開き直し、ショートカット（A-2）と A-4（`DROPBOX_REFRESH_TOKEN` だけ更新）を行う

## 手順C: PC のターミナル・Claude Code のセッションで取得する（PKCE。App secret 不要）

```bash
export DROPBOX_APP_KEY=…            # PC の場合。Claude Code ではクラウド環境の環境変数に登録
python3 -m structlab dropbox-auth start            # 読み取り専用（--write で書き込みも許可）
#  → 表示された URL をブラウザで開き「許可」→ 表示されたコードを finish に渡す
python3 -m structlab dropbox-auth finish （コード）
```

トークンは `~/.config/structlab/dropbox_refresh_token`（権限600）に保存され、値は表示されません。続けて接続確認を自動で行います。
この実行環境では環境変数なしで使えますが、GitHub Actions で使う場合は手順A で取得してください
（ファイルの中身を表示して貼り付けると、チャットやログに残るおそれがあります）。

短期のアクセストークン（App Console の「Generate access token」、約4時間で失効）を `DROPBOX_ACCESS_TOKEN` に入れても動きますが、長時間の処理では途中で失効するため推奨しません。

## 確認コマンド

```bash
python3 -m structlab -c configs/dropbox.yaml connect-check            # 一覧・見本の取得・Claude キー（課金なし）
python3 -m structlab -c configs/dropbox.yaml connect-check --no-download
python3 -m structlab -c configs/dropbox.yaml check                    # 実行環境全体の確認
```

どちらも Dropbox には何も書き込みません。うまくいかないときは [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

## 対象フォルダの変更

`configs/dropbox.yaml`（または自分の `config.yaml`）の `source.path` を変更してください。
結果の保存先 `output.dropbox_path` は、解析対象フォルダの **外側** にする必要があります。
