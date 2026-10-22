# 引き継ぎ資料（保存データ: after-M27）

この保存データ一式だけを読めば、元の会話（Claude Code とのやり取り）を見なくても、システムの目的・要求事項・
設計・実装・実行方法・進捗・未解決事項を理解して作業を引き継げるように書いています。
最初にこの文書を読み、次の順で他の文書を読んでください。

| 順 | 文書 | 内容 |
|---|---|---|
| 1 | `HANDOFF.md`（この文書） | 目的・利用者・絶対条件・全体像・どこに何があるか |
| 2 | `REQUIREMENTS.md` | 利用者の全指示（M0〜M23 と CLAUDE.md）を項目にした台帳。**最優先で守るもの** |
| 3 | `DESIGN.md` | 全体設計、処理の流れ、モジュールの役割、データ形式、外部サービス |
| 4 | `COSTS.md` | 費用モデル、見積もり、採用済み・候補の削減策 |
| 5 | `DECISIONS.md` | 判断の履歴（何を採用し、何をなぜやめたか） |
| 6 | `PROGRESS.md` | この時点の進捗、実行中・完了した処理、未完了作業、既知の問題 |
| 7 | `MODULES.md` / `CONFIG.md` / `WORKFLOWS.md` / `TESTS.md` / `ENVIRONMENT.md` | ソースから自動生成した参照資料 |
| — | `source/`（アーカイブの中） | ソース一式（Git の管理下のファイルすべて）。`MANIFEST.json` に全ファイルの sha256 |

記号: 文中の **[確認済み]** は実データ・テスト・公式資料で確かめた事実、**[推定]** は根拠のある見込み、
**[未確認]** はまだ確かめていないこと。

## 1. 保存データの識別

- 名前: `after-M27`（M24〜M26 の最適化に続き、M27「G5 方式の実用化」を終えた時点）
- 元のコミット: `MANIFEST.json` の `commit`（ブランチ `claude/wonderful-franklin-3tnt72`、リポジトリ `csta24055-sys/structural-experiment-ai`、非公開）
- 以前の保存データ: `before-M24`（M24 の最適化前、9880681）、`after-M24`（M24 後、d1a7472）。機械的な差分は `snapshots/DIFF-*.md`、結論は `COMPARISON-*.md`
- 最適化の詳細: `source/docs/OPTIMIZATION_M24.md`・`M25.md`・`M26.md`（支払総額での比較）、`CLAUDE_CODE_QUEUE.md`（G5 の手順）、`OPTIMIZATION_M27.md`（G5 の公式条件・同等性・完成までの費用・P-G5）。3大原則（成果を減らさない・品質を下げない・今の方式に固執しない）は台帳 P136

## 2. 何のためのシステムか

建築学科の学生（利用者）が、鋼材（鉄筋コンクリート梁）のせん断加力実験のレポートと Excel を作るために、
先生の説明・実験の録画・資料を **AI で正確に読み取り、理解し、指示（してよいこと・いけないこと・必須事項）を漏れなく
整理する** ツールです。名前は `structlab`（Python パッケージ、`src/structlab/`）。

- 資料は利用者の Dropbox の `/鋼材実験レポート用`（11 フォルダ・12 ファイル。動画 8、PDF 4（うち 3 は同じ内容の重複））
- 利用者は主に **iPhone の Claude アプリ** から Claude Code（クラウドのセッション）に指示する。
  処理の重い部分は **GitHub Actions** で動かす（`ops/requests/*.txt` を変更して push すると起動）
- 現在のゴールは「**読み取り段階**（全資料を高精度に読む）と、その上の**理解・統合**（意味理解・指示の抽出と照合・資料横断）」。
  **実験レポートや Excel の提出物そのものは作らない**（利用者の指示）。Excel・レポートの作成段階では、同じ要求事項の台帳で再照合する仕組みだけを用意している

## 3. 絶対に守ること（詳細は REQUIREMENTS.md）

1. **有料 AI の本番解析は、利用者が予算を明示的に承認するまで開始しない**（M19〜M24）。有料の品質比較（パイロット）も同じ
2. 実際の Excel・レポートの作成も承認まで開始しない
3. **AI モデルを格下げしない**。全工程で現在使える最上位モデル `claude-opus-5-5`（effort high）を使う（M16。Fable 5.1 は利用不可）
4. 本来得られる成果そのもの（情報の種類・範囲・量・詳細さ）と、その品質（正確性・網羅性・理解の深さ・検出能力・再現性・検証可能性）を下げない。
   **品質が同等と確認できない変更は本番に採用しない**（M19・M23・M24）
5. 過去のすべての指示を維持する（新しい指示は追加であって置き換えではない。訂正は台帳で superseded として双方を残す）
6. Dropbox の元資料フォルダ `/鋼材実験レポート用` には **書き込まない**（Dropbox への書き込み自体を行わない運用）
7. API キー・トークンを、チャット・ログ・コード・コミットに表示・保存しない（`cat` で中身を見ない。値を求めない）
8. 実データ・解析結果（`work/`）・`config.yaml`・`.env`・本人の文章スタイル PDF・`style/profile.json` をコミットしない
9. 話者識別はしていない。動画の音声を「先生の指示」と断定しない
10. 本番解析の最終指示（M15）: 先生の実験経過動画 1→2 だけが連続。末尾で冒頭画面に無音で戻る部分は除外し、除外時刻と根拠を記録。
    有効部分をつなぎ、欠けた時間を補完しない。生徒の動画は表裏を交互に撮影しており、先生側と重複除去しない・時計で合わせない・
    先生側の連結/除外を適用しない。他の「1・2」の動画は番号で連続とみなさない。回転・音声欠落・通知を補正。発話を捏造しない

## 4. 全体像（詳細は DESIGN.md）

```
Dropbox（読み取りのみ）
   │  structlab read（GitHub Actions「Read materials」、資料ごとに並列ジョブ）
   ▼
読み取り層 reading/ ── 無料: 棚卸し → PDF テキスト層 / 動画の切り出し・回転補正・場面分割・代表画面・OCR・文字起こし(Whisper)
   │                  有料(許可時): Claude による画像の構造化書き起こし（Batch API）、用語の訂正
   │                  無料: 照合（PDF テキスト層・OCR との突き合わせ）
   ▼  reading.json（資料ごと）
資料横断の整理 reading/corpus.py ── 重複・差分、先生 1→2 の時間軸、生徒の表裏、先生と生徒の対応
   ▼
意味理解層 semantic/（semantic_bridge が読み取り結果を 120 秒の「場面」に変換）
   │  無料: 規則による指示の抽出・関連付け
   │  有料(許可時): 第1回 場面の理解→全体の理解→再解釈 / 第2回 指示の抽出 / 第3回 元資料との照合 / 資料横断の統合
   ▼
要求事項の台帳 requirements.py ── 利用者の指示＋先生の指示＋読めなかった箇所を、解析・Excel・レポートの各段階で照合
費用 costmodel.py / pilot.py ── 工程別の費用、候補の品質比較
```

## 5. どこに何があるか

| 場所 | 内容 |
|---|---|
| `src/structlab/reading/` | 読み取り層（`runner.py` 実行、`video_read.py` 動画、`pdf_read.py` PDF、`ai_read.py` AI への依頼、`detail.py` 拡大画像、`corpus.py` 資料横断、`semantic_bridge.py` 意味理解への受け渡し、`verify.py` 照合） |
| `src/structlab/semantic/` | 意味理解（`engine.py` 段階の実行、`prompts.py` 指示文とスキーマ、`rules.py` 規則、`relations.py` 関連付け、`report.py` 出力） |
| `src/structlab/ai/` | AI プロバイダ（`anthropic_provider.py` 本番、`mock_*.py` テスト用） |
| `src/structlab/media.py` | ffmpeg・画像処理（切り出し領域の検出、回転、可逆保存） |
| `src/structlab/requirements.py` + `requirements/process.yaml` | 要求事項の台帳と照合 |
| `src/structlab/costmodel.py` / `pilot.py` | 費用モデル / 品質比較（パイロット。`pilot branch` は段階を分けた有料の比較） |
| `src/structlab/ai/tokens.py` | 画像のトークン数（公式の式） |
| `scripts/snapshot.py` / `scripts/actions_usage.py` | 保存データの作成 / Actions の利用時間の記録 |
| `snapshots/` | 最適化前後の保存データ（このデータ自体） |
| `src/structlab/cli.py` | コマンド（`python3 -m structlab ...`） |
| `configs/dropbox.yaml` | 本番設定（秘密情報なし） |
| `.github/workflows/reading.yml` | 本番の読み取り＋意味理解＋台帳の照合（`ops/requests/reading.txt` の変更で起動） |
| `docs/` | 利用者向けの説明（READING / SEMANTIC / COSTS / SETUP_* など） |
| `CLAUDE.md` | Claude Code への常設の指示（「解析を開始して」と言われたときの手順など） |
| `tests/` | テスト（ダミーデータを自動生成） |

## 6. 実行方法（要約。詳細は DESIGN.md §6）

```bash
# 環境（ENVIRONMENT.md）
sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-jpn
python3 -m pip install -r requirements.txt -r requirements-optional.txt && python3 -m pip install -e .
python3 -m pytest -q -m "not slow"            # テスト（約 10 分）

# 本番は GitHub Actions で: ops/requests/reading.txt の request 行などを変えて push
#   allow_external_ai: false（無料）/ true（有料。承認後だけ）
#   count_tokens: true で入力トークンを無料で実測
# 手元で（認証情報が環境変数にある場合）
python3 -m structlab -c configs/dropbox.yaml read --out work/reading            # 無料の読み取り＋見積もり
python3 -m structlab -c configs/dropbox.yaml read --count-tokens --out work/reading
python3 -m structlab cost --reading work/reading --semantic-db <db> --out work/costs   # 工程別の費用
python3 -m structlab requirements verify --stage analysis ...                  # 台帳の照合
```

必要な環境変数（値は保存していない）: `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`,
`ANTHROPIC_API_KEY`（有料実行時のみ）。GitHub Secrets に登録済み [確認済み: 接続確認ワークフローが成功]。

## 7. この時点の状態（詳細は PROGRESS.md）

- 無料の前処理 v7（実行 ID 39599977934）は全 9 資料で完了（Actions 約 660 分）。入力トークンは全依頼で実測済み（合計 約 896 万）
- 有料 AI は一度も本番で実行していない（承認待ち）。有料の品質比較（パイロット）も未実施（承認待ち）
- 実際の支払いが最も安い候補は G5（定額の契約の Claude Code で同じ依頼を処理。完成まで API 0 円＋比較 P-G5 約 790 円）。
  実行の準備は完了（`scripts/g5_driver.py`・`.claude/agents/g5-answerer.md`・`scripts/pg5.py`）。既定では無効・承認待ち
- 費用の見込み（API の場合、全 9 資料、Batch、mid）: 読み取り 約 7,086 円＋理解・統合 約 10,618 円＝**約 17,704 円**（幅 約 9,790〜30,510 円。
  M25 で再解釈の出力の仮定を直した）。最適化前は 約 18,383 円。候補 C1+C4+C5 を比較（約 1,036 円）で採用できれば 約 14,639 円
- M24 で採用した改善（内容は同じ）: 依頼のまとめ送り（A2）、キャッシュの先書き（A1）、中断した Batch の再開（A3）、公式の式の見積もり、
  Actions の利用時間の記録。独立した検証チームが最適化前のコードと並べて同等を確認

## 8. 引き継ぐ人への注意

- 品質の基準は「今の実装」ではなく「今の最新構成で本来得られる情報とその品質」。別の方法で同じ以上が得られると検証できれば作り替えてよい（M24）
- 依頼の中身（AI に送る文字・画像）を 1 バイトでも変える削減は、有料の比較（`structlab pilot branch`）で同等と確認するまで既定で無効にしておく
- 内容を変えない改善でも、変更前のコードと並べて「送る依頼の全文」と「DB の結果」が同じことを確かめてから採用した（tests/test_reading.py の同等性テスト）
- GitHub Actions の利用時間（非公開リポジトリは無料枠から消費）も費用に含めて判断する
- 利用者への報告は日本語。iPhone で読むので要点を短く
