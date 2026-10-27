"""試験出題予測・集中対策システム。

授業動画・配布資料・課題・過去問（Dropbox / ローカル / Box 共有リンク）を読み、
出題根拠のある論点だけを S/A/B/C で選び、対策教材と模擬テスト 3 回分を作る。

処理の段階（それぞれ別モジュール。内容データと文書出力は分離している）:
  discover → links/box（入力の発見・Box 取得可否） → analyze（動画・PDF 読み取り）
  → classify（科目・講義回） → cues/topics（出題根拠） → scoring（S/A/B/C）
  → questions（問題・解答） → render（PDF・Word）
"""
