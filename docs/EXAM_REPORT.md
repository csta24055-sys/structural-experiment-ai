# 開発報告: 試験出題予測・集中対策システム（2026-10-27）

ブランチ: `claude/busy-fermat-ybka8s`。使い方は [EXAM.md](EXAM.md)。

## 1. 作業開始時の確認

| 項目 | 結果 |
|---|---|
| リポジトリ | csta24055-sys/structural-experiment-ai（**公開**）。既定ブランチ main |
| 開始時の内容 | Initial commit のみ（README.md と Python 用の .gitignore） |
| 書き込み | 開発ブランチへ push 可 |
| 過去の成果物 | structlab_exam_feature-branch.zip・exam-prediction.patch・docs/EXAM.md・以前の開発ブランチは、いずれもこの環境に無かった。使っていない |

## 2. 添付コードの復元

`tools/restore_dump.py` で 2 つのダンプを復元（既存ファイルは上書きしない。一覧・行数・sha256 は `docs/restore/`）。

| ダンプ | ファイル数 | 備考 |
|---|---|---|
| ① 報告書作成コード（commit a5a18c9） | 9 | `scratchpad/make_content*.py` はパス末尾の注記（※…）を除いて復元し、注記は manifest に残した |
| ② 動画・資料分析コード（commit 64d84b5） | 128 | 見出しは「116」だが、区切りは 128 件（scratchpad/ の 12 件を含む）。重複パスなし |

ダンプに含まれていなかったもの（復元できない・推測で作っていない）:
- `requirements/process.yaml` … `src/structlab/requirements.py` が読む。これが無いため既存テスト 3 件が失敗する（下記）
- 元の `.gitignore` … `tests/test_check_catalog.py` が求める `work/`・`*writing_style*.pdf` を追記した

## 3. 実装したもの（`src/structlab/exam/`）

| モジュール | 内容 | 既存コードの再利用 |
|---|---|---|
| links.py | .url（UTF-8/BOM/UTF-16/CP932）とテキストから URL を抽出。Box のホストだけを Box として扱う。追跡用 utm_* の除去・重複の統合 | — |
| box.py / box_browser.js | Box 共有リンクの検査（状態 A〜F）。HTTP（Box 外への転送は追わない）・Playwright（Python 版→Node 版）・Box 公式 API。許可がある場合だけ公式の手段で取得 | media.probe |
| discover.py | ローカル・Dropbox の資料の発見、役割（過去問・課題・試験範囲…）・年度の判定、内容ハッシュでの重複判定 | DropboxSource |
| analyze.py | PDF・Office（LibreOffice で PDF 化）・動画・字幕（.vtt/.srt）・画像の読み取り。結果を内容ハッシュで保存し再利用 | read_pdf・read_video・OCR・Whisper |
| classify.py | 科目（一覧・フォルダ名・本文の用語・教員名・似た資料）と講義回の判定。不明は「要確認」 | — |
| cues.py / topics.py | 教員の指示（出題・暗記・重要・計算・頻出・除外・訂正・範囲）の検出と論点への対応づけ、過去問・課題・反復・強調の照合、動画画面とスライドの照合 | — |
| scoring.py | S/A/B/C 判定・根拠台帳・手動修正。未解析資料がある講義回の論点は C にしない | — |
| questions.py | 計算（sympy で解いて検算）・穴埋め・選択・正誤・記述、模擬テスト 3 回分 | — |
| materials.py / render.py | 教材の内容データと出力の分離。PDF（日本語フォント埋め込み）・Word・品質検査 | — |
| pipeline.py / cli.py / sync.py | 全工程・差分更新・判定履歴・途中再開・Dropbox への教材と状態の保存・`--quiet` | DropboxOutput・redact |

既存の `structlab` の機能・テスト・ワークフローは変更していない（pyproject.toml と requirements.txt に依存とコマンドを追加しただけ）。

## 4. テスト

ローカル（Python 3.13、tesseract あり）: **299 passed / 3 failed / 2 skipped**。

- 失敗 3 件は `tests/test_requirements.py`（`requirements/process.yaml` が無い）。変更前の基準でも失敗しており、今回の変更とは無関係
- 変更前の基準: 230 passed / 10 failed（上記 3 件・anthropic 未導入 6 件・.gitignore 1 件）。anthropic を入れ .gitignore を補うと 7 件は通る
- 追加したテスト 62 件（`tests/test_exam_*.py`・`tests/test_restore_dump.py`）はすべて通過

受け入れ試験（指示書 第 20 章）との対応: 合成データで確認したもの

- 1〜20: 2 科目の混在、ファイル名に科目なし（内容・フォルダから判定）、講義回、出題明言→S、出題しない→C-除外、後の回の変更、過去問なし／複数年度、同一論点の反復、重複投入、途中停止→再開、API 不使用、未解析での誤除外防止、計算の検算、原本を変更しない、B・C を教材に混ぜない
- 21〜34: pytest、CLI、PDF・Word、日本語、模擬テスト 3 回、問題と解答用紙の分離、API キーなしで動作、Secrets を出力しない、Dropbox 接続失敗時のエラー、再開、成果物
- 35〜52: .url の抽出（4 文字コード）、メタデータの記録、Playwright が無いときの代替、メタデータ取得と取得の区別、許可時の取得（模擬 API）、再生可でもダウンロード不可なら取得せず未解析と表示、認証・パスワード、期限切れ、未解析の反映、重複リンク、再開、認証情報・教材を公開の場所に出さない

確認できていないもの:
- 「数式が不鮮明」「音声の一部が不明瞭」「動画が複数ファイルに分割」の実データでの挙動（OCR・Whisper の精度は未評価）
- 45（取得した動画の文字起こし・代表画面）は合成動画で場面抽出のみ確認。Whisper はこの環境に無く未実行。OCR（tesseract）は途中で導入して合成動画で確認
- 50（許可された動画からのクリップ生成）は未実装。代わりに動画の時刻と論点の対応（重要発言.json・スライド照合.json）を出す

## 5. 実データでの検証

Dropbox の `/授業動画` には動画は無く、Box 共有リンクの .url が 1 件だけ。これを使って確認した:

| 項目 | 結果 |
|---|---|
| 共有ページにアクセスできるか | できる（HTTP 200、ログイン不要） |
| メタデータ | 04.mp4・mp4・648,927,349 バイト・1:24:41 |
| 動画を取得できるか | **できない**。ページ自身が `can_download: false`（`can_preview: true`）を示している → 状態 B |
| 音声抽出・画面解析・PDF 照合・出題候補 | 動画を取得できないため未実施。この講義回は「未解析」と表示される |

ダウンロード禁止の設定は回避していない。この動画を解析するには、教員から動画ファイルか字幕の提供を受ける必要がある。

## 6. GitHub Actions

- Tests: push ごとに実行。process.yaml の 3 件は失敗のまま（§4）
- CI では OCR が使えるため、動画の画面とスライドの照合が働き、場面の切り替わり時刻（60 秒）の
  「鏡像音源法は試験に出します」が直前のスライドの論点に結びつく不具合が見つかった（ローカルは OCR 無しで再現しなかった）。
  文中で名指しした論点を画面より優先し、時刻はその時刻を含む場面を優先するよう修正し、回帰テストを追加
- Exam preparation / Box link check: 追加済み。Dropbox の Secrets が未登録のため、まだ実行していない（未検証）

## 7. 利用者が行う操作

1. GitHub の Settings → Secrets に DROPBOX_APP_KEY / DROPBOX_APP_SECRET / DROPBOX_REFRESH_TOKEN を登録（出力フォルダへの書き込み許可 `files.content.write` が必要）
2. `requirements/process.yaml` が手元にあれば追加する（既存テスト 3 件が通る）
3. 授業資料（スライド PDF・過去問・課題）を Dropbox の科目名フォルダに置く。Box の動画は、可能なら教員に動画ファイルか字幕を依頼する
4. Actions → Exam preparation → Run workflow。教材は Dropbox の `/試験対策出力/試験対策システム/` に保存される
5. 公開リポジトリのままでは Artifacts に教材を保存しない設計。非公開にすれば Artifacts にも保存される

## 8. 今後の改善点

- 実際の試験問題による過去問バックテスト（的中率・学習範囲の削減率の測定）
- 授業資料の実データで、論点の切り出し（ページ見出し基準）と手がかりパターンの調整
- 許可された外部 AI を使う場合の問題生成・根拠検証（既存の ai/ と semantic/ の仕組みの接続。現在は無効）
