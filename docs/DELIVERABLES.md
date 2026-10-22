# Excel・レポートの作成（M28 で準備。作成は利用者の許可の後）

台帳 P151・P155。実装は `src/structlab/deliver/`（`rc_beam.py`・`excel.py`・`report.py`）と `scripts/deliver.py`。
AI は呼ばない（費用 0）。本文（考察など）は本番で Claude Code（定額の契約）が書き、ここでは組み立てと検査だけを行う。

## 1. 入力（`work/inputs/`、コミットしない）
| ファイル | 内容 | 読み方 |
|---|---|---|
| `*実験結果*.csv`（CP932） | 1 行目は題、2 行目が見出し。Load・Disp-1〜3・Strain-1〜4 | readme の対応で P・たわみ No.1〜3・主筋 No.1・あばら筋 No.2〜4 |
| `*材料*.txt`（CP932） | 主筋・あばら筋の σy・σm、コンクリートの σB | 表2 の実測値 |
| `データ*.txt`（CP932） | 曲げひび割れ・せん断ひび割れ・曲げ降伏の荷重と変位、最大荷重 | 表3 の TQ（= P/2） |
| `readme*.txt` | 列の対応 | 見出しとの照合 |
| 報告書の書式 PDF | 表1（設計値）・Ⅳ 項の式・Ⅴ 考察・表2〜4 の例・図2 の例 | 式は `rc_beam.theory` に実装 |

`python3 scripts/deliver.py check-inputs` で検査（読み取りだけ・いつでも実行してよい）。不足・不明・矛盾は issues に残し、推測で補わない。

## 2. 計算（`rc_beam.theory`）
- 書式の式をそのまま実装。設計値で計算すると書式の印刷値と 0.6% 以内で一致（差は書式の途中の丸め）→ `tests/test_deliver.py`
- 理論値 CQ: コンクリート Fc → σB、主筋 σy → 実測 σy、あばら筋 σwy → 実測 σy に置き換え、Ⅳ 項をすべて再計算（Ec・KE・Ky・μ を含む）
- 補正たわみ δ = No.1 −(No.2+No.3)/2（講義の指示・書式の考察 1)。データ.txt の変位はこの値の四捨五入と一致することを検査）
- 図2 の三折れ線: 原点 → (2QSC, 2QSC/KE) → 2Qy と 2Qmin の小さい方で頭打ち（書式 p9 の図の作り方）

## 3. Excel（`deliver.py excel --approved`）
- シート: 設計条件（入力と式。計算はすべてセルの数式）・測定データ（全行＋補正たわみ・Q・R・τ の数式）・表2・表3・表4・理論線・各荷重・図
- 図: 散布図・凡例・軸タイトル（単位つき）。図の題はグラフの上に付けず、図の下のセルに書く（講義の指示「グラフとか図のタイトルは下」）
- LibreOffice で再計算して保存し、表4 の値を Python の独立の計算と照合（一致しなければ失敗）。エラーのセルも検出
- 既存の `structlab check-draft <xlsx> --db <G5 の semantic.db の複製>` で、講義の指示（散布図・凡例・近似曲線など）と照合
  （check-draft は --db の DB にチェックの記録を書き足すので、解析結果の DB そのものではなく複製を渡す）

## 4. レポート（`deliver.py report --content content.json --approved`）
- `content.json`（本番で Claude Code が書く。`work/` に置く）: 表紙の欄、本文の章（文・図・表の並び）、考察 1)〜4) の必須（◎）の項目、
  表3 の最大耐力と比べる理論値（`table3_max`: Qmin・Qmean・Qy。書式に指定が無いので本文の破壊モードの判断と合わせて決める）
- 図は matplotlib（荷重–補正たわみ＋三折れ線＋各荷重、上の軸に部材角 R、右の軸に τ。主筋・あばら筋の荷重–ひずみ度）。題は図の下、表の題は表の上
- docx → PDF（LibreOffice）。必須の項目の欠け・【未記入】・表紙の空欄は issues に残し、完成扱いにしない
- 仕上げ: `structlab check-draft <pdf> --db ...`（数値・単位・指示の照合）、`structlab requirements checklist/verify --stage report`
- 文章スタイル PDF は最後の表現の調整にだけ使う（CLAUDE.md）

## 5. 本番の流れ（利用者の許可の後）
1. G5 で 9 資料の解析（読み取り → 理解・統合 → 資料横断）。P-G5（有料、約 1,000 円）は別に承認が必要
2. 講義の指示の一覧（`semantic/directives.md`）と書式から、Excel・レポートの確認リストを作る（`requirements checklist --stage excel|report`）
3. `deliver.py excel` → 検査 → 指示との照合 → 直す
4. Claude Code が `content.json` を書く（計算値は Excel と同じもの。考察は書式の ◎ をすべて、講義の指示に沿って）→ `deliver.py report` → 検査 → 直す
5. `requirements verify --stage excel|report`。「要確認」「未検証」「未実施」「矛盾」は成功ではない
