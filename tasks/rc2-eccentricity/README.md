# 鉄筋コンクリート構造Ⅱ：保有水平耐力計算とは／偏心率

このフォルダはClaude Codeが課題PDFと講義ノートを参照し、独立検算と答案のPDF書き込みを実施するための作業場所です。

## 入力資料（ローカルだけに配置）
- materials/06_保有水平耐力計算とは_課題.pdf （1ページ）
- materials/06_保有水平耐力計算とは_講義ノート.pdf （22ページ）

講義資料や課題の原本はGitHubの公開リポジトリにコミットしません。材料の配置先は .gitignore で除外しています。トークン付きCanvas URLも保存しません。

## 実行
- python3 tasks/rc2-eccentricity/solver.py
- python3 -m unittest discover -s tasks/rc2-eccentricity -p 'test_*.py' -v

- python3 tasks/rc2-eccentricity/independent_check.py （別方式：剛床の剛性マトリクス法＋変位解析＋感度解析）
- python3 tasks/rc2-eccentricity/fill_assignment.py （materials/の課題PDFへ書き込み → output/鉄筋コンクリート6回目回答.pdf と確認用PNG）
- python3 tasks/rc2-eccentricity/fill_assignment.py --demo （自作の模擬シートで書き込み処理を自己テスト）
- python3 tasks/rc2-eccentricity/make_explanation.py （解説PDF solution/鉄筋コンクリート6回目回答.pdf を再生成。ヘッドレスChromiumを使用）

solver.py は標準ライブラリのみ（Python 3.10以上）。independent_check.py は numpy、fill_assignment.py は pdfplumber・pypdf・reportlab・日本語フォント（IPAゴシック等）、PNG確認に pdftoppm を使用。課題図の転記は PROBLEM.md、Claude Codeへの手順はルートの CLAUDE.md を参照。答えだけではなく、定義・代入・検算を残してください。

## 必須出力
- OUTPUT_SOLUTION.md：各設問の計算過程と最終値
- output/鉄筋コンクリート6回目回答.pdf：原本PDFの空欄へ書き込んだ読みやすい答案（原本がローカルにある場合）
- solution/鉄筋コンクリート6回目回答.pdf：図入りの詳しい解説（自作の模式図。原本の転載なし）
- 実行したテストと結果をDraft PRに記載

右側の耐震壁のモデル化、X/Y方向の取り違え、Kの二重計上を重点検査します。