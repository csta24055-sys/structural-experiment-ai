# 最適化前後の比較（M24）: 変更・採否・費用・品質検証・未検証・課題

| | 最適化前（提出物 1） | 最適化後（提出物 2） |
|---|---|---|
| 保存データ | `snapshots/before-M24/` ＋ `snapshots/before-M24.tar.gz` | `snapshots/after-M24/` ＋ `snapshots/after-M24.tar.gz` |
| 元のコミット | 9880681（M24 開始時点。M24 の作業開始前に保存） | ba6c9c0 以降（`after-M24/MANIFEST.json` の commit） |
| 取得方法 | GitHub のリポジトリ `csta24055-sys/structural-experiment-ai` のブランチ `claude/wonderful-franklin-3tnt72` の `snapshots/`（各 .tar.gz を展開すると `<名前>/` に資料と `source/`） | 同左 |

詳しい調査・方針・検証は `after-M24/source/docs/OPTIMIZATION_M24.md`（同じ内容がリポジトリの docs/ にもある）。

## 1. 変更点（ソース、snapshots/ を除く）
```text
 .github/workflows/reading.yml            |  13 +-
 docs/COSTS.md                            |  47 +++---
 docs/OPTIMIZATION_M24.md                 | 146 +++++++++++++++++
 requirements/process.yaml                |  20 +++
 scripts/actions_usage.py                 |  67 ++++++++
 scripts/snapshot.py                      | 258 +++++++++++++++++++++++++++++++
 src/structlab/ai/anthropic_provider.py   | 125 +++++++++++++--
 src/structlab/ai/base.py                 |   5 +
 src/structlab/ai/tokens.py               |  33 ++++
 src/structlab/cli.py                     |  35 ++++-
 src/structlab/costmodel.py               | 115 +++++++++++---
 src/structlab/pilot.py                   | 110 +++++++++++--
 src/structlab/reading/ai_read.py         |  36 +++--
 src/structlab/reading/runner.py          |   3 +-
 src/structlab/reading/semantic_bridge.py | 107 ++++++++++---
 src/structlab/requirements.py            |  11 +-
 src/structlab/semantic/engine.py         | 124 ++++++++++++---
 tests/test_config_and_integrations.py    |  64 ++++++++
 tests/test_costmodel.py                  |  58 +++++++
 tests/test_pilot.py                      |  75 +++++++++
 tests/test_reading.py                    | 121 +++++++++++++++
 21 files changed, 1446 insertions(+), 127 deletions(-)
```

| 区分 | 変更 | 内容が同じか | 既定 |
|---|---|---|---|
| A1 キャッシュの先書き | ai/anthropic_provider.py（batch_plan・2 段階の Batch） | 同じ（キャッシュの設定のみ） | 有効 |
| A2 依頼のまとめ送り | semantic/engine.py（_batching・_ai_many）、reading/semantic_bridge.py（call_many） | 同じ（依頼の全文・DB の結果） | 有効 |
| A3 中断した Batch の再開 | reading/semantic_bridge.py（semantic_batch_state.json）、ワークフローのキャッシュ | 同じ | 有効 |
| A4 見積もりの式 | ai/tokens.py、reading/ai_read.py | AI に送る内容に関係しない | 有効 |
| A5 費用の記録 | scripts/actions_usage.py、costmodel.py、ワークフロー | 関係しない | 有効 |
| 候補 C1v / C1r / C4 / C2 | engine.py（cache_layout の種類・prefix_before_images・cache_ttl）、pilot.py | 変わる（順序・まとめ方） | **無効**（比較の承認待ち） |
| 段階を分けた比較 | pilot.py（branch_run・branch_compare）、engine.py（stop_before）、cli.py（pilot branch） | 関係しない（比較の仕組み） | 使うときだけ |
| 台帳 | requirements/process.yaml に M24（P127〜P135）、自動検査 snapshot_before / snapshot_after | — | — |
| 保存 | scripts/snapshot.py、snapshots/ | — | — |

## 2. 採用・不採用と理由
- 採用: A1〜A5（内容が同じことをテストと独立した検証チームで確認）
- 候補（比較で採用されるまで無効）: C1v+C4（推奨。比較 約 516 円、本番 1 回の削減 約 1,818 円）、C1v、C4
- 保留: C1r（比較 約 1,230 円 > 削減 約 733 円）、D1（M21 の最高品質の指示との兼ね合い。削減 約 775 円 [推定]）
- 不採用: C2（比較の費用が削減の約 19 倍）、画面の削減・縮小（M20・M21 に反する）、effort の引き下げ・モデルの変更（禁止・理解の深さ）、
  資料全体の発話を共通の先頭にする案（変更が大きく比較が見合わない）

## 3. 費用（全 9 資料、Batch、1 USD = 155 円）
| | low | mid | high |
|---|---|---|---|
| 最適化前 合計 | 約 10,040 | 約 18,027 | 約 30,290 |
| 最適化後 合計（採用分） | 約 9,600 | 約 17,348 | 約 29,610 |
| 削減額・削減率（mid） | | **−679 円（−3.8%）** | |
| 候補 C1v+C4 が採用された場合 | 約 9,150 | 約 15,530（−13.9%） | 約 25,570 |

内訳（mid）: 読み取り 7,236 → 7,086、理解・統合 10,791 → 10,262。入力トークンは全 9 資料で実測（8,955,399）、出力は仮定。
最適化作業そのものの費用: 有料 API 0 円、GitHub Actions 0 分（M24 で起動していない。v7 の無料実行 約 660 分は M24 前に開始したもの）。
調査・検証のサブエージェント 4 件（Claude Code の利用枠。API の請求ではない）。

## 4. 品質の検証結果
- A2: 最適化前のコードと並べて 12 通りの条件で、送る依頼の列（順序・全文）と DB の全内容が一致（独立した検証チーム）。
  初回の検証で見つかった「Batch を使わない経路での中断時の結果の喪失」は修正し、再検証で最適化前と同じ動作
- A1: 先書きしない場合は依頼が 1 バイトも変わらない。先書きする場合もキャッシュの設定以外は同じ。1 つの依頼で有効期間が混ざらない
- A3: 1 段目・2 段目で中断して順序を入れ替えても、送り直しなし・各依頼に正しい結果
- テスト: 最適化前 199 件成功 → 最適化後 209 件成功（追加: 同等性・中断・先書き・再開・費用モデル・段階を分けた比較）
- 保存データ: 各アーカイブを空のフォルダに展開し、必要なファイルの有無とテストの実行を確認（各 TESTS.md）

## 5. 未検証の事項
- キャッシュが実際に当たる割合（公式に保証されない。有料実行の cache_read_tokens で測る）→ 削減額は仮定に依存
- 出力・思考の量（費用の約半分）
- 候補 C1v / C4 / C1r / C2 / D1 の品質（有料の比較が必要）
- P085（実験区間のまとめ読みと区間ごとの同等性）

## 6. 残る課題
- 候補の比較の承認と実施（推奨: C1v+C4）、D1 の判断
- 読み取りの AI を 1 つのジョブにまとめて Actions の待ち時間を減らす案（今後）
- summary_all.md の旧来の見積もり（約 $96）の表示が紛らわしい（費用は costs.json を見る）
