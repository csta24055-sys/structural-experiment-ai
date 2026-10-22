# 保存データの機械的な比較: after-M25 → after-M26

- after-M25: コミット 675aa31（2026-10-21T23:54:55+00:00）、129 ファイル
- after-M26: コミット 2a39010（2026-10-22T00:27:24+00:00）、134 ファイル
- 追加 5・削除 0・変更 15 ファイル

## ファイルごとの変更（行数）

| ファイル | 行 | 種類 |
|---|---|---|
| `docs/handoff/DECISIONS.md` | +8 / −0 | 変更 |
| `docs/handoff/DESIGN.md` | +7 / −1 | 変更 |
| `docs/handoff/HANDOFF.md` | +5 / −4 | 変更 |
| `docs/handoff/PROGRESS.md` | +2 / −1 | 変更 |
| `requirements/process.yaml` | +8 / −0 | 変更 |
| `src/structlab/ai/__init__.py` | +6 / −0 | 変更 |
| `src/structlab/ai/base.py` | +4 / −0 | 変更 |
| `src/structlab/cli.py` | +34 / −1 | 変更 |
| `src/structlab/config.py` | +1 / −1 | 変更 |
| `src/structlab/pipeline.py` | +4 / −0 | 変更 |
| `src/structlab/reading/runner.py` | +5 / −1 | 変更 |
| `src/structlab/reading/semantic_bridge.py` | +6 / −1 | 変更 |
| `src/structlab/requirements.py` | +1 / −0 | 変更 |
| `src/structlab/semantic/corpus.py` | +3 / −0 | 変更 |
| `src/structlab/semantic/engine.py` | +8 / −7 | 変更 |
| `docs/CLAUDE_CODE_QUEUE.md` | +37 / −0 | 追加 |
| `docs/OPTIMIZATION_M26.md` | +45 / −0 | 追加 |
| `scripts/queue_reading.py` | +58 / −0 | 追加 |
| `src/structlab/ai/queue_provider.py` | +96 / −0 | 追加 |
| `tests/test_queue.py` | +62 / −0 | 追加 |

## 要求事項の台帳: 追加された出典 ['M26']、追加された項目 3 件

- P143: 目的は技術的な最適化ではなく、成果と品質を保ったまま実際に支払う総額を最も安くすること。技術的な効率・処理速度・洗練度・AI の使用量・処理回数はそれ自体を評価基準にしない。非効率・処理回数が多い・無料の計算を大量に使う・時間が長い方法でも、総額が安く成果と品質を保てるなら有力な選択肢とする。総額が増える技術的改善は削減の成果として数えない
- P144: 古典的な手法・単純な処理・大量の無料計算・既存ツールの流用・重複を許す方式・力技も検討する。無料枠・既存の計算資源は条件と制限を明示して評価する。時間が長いことだけで不利に評価しない。理論上の最適解の証明を目的にしない
- P145: 比較は Excel・レポートの完成までの実際の支払総額（有料 API・クラウド計算・ストレージ・再実行・検証・保存など実際に発生するもの）で行う
