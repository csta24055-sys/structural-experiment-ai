# structural-experiment-ai

建築構造実験（鋼材の引張試験など）の配布資料・講義動画・Excel などを **Dropbox から1ファイルずつ取得し、小さな単位（PDFのページ・動画の時間区間・Excelのシート範囲）ごとに解析して、10分類に階層化する** Python プログラム（`structlab`）です。

- ファイル数・総容量を事前に決めずに、フォルダ以下を再帰的に順番に処理します
- 処理状況は SQLite に記録され、途中で止まっても続きから再開します。解析済みの部分は再解析しません
- 外部AI（Claude）への資料送信は、**明示的に許可した場合だけ**行います。許可しなければローカル抽出のみで動きます
- このリポジトリには実際の資料・解析結果・APIキーを入れません（`work/`・`config.yaml`・`.env` は `.gitignore` 済み）

> 実験レポートや Excel の提出物は作りません。資料の整理・抽出だけを行います。

---

## 1. どこで動かすか（推奨構成）

iPhone 自体では解析を実行しません。iPhone は「指示を出す・結果を見る」だけにして、処理はクラウドで動かします。

| 方式 | 起動方法（iPhone から） | 長所 | 注意 |
|---|---|---|---|
| **A. Claude Code on the web（推奨）** | Claude アプリでこのリポジトリのセッションを開き「`scripts/run_analysis.sh` を実行して」と頼む | 追加の契約不要。実行中の様子を会話で確認できる | コンテナは使い捨て。状態は Dropbox に保存されるので再開可能 |
| B. GitHub Actions | GitHub アプリ → Actions → 「Analyze materials」→ Run workflow | 放置して数時間動かせる | 非公開リポジトリは無料枠の分数に上限あり（Free プランで月2,000分）。1回最大6時間 |
| C. 自分の PC | `structlab run` | 制限が少ない | PC を起動しておく必要あり |

どの方式でも、処理状況DB（`state.db`）と結果を **Dropbox の出力フォルダ**（既定 `/鋼材実験レポート用_解析結果`）に保存するため、別の場所・別のセッションから続きを実行できます。元資料のフォルダ（`/鋼材実験レポート用`）には一切書き込みません。

この開発環境（Claude Code on the web のコンテナ）で確認できたこと: Python 3.13、ffmpeg、tesseract（日本語）、faster-whisper が導入可能で、`api.dropboxapi.com`・`api.anthropic.com`・Hugging Face へ通信できました（2026-10-20 時点）。

## 2. 資料がそろった後の最小手順

初回の準備（[docs/SETUP_DROPBOX.md](docs/SETUP_DROPBOX.md)・[docs/SETUP_AI.md](docs/SETUP_AI.md)）が終わっていれば、Claude Code のセッションで次のどちらかを頼むだけです。

```bash
# ローカル抽出のみ（資料を外部AIに送らない）
scripts/run_analysis.sh

# Claude による画像・動画フレーム・ページの解析も行う（送信を明示的に許可）
scripts/run_analysis.sh --allow-external-ai
```

途中で止まっても、同じコマンドをもう一度実行すれば続きから再開します。結果は Dropbox の
`/鋼材実験レポート用_解析結果/results/` に出力されます（iPhone の Dropbox アプリで閲覧可能）。

## 3. インストール（PC で動かす場合）

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
# 任意: 音声の文字起こし（ローカル実行・外部送信なし）
python3 -m pip install faster-whisper
# OS パッケージ: ffmpeg（動画に必須）、tesseract-ocr + tesseract-ocr-jpn（任意・ローカルOCR）
sudo apt-get install ffmpeg tesseract-ocr tesseract-ocr-jpn
```

必要なライブラリ: [requirements.txt](requirements.txt)（必須）、[requirements-optional.txt](requirements-optional.txt)（任意）。Python 3.10 以上。

## 4. 使い方

```bash
structlab init                 # config.example.yaml を config.yaml にコピー
structlab check                # 環境・認証・接続を確認（何も書き換えない）
structlab scan                 # 一覧取得と新規・更新・削除・アップロード待ちの判定だけ
structlab run                  # スキャン → 解析 → 結果出力
structlab run --allow-external-ai --max-runtime 300   # AI解析あり・300分で安全に停止
structlab run --watch 30       # 30分ごとに再スキャンし、新しく上がったファイルを処理し続ける
structlab status               # 処理状況
structlab errors               # 未解決のエラー
structlab retry [--file 名前]  # 失敗した部分を再試行対象に戻す
structlab reanalyze 名前       # 指定ファイルを強制的に再解析
structlab report               # 結果ファイルを再生成
```

設定ファイルは `-c` で指定します（既定 `config.yaml`、`scripts/run_analysis.sh` は `configs/dropbox.yaml`）。
途中で止めたいときは Ctrl+C、または `work/STOP` というファイルを作ると、現在の単位を終えてから安全に停止します。

## 5. 出力

`work/results/`（Dropbox 出力を設定していれば `…_解析結果/results/` にも）:

| ファイル | 内容 |
|---|---|
| `knowledge.md` / `knowledge.json` | 10分類に階層化した解析結果。各項目に **出典（ファイル名・ページ・動画の時刻）**、由来（資料に記載／音声での発言／画面に表示／AIの推測）、根拠の照合結果 |
| `inventory.md` / `inventory.json` | フォルダ構造・ファイル名・容量・更新日時・形式・処理状態の一覧（ZIP 内部は `a.zip!/内部パス`） |
| `timelines/*.md` | 動画ごとの「発話 ↔ その時の画面」対応表と重要場面 |
| `files/*.json` | ファイル別の詳細（ページごとの表・数式候補、Excel の数式・グラフ定義など） |
| `status.md` / `status.json` | 処理状況・API使用量・エラー |

10分類: 1.実験の目的・概要 / 2.実験手順 / 3.測定データ / 4.Excel作成方法 / 5.レポート作成方法 / 6.必要な計算式・グラフ / 7.提出物の要件 / 8.先生の指示 / 9.注意事項・禁止事項 / 10.不明点・矛盾

各分類の中は次のように分けて出力し、**先生の指示とAIの推測を混ぜません**:
- 先生の指示として明記されているもの（資料・発言に根拠があるもの）
- 資料・発言・画面に明記されている内容（AI整理）
- AIの推測（原資料で未確認）
- 原文抜粋（ローカル規則で抽出した候補。AIを使わなくても出る）
- 読み取り不能・処理上の記録

「10. 不明点・矛盾」には、資料間で値が食い違う箇所（例: 提出期限が資料によって違う）、AIが示した根拠が原文に見つからない項目、解析に失敗した部分、AI未解析で内容未確認の図・画面がまとめて出ます。

## 6. 仕組み

```
Dropbox (読み取り専用)
  │ files/list_folder(recursive) をページングで全件走査
  ▼
scan ── 新規 / 更新(content_hash 変化) / 削除 / アップロード完了待ち / 無視(~$*, *.part 等) を判定 → SQLite
  ▼
1ファイルずつ: ダウンロード(ストリーミング・rev指定・content_hash 照合) → 解析単位に分割
  │   PDF: ページ / 動画: N秒区間(+重要場面) / Excel: ブック構造 + シートのN行ごと / 画像: 1枚 / ZIP: 安全に展開して中身を順に
  ▼
単位ごと: ローカル抽出 → 規則による分類候補 →（許可時のみ）AI解析 → 検証 → DB に確定
  ▼
report: Markdown / JSON 出力 →（設定時）Dropbox の出力フォルダへ保存
```

- **アップロード途中の誤解析防止**: サーバー更新から `min_age_seconds`（既定300秒、クラウド設定は600秒）経過するまで待つ／スキャン間でハッシュが変わったらさらに待つ／ダウンロードはスキャン時のリビジョンを指定し、取得後に Dropbox の content_hash と照合（不一致なら次回に回す）／一時ファイル名は無視
- **大容量対策**: ダウンロード・ハッシュ計算・ZIP展開はすべてチャンク単位。動画は ffmpeg で区間ごとに音声・フレームを切り出す。PDF はページごとに開く。Excel は read-only モードで行範囲ごと
- **重複処理の防止**: 単位は `(ファイル, content_hash, 種別, 位置)` で一意。完了済みは再実行しない。内容が同一のコピーは `duplicate` として1回だけ解析
- **更新時**: content_hash が変われば新しい版として再解析し、古い版の結果は出力から外す
- **失敗時**: 失敗した単位・ファイル・AI呼び出しを記録し、次回の実行で `max_attempts` 回まで自動再試行。`structlab retry` で手動再試行
- **AIの扱い**: 出力はJSONスキーマで受け取り、スキーマ外は破棄。根拠の引用をローカル抽出テキストと照合し、確認できない「指示」は先生の指示として確定しない。読み取れない部分は「読み取り不能」として記録し推測で埋めない。AIの推測は必ず「AIの推測」と表示
- **AIの交換**: `src/structlab/ai/base.py` の `AIProvider` を実装し、`ai/__init__.py` の `make_provider` に登録すれば別のモデル・APIに差し替えられます（同梱: `anthropic` / `mock` / `none`）
- **動画の音声と映像の対応付け**: 区間内を一定間隔でサンプリングし、画面が変わった時刻をキーフレームとして保存。各発話に「発話開始時点で表示されていた画面」を対応付けます。重要語（「必ず」「提出」「注意」など）を含む発話や、AIが重要と判断した時刻の前後は、高解像度で追加解析します

## 7. 安全性

- 元資料は読み取り専用（ソース側のクラスには書き込み処理が存在しません）。書き込みは `output.dropbox_path` 配下だけに限定し、解析対象フォルダと重なる設定はエラーで停止します
- APIキー・トークンは環境変数のみ。設定ファイルに `api_key` などを書くとエラーで停止します
- ZIP: 絶対パス・`..`・シンボリックリンク・暗号化エントリは展開しない、展開後サイズ・ファイル数・圧縮率・入れ子の深さに上限、実際に書き出したバイト数で判定（ZIP爆弾対策）
- 上限設定: ファイルサイズ、必要な空きディスク、1回あたりのダウンロード量・実行時間、AIの呼び出し回数・トークン数（1回あたり・累計）
- 外部AIへの送信は `ai.allow_external: true` または `--allow-external-ai` のときだけ。既定の設定ではどちらも無効です
- マクロ付き Excel のマクロは実行しません

## 8. テスト

```bash
python3 -m pip install -r requirements.txt && python3 -m pip install -e .
python3 -m pytest -q               # すべて
python3 -m pytest -q -m "not slow" # 時間のかかるもの以外
```

ダミーの PDF・動画・画像・Excel・ZIP をテスト内で生成して検証します（`tests/dummy_data.py`。`python3 tests/dummy_data.py 出力先` で手元にも作れます）。実際の資料は使いません。
検証済み／未検証の範囲は [docs/VERIFICATION.md](docs/VERIFICATION.md) を参照してください。

## 9. ドキュメント

- [docs/SETUP_DROPBOX.md](docs/SETUP_DROPBOX.md) — Dropbox 接続設定
- [docs/SETUP_AI.md](docs/SETUP_AI.md) — AI解析サービス・文字起こしの設定
- [docs/CLOUD_RUN.md](docs/CLOUD_RUN.md) — iPhone からクラウドで実行する手順（Claude Code / GitHub Actions）
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — エラー発生時の対処
- [docs/VERIFICATION.md](docs/VERIFICATION.md) — 動作確認済みの範囲と未検証の項目
