# エラー発生時の対処

まず次の3つで状況を確認してください。

```bash
structlab -c configs/dropbox.yaml status     # 全体の状況・要対応ファイル・最近のエラー
structlab -c configs/dropbox.yaml errors     # 未解決エラーの詳細
ls work/logs/                                # 実行ログ（run_日時.log）
```

結果フォルダの `status.md` にも同じ内容が出ます（Dropbox アプリから確認可能）。

## 基本方針

- 失敗した単位・ファイルは自動で記録され、次回の `run` で `limits.max_attempts`（既定3回）まで自動再試行されます
- 上限まで失敗したものは `failed`（ファイル）／`partial`（一部の単位だけ失敗）になります。原因を取り除いたら `structlab retry` で再試行対象に戻し、`run` を実行します
- どの段階で止まっても、もう一度 `run` すれば続きから再開します（完了済みの部分はやり直しません）

## よくあるエラー

| 表示 | 原因と対処 |
|---|---|
| `Dropbox の認証情報がありません` | 環境変数 `DROPBOX_APP_KEY` / `DROPBOX_REFRESH_TOKEN` が未設定。クラウド環境の設定を確認し、新しいセッションで実行 |
| `Dropbox 認証エラー（トークン・権限を確認）` | トークンの失効・取り消し、またはアプリの権限不足。権限を変更した場合はトークンを取り直す（SETUP_DROPBOX.md 手順2） |
| `path/not_found` | `source.path` のフォルダ名が違う。Dropbox 上の正確なパスを設定 |
| `出力フォルダが解析対象フォルダと重なっています` | `output.dropbox_path` を解析対象の外に変更 |
| `アップロード完了待ち（あと約N秒）` | 正常な待機。アップロード直後のファイルは `stability.min_age_seconds` 経過後に処理される |
| `取得中に変更を検出` | ダウンロード中にファイルが更新された。次回のスキャンで新しい版として処理される |
| `ディスク空き容量不足` | 大きな動画などで作業ディスクが足りない。`work/cache/` を削除、`limits.min_free_disk_mb` を調整、または `limits.max_file_size_mb` で大きすぎるファイルを除外 |
| `サイズ上限超過` | `limits.max_file_size_mb` を上げる |
| `旧形式 .xls は未対応` | Excel で `.xlsx` に保存し直してアップロード |
| `パスワード付きPDFは解析できません` | パスワードを外したPDFをアップロード |
| `ZIP爆弾の可能性` / `ZIP内のファイル数が上限` | 異常なZIP。正常なものなら `zip:` の上限を確認して調整 |
| `動画の長さを取得できません` / ffmpeg エラー | 動画の破損・アップロード途中。完全な動画を再アップロード |
| `コマンドが見つかりません: ffmpeg` | `apt-get install ffmpeg` |
| `faster-whisper がありません` | `pip install faster-whisper`、または `transcription.backend: none` |
| `外部AIへの送信が許可されていません` | 意図どおりなら問題なし。AI解析をするなら `--allow-external-ai` |
| `APIキーが無効です` | `ANTHROPIC_API_KEY` を確認 |
| `レート制限` / `APIエラー 5xx` | 一時的。次回の実行で自動再試行される |
| `AI呼び出し回数の上限` | 1回の実行の上限に達しただけ。次回の `run` で続きを処理。上限は `ai.max_calls_per_run` など |
| `出力が上限で途切れました` | `ai.max_output_tokens` を増やして `structlab retry` |
| `AIが解析を拒否しました（refusal）` | その単位は自動再試行しない。`knowledge.md` の「解析に失敗した部分」に記録される。原資料を直接確認 |
| `別の structlab が実行中です` | 同じ作業ディレクトリで二重起動。前の処理の終了を待つ。異常終了後に出る場合はプロセスが残っていないか確認 |

## 状態を最初からやり直したい

結果をすべて作り直す場合は、作業ディレクトリ（`work/`）を削除します。
Dropbox に state.db を保存している場合は、出力フォルダの `state/state.db` も削除しないと、次回それを取得して続きから再開します。

特定ファイルだけ作り直す場合: `structlab reanalyze ファイル名の一部`

## 結果がおかしいとき

- `knowledge.md` の各項目には出典（ファイル・ページ・時刻）があります。必ず原資料で確認してください
- 「AIの推測」「根拠が原文で確認できない」項目は誤りの可能性があります
- 動画の発話と画面の対応がずれる場合は、`video.sample_interval_seconds` を小さく、`video.scene_change_threshold` を調整してください
