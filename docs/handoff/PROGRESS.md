# 進捗（after-M26 の時点: 2026-10-22 UTC）

## 完了
- 読み取り層・意味理解層・資料横断・要求事項の台帳・費用モデル・パイロットの仕組み（テスト 209 件成功、1 件スキップ、`-m "not slow"`）
- 無料の前処理 v7（実行 ID 39599977934、コミット 9a73d63 のコード）: 全 9 資料が成功。Actions 約 660 分。
  入力トークンを全依頼で実測（合計 8,955,399）。台帳の照合（解析段階）: 成功 29・要確認 552（有料の理解が未実行のため）・
  対象外 11・未実施 8・矛盾 1（P085: 実験区間のまとめ読みの同等性が未検証、既知）
- M24: 最適化前の保存（before-M24）、調査、方針、内容を変えない改善 A1〜A5 の実装と独立した検証、候補の段階を分けた比較の仕組み、
  最適化後の保存（after-M24）、比較資料

## 承認待ち（利用者の判断が必要）
- **G5（Claude Code で解析）の試行と比較 P-G5（API 約 1,500 円）**: 合格なら本番の追加の支払いは 0 円（docs/OPTIMIZATION_M26.md）
- 有料 AI の本番解析（読み取り 約 7,086 円＋理解・統合 約 10,618 円＝約 17,704 円、mid）
- 推奨の比較 P2（C1+C4+C5、再解釈から先、約 1,036 円）: `semantic.stop_before: reinterpret` で止め、
  `structlab pilot branch --arm C1+C4+C5 ...`。代わりに P1（C1v+C4、照合だけ、約 516 円）も可
- 候補 C1v+C4 の品質比較（約 516 円）: 本番の意味理解を照合の直前（`semantic.stop_before: verify`）で止め、
  `structlab pilot branch --arm C1v+C4 --sample 事前説明1 --semantic-db corpus/semantic_run/semantic.db --records results --allow-external-ai`。
  採用なら `semantic.cache_layout: prefix_verify` と `semantic.verify_group: 5` を設定し、`pilot_decision.json` を corpus に置いて再開
- D1（Excel の全体画面を元の解像度に）の検討（M21 との兼ね合い）
- Excel・レポートの作成段階

## 既知の問題・注意
- `summary_all.md` の「AI の読み取りの見積もり 約 $96」は旧来の見積もり（Batch・まとめ読みを考えない）。費用は `costs.json`・docs/COSTS.md を見る
- 出力トークン（思考を含む）は測れないため、費用は幅で示している
- 意味理解の Batch の再開の記録（semantic_batch_state.json）は、依頼の集まりが変わると古い記録が残る（害はない）
- 読み取りの AI は資料ごとのジョブで Batch を待つ（Actions の待ち時間）。1 つにまとめる案は今後の候補
