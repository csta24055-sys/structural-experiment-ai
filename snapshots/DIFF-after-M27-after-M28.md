# 保存データの機械的な比較: after-M27 → after-M28

- after-M27: コミット b2bb97b（2026-10-22T03:31:34+00:00）、138 ファイル
- after-M28: コミット 342701b（2026-10-22T05:26:07+00:00）、150 ファイル
- 追加 12・削除 0・変更 15 ファイル

## ファイルごとの変更（行数）

| ファイル | 行 | 種類 |
|---|---|---|
| `CLAUDE.md` | +5 / −0 | 変更 |
| `docs/CLAUDE_CODE_QUEUE.md` | +7 / −2 | 変更 |
| `docs/COSTS.md` | +1 / −1 | 変更 |
| `docs/OPTIMIZATION_M27.md` | +3 / −1 | 変更 |
| `docs/handoff/DECISIONS.md` | +7 / −0 | 変更 |
| `docs/handoff/PROGRESS.md` | +6 / −1 | 変更 |
| `requirements.txt` | +4 / −0 | 変更 |
| `requirements/process.yaml` | +13 / −0 | 変更 |
| `scripts/g5_driver.py` | +18 / −3 | 変更 |
| `scripts/pg5.py` | +15 / −4 | 変更 |
| `src/structlab/ai/queue_provider.py` | +21 / −3 | 変更 |
| `src/structlab/cli.py` | +1 / −1 | 変更 |
| `src/structlab/pilot.py` | +5 / −5 | 変更 |
| `src/structlab/requirements.py` | +9 / −0 | 変更 |
| `tests/test_queue.py` | +26 / −0 | 変更 |
| `docs/DELIVERABLES.md` | +43 / −0 | 追加 |
| `docs/G5_BACKUP.md` | +61 / −0 | 追加 |
| `docs/OPTIMIZATION_M28.md` | +46 / −0 | 追加 |
| `scripts/deliver.py` | +85 / −0 | 追加 |
| `scripts/g5_backup.py` | +265 / −0 | 追加 |
| `src/structlab/deliver/__init__.py` | +0 / −0 | 追加 |
| `src/structlab/deliver/excel.py` | +204 / −0 | 追加 |
| `src/structlab/deliver/rc_beam.py` | +214 / −0 | 追加 |
| `src/structlab/deliver/report.py` | +237 / −0 | 追加 |
| `tests/rc_synth.py` | +23 / −0 | 追加 |
| `tests/test_backup.py` | +158 / −0 | 追加 |
| `tests/test_deliver.py` | +87 / −0 | 追加 |

## 要求事項の台帳: 追加された出典 ['M28']、追加された項目 5 件

- P151: 提供された実験データ（CSV・材料試験結果・データ.txt・readme）と報告書の書式 PDF を 9 資料と合わせ、CSV の全行の読み取り・列の意味・実測強度と各限界荷重・理論値の再計算・Excel の数式と表とグラフ・書式・必須の考察・講義の指示との矛盾を確認する。不足・不明・矛盾は推測で補わず具体的に報告する（scripts/deliver.py check-inputs）
- P152: P-G5 の合格条件: G5 の包含率が API どうしの包含率以上（2 ポイントの許容差は撤廃）。数値・数式・セル・先生の必須の指示など重要項目の厳格な確認は維持し、費用（約 1,000 円）と方式は変えない
- P153: 作業環境の回収による作業フォルダの消失に備える: 保存先（Dropbox・GitHub・その他）を費用・安全性・容量・復旧の確実性で比べて選び、実装し、消失からの復旧を無料で試験する（scripts/g5_backup.py、docs/G5_BACKUP.md）
- P154: 元資料や既存ファイルを勝手に上書き・削除しない（保存先への書き込みは追加だけ、同じ名前があれば失敗させる）
- P155: 本番（9 資料の解析・Excel の作成・レポートの作成・検証・最終出力）を、利用者の明示の許可の後に追加の確認なしで完了できるよう準備する。許可の後も有料の処理の直前では止まる。最終確認の報告の時点では本番を始めない
