# モジュール一覧（ソースから自動生成）

各モジュールの先頭の説明（docstring）と、トップレベルのクラス・関数（引数と説明の1行目）。

## `src/structlab/__init__.py`（3 行）

```text
structlab: 建築構造実験資料の逐次・階層化解析ツール。
```


## `src/structlab/__main__.py`（5 行）


## `src/structlab/ai/__init__.py`（93 行）

```text
AIプロバイダの生成と、AI出力の検証。
```

- `make_provider(cfg, allow_external_flag: bool=False)` — (provider or None, 理由)。外部送信は ai.allow_external か --allow-external-ai が必要。
- `make_escalation_provider(cfg, allow_external_flag: bool=False)` — 複雑な部分の読み直しに使う上位モデル（ai.escalation_model）。未設定・送信不可なら None。
- `items_from_response(data: dict[str, Any], corpus: str, *, analyzer: str, page: int | None, t_start: float | None, t_end: float | None)` — AI出力を検証して Item に変換する。AIの出力は無条件に信用しない:

## `src/structlab/ai/anthropic_provider.py`（300 行）

```text
Anthropic Claude による解析。APIキーは環境変数 ANTHROPIC_API_KEY から読む。
```

- 定数 `CACHE` = `{'type': 'ephemeral'}`
- 定数 `PRIME_MIN_GROUP` = `6`
- 定数 `CACHE_MIN_TOKENS` = `512`
- 定数 `MAX_IMAGE_BYTES` = `3700000`
- `encode_image(path, max_edge: int, lossless: bool=False)` — 
- 定数 `STREAM_ABOVE` = `16000`
- 定数 `PHASE_STRIDE` = `1000000`
- `_cache_blocks(p: dict)` — 
- `prefix_key(p: dict)` — キャッシュされる先頭部分（最後のキャッシュの区切りまで）の指紋。区切りが無ければ None。
- `prefix_tokens(p: dict, req: AIRequest | None=None)` — キャッシュされる先頭部分の大まかなトークン数（文字数 + 画像は公式の式 ⌈幅/28⌉×⌈高さ/28⌉）。
- `image_tokens_official(path, max_edge: int=2576)` — 
- class `AnthropicProvider` — 
  - `_content(self, req: AIRequest)` — 
  - `_params(self, req: AIRequest, ttl: str | None=None)` — Batch API 用の1件分の依頼。指示文はキャッシュ（同じ指示の2件目以降は入力が安くなる）。
  - `_params_5m(self, req: AIRequest)` — 
  - `batch_plan(self, reqs: list[AIRequest])` — Batch で送る順序と各依頼の params を決める（API は呼ばない）。
  - `analyze_batch(self, reqs: list[AIRequest], *, poll_seconds: int=60, log=print, known: dict | None=None, on_created=None)` — Message Batches API でまとめて送る（料金は通常の 50%）。各依頼の AIResponse か例外を順に返す。
  - `_run_batch(self, idx: list[int], params: list[dict], out: list, offset: int, poll_seconds: int, log, known: dict | None, on_created)` — 
  - `_response(self, resp)` — 
  - `count_input_tokens(self, req: AIRequest)` — 入力トークン数を数える（推論はしないので課金なし）。見積もりの実測に使う。
  - `analyze(self, req: AIRequest)` — 

## `src/structlab/ai/base.py`（70 行）

```text
AI解析プロバイダの共通インターフェース。モデル・APIはここを実装すれば交換できる。
```

- class `AIImage` — 
- class `AIRequest` — 
- class `AIResponse` — 
- class `AwaitingAnswer` — 答えがまだ無い（Claude Code の回答待ちなど）。失敗ではないので、状態を変えずにこの実行では先へ進まない（次回続きから）。
- class `AIError` — 
- class `AIProvider` — 
  - `analyze(self, req: AIRequest)` — 

## `src/structlab/ai/mock_provider.py`（48 行）

```text
テスト用の決定的なAIプロバイダ（外部送信なし）。

ローカル抽出テキストの先頭行を document_text として返し、
さらに意図的に「根拠が原文に存在しない」推測項目を1件混ぜて、検証機構を試せるようにする。
テキストに "[[AI_FAIL]]" が含まれると一時エラーを発生させる（再試行テスト用）。
```

- class `MockProvider` — 
  - `analyze(self, req: AIRequest)` — 

## `src/structlab/ai/mock_semantic.py`（242 行）

```text
テスト用: 意味理解層の各タスクに対する決定的な応答（外部送信なし）。

実際のAIの理解力を模倣するものではない。多段階の処理（局所理解→全体理解→再解釈→指示抽出→照合→統合）が
正しくつながり、検証機構が働くことを確かめるためのもの。テキスト中の次の印で挙動を変える:
  [[MISS]]     … 指示抽出でわざと拾わない（見落とし検出のテスト）
  [[MISCLASS]] … 指示の向きをわざと逆に分類する（分類ガードのテスト）
  [[NOTDIR]]   … 規則候補を「指示ではない」と判断する
```

- 定数 `_LINE` = `re.compile('^(?:\\[(\\d\\d:\\d\\d:\\d\\d|p\\.\\d+)\\] )?(発話|画面|資料): (.*)$')`
- 定数 `_LABEL2CLS` = `{v: k for k, v in DIRECTIVE_CLASSES.items()}`
- `_sec(t: str | None)` — 
- `_section(text: str, head: str, nxt: str='\n## ')` — 
- `_json_after(text: str, head: str)` — 
- `_lines(sec: str)` — 
- `scene_analysis(text: str)` — 
- `global_synthesis(text: str)` — 
- `scene_reinterpret(text: str)` — 
- `scene_reinterpret_delta(text: str)` — 
- `directive_extraction(text: str)` — 
- `directive_verify(text: str)` — 
- `corpus_integration(text: str)` — 
- `blind_directives(text: str)` — 独立した再検証: 元の範囲だけから規則で指示を列挙する（[[MISCLASS]] の誤りを含まない）。
- `style_rewrite(text: str)` — 文章スタイル: 表現だけを置き換える。[[STYLE_BREAK]] で数値を変え、[[STYLE_INVENT]] で文を書き足す（検査のテスト用）。
- `directive_verify_group(text: str)` — まとめた照合: 各確認対象を1件ずつの照合と同じ規則で判定する。
- 定数 `TASKS` = `{'scene_analysis': scene_analysis, 'global_synthesis': global_synthesis, 'global_synthesis_part': global_synthesis, 'scene_reinterpret': scene_reinterpret, 'sce…`

## `src/structlab/ai/prompts.py`（89 行）

```text
AIへの指示文と出力スキーマ。
```

- 定数 `SYSTEM_PROMPT` = `'あなたは建築構造実験（鋼材の引張試験など）の授業資料を整理する補助者です。\n与えられた「資料の一部分」（PDFの1ページ、動画の一区間、Excelのシート範囲、画像1枚など）だけを根拠に、\n学生がレポートとExcelを作成するために必要な情報を抽出し、指定のJSONで返してください。\n\n厳守事項:\n- 資料…`
- 定数 `CATEGORY_GUIDE` = `'\n'.join((f'- {k}: {v}' for k, v in CATEGORIES.items()))`
- 定数 `TASK_HINTS` = `{'pdf_page': 'PDFの1ページです。本文・図・表・数式を取りこぼさないでください。図や表は何を示すかも書いてください。', 'image': '画像1枚です。画像内の文字・図・数式・表を読み取ってください。', 'video_segment': '動画の一区間です。添付フレーム（時刻付き）と文字起こし（時…`
- `build_user_text(task: str, source_label: str, text: str)` — 

## `src/structlab/ai/queue_provider.py`（96 行）

```text
候補 G5: AI の解析を、従量課金の API ではなく Claude Code（利用者の定額の契約）で行うための受け渡し口。

依頼（指示文・本文・画像・出力の形）は API に送るものと同じものをファイルに書き出す。Claude Code のエージェントが
1 件ずつ読み（画像は元のファイルを開く）、出力の形（JSON スキーマ）に合う答えを書き戻す。答えはスキーマで検査してから使う。
答えの無い依頼は「未回答（あとで再試行）」として返すので、同じコマンドを繰り返すと答えた分だけ先へ進む（途中で止めても続きから）。

  queue/requests/<id>.json  … 依頼（id は依頼の内容の指紋。同じ依頼は同じ id）
  queue/answers/<id>.json   … 答え {"data": {...}, "model": "...", "answered_by": "claude-code"}

使い方は docs/CLAUDE_CODE_QUEUE.md。API と同じ品質かは、見本で API の結果と比べるまで未検証（既定では使わない）。
```

- `request_id(req: AIRequest)` — 
- `request_record(req: AIRequest)` — 
- `validate(data, schema: dict)` — 答えが出力の形（JSON スキーマ）に合うか。合わない点の一覧（空なら合格）。
- class `QueueProvider` — 
  - `_answer(self, req: AIRequest)` — 
  - `analyze(self, req: AIRequest)` — 
  - `analyze_batch(self, reqs: list[AIRequest], *, log=print, known=None, on_created=None, **_)` — 
- `pending(queue_dir: Path)` — 
- `answer(queue_dir: Path, rid: str, data_file: Path, model: str)` — 答えを検査して保存する。戻り値: 形の合わない点（空なら保存した）。

## `src/structlab/ai/tokens.py`（33 行）

```text
画像の入力トークン数（公式の式）。見積もりと、キャッシュの判断に使う。

公式（https://platform.claude.com/docs/en/build-with-claude/vision、2026-10 確認）:
  トークン = ⌈幅/28⌉ × ⌈高さ/28⌉（28×28 画素ごとに 1 トークン）
  Opus 5.5 は高解像度の区分: 長辺 2576 画素・1 枚 4784 トークンまで。超える画像はサーバーで縦横比を保って縮小され、
  縮小後の大きさで数えられる。
```

- 定数 `MAX_EDGE` = `2576`
- 定数 `MAX_TOKENS` = `4784`
- `tokens_for_size(w: float, h: float, max_edge: int=MAX_EDGE)` — 
- `image_tokens(path, max_edge: int=MAX_EDGE, default: int=1500)` — 

## `src/structlab/checks/__init__.py`（4 行）

```text
各工程に組み込むチェックと、独立した再検証。結果は checks テーブルに記録する。
```


## `src/structlab/checks/acquisition.py`（64 行）

```text
資料の取得工程のチェック（A1〜A10）。

取得の検証（全件列挙・アップロード途中の除外・content_hash の照合・ZIPの安全な展開・上限・未対応形式）は
既存の処理（scanner / sources / pipeline）の中で行われている。ここでは再実行せず、その結果をファイルごとに記録する。
```

- 定数 `STAGE` = `'acquisition'`
- `record_acquisition(db: DB, cfg, run_id: int | None=None)` — 

## `src/structlab/checks/base.py`（66 行）

```text
各工程のチェック結果の記録。

状態は5種類に分け、確認できなかったものを「成功」に含めない:
  ok           成功（照合して一致した）
  mismatch     不一致（照合して食い違った）
  needs_review 要確認（機械的には判断できない疑いがある）
  unverified   未検証（照合の材料・手段がない）
  failed       チェック自体が実行できなかった
```

- 定数 `STATUS_LABEL` = `{'ok': '成功', 'mismatch': '不一致', 'needs_review': '要確認', 'unverified': '未検証', 'failed': 'チェック不能'}`
- 定数 `STAGE_LABEL` = `{'acquisition': '資料の取得', 'extraction': '資料抽出', 'understanding': '局所理解・全体理解', 'directives': '指示抽出', 'integration': '複数資料の統合', 'deliverable': '提出物（Excel・レポート）', '…`
- `_s(v: Any)` — 
- `input_hash(*parts: Any)` — 
- `record(db: DB, *, stage: str, check_type: str, target_type: str, target_id: Any, status: str, expected: Any=None, actual: Any=None, detail: str='', file_id: int | None=None, unit_id: int | None=None, run_id: int | None=None, ihash: str='')` — 
- `clear(db: DB, stage: str, *, file_id: int | None=None, check_type: str | None=None, target_type: str | None=None)` — 
- `existing_hash(db: DB, stage: str, check_type: str, target_type: str, target_id: Any)` — 

## `src/structlab/checks/catalog.py`（168 行）

```text
システム全体のチェック項目の一覧（カタログ）。

先にシステム全体として必要な検証要件（R1〜R15）を定め、それを満たすチェック項目を、最も適した工程に割り当てる。
既存の検証も含めて全項目をここに登録し、各工程のチェックを合わせたときに要件が網羅されることを一覧で確認できるようにする。

mode:
  runtime  … 実行のたびに結果を checks テーブルに記録する
  design   … 仕組み（コード・設定の検証）で常に保証し、自動テストで確認する（実行時の記録はしない）
  future   … 提出物の作成など、今は存在しない工程で実行する（下書きを渡せば今でも実行できる）
conditional: その状況のときだけ記録される（例: Excel が含まれるとき、不一致があるときだけの明細）
origin: 前回までに実装済みの検証か（existing）、この改修で追加したか（new）。既存の検証は再実行せず結果を記録する
```

- 定数 `STAGE_ORDER` = `['acquisition', 'extraction', 'understanding', 'directives', 'integration', 'deliverable', 'style', 'reverify', 'operations']`
- `_c(cid, req, stage, check_type, title, how, origin='new', mode='runtime', conditional=False, ai=False, tests=())` — 
- 定数 `BY_TYPE` = `{(c['stage'], c['check_type']): c for c in CATALOG}`
- `coverage_matrix()` — 要件 × 工程 → チェック項目ID

## `src/structlab/checks/deliverable.py`（209 行）

```text
将来の提出物（Excel・レポート）作成時のチェック。

このツールは提出物を作らない。利用者（または将来の作成工程）が用意した下書きを、元資料・計算結果・
指示と照合するだけ。自動で判定できないもの（考察の妥当性など）は「未検証」として一覧にし、成功扱いにしない。

  Excel: 数式の再計算、列見出しの単位、グラフの種類・近似曲線・軸タイトル・凡例と指示の照合
  文章:  数値が元資料・計算結果で確認できるか、単位の欠落、数式が資料に存在するか、
         必須の章（考察など）の有無、提出形式・ページ数などの提出条件、必須・禁止・条件付きの指示の確認リスト
```

- 定数 `STAGE` = `'deliverable'`
- 定数 `_NUM` = `re.compile('(?<![0-9A-Za-z.])([-+]?\\d+(?:\\.\\d+)?)(?:\\s*(N/mm2|N/mm²|mm2|mm²|mm|cm|kN|MPa|GPa|N|kg|με|%))?')`
- 定数 `SECTIONS` = `['目的', '方法', '結果', '考察', '結論', '参考文献', '表紙', '感想']`
- `draft_text(path: Path)` — 
- `_effective_directives(db: DB)` — 
- `_source_numbers(db: DB)` — 元資料の数値（項目付きの数値・Excelの数値セル）。(値, 単位, 出典)
- `_matches(v: float, decimals: int, pool: list[tuple[float, str, str]])` — 
- `check_draft(db: DB, cfg, path: Path, *, fmt: str | None=None, pages: int | None=None, run_id: int | None=None)` — 
- `_excel(db: DB, cfg, path: Path, dirs, rec, run_id)` — 
- `_text(db: DB, text: str, dirs, rec, fmt: str, pages: int | None, path: Path)` — 

## `src/structlab/checks/extraction.py`（357 行）

```text
資料抽出時のチェック（既存の抽出結果を読み、不足していた検査だけを行う）。

- 音声と映像の時刻対応（発話が区間内か、対応付けた画面が発話時点で表示済みか）
- 文字起こし（音声があるのに発話ゼロ、低信頼区間、文字起こし未実施）
- OCR（文字化けの疑い、OCRなしのスキャン資料）
- 数値・数式（数値の誤読の疑い、括弧の対応）
- Excel（数式の再計算とキャッシュ値の照合、エラー値、空セル参照）
- 読み取り失敗（失敗した単位）
既存の検査（ダウンロード時のハッシュ照合、ZIPの安全性、スキャンページ判定など）は重複して行わない。
```

- 定数 `STAGE` = `'extraction'`
- 定数 `_OK_CHARS` = `re.compile('[\u3040-ヿ㐀-鿿０-ｚA-Za-z0-9\\s.,:;()\\[\\]+\\-*/=%、。・「」（）：＝σεΔπ°μ\'\\"!?！？]')`
- 定数 `_RUN` = `re.compile('(.)\\1{5,}')`
- 定数 `_NUM_SUSPECT` = `re.compile('(?<![A-Za-z0-9])(?=[0-9OolI.]*\\d)(?=[0-9OolI.]*[OolI])[0-9OolI][0-9OolI.]*(?![A-Za-z0-9])')`
- `ocr_quality(text: str)` — 
- `suspicious_numbers(text: str)` — 
- `unbalanced(s: str)` — 
- `check_file(db: DB, cfg, frow, local_path: Path | None, run_id: int | None=None)` — 
- `_numbers(db, u, text: str, what: str, kw)` — 
- `_chart_refs(db, u, data, kw)` — 
- `_unit_coverage(db, frow, units, run_id)` — 全ページ・全時間・全行が解析単位で隙間なく覆われ、すべて抽出済みか。
- `_video(db, u, lo, data, kw)` — 
- `_pdf(db, u, lo, rec, data, kw)` — 
- `load_workbook_cells(path: Path, max_cells: int)` — (数式 {(sheet,cell): '=...'}, 値 {(sheet,cell): value}, 上限で打ち切ったか)
- `_resolver(values: dict, formulas: dict | None=None, memo: dict | None=None, stack: set | None=None)` — 参照先が数式セルなら、Excel の保存値を使わず入力値から再帰的に計算し直す（独立した再計算）。
- `recalc_workbook(db: DB, cfg, frow, path: Path, run_id: int | None=None, *, stage: str=STAGE, target_type: str | None=None, prefix: str | None=None)` — 全数式を保存値から独立に再計算し、Excel が保存した計算結果と照合する。

## `src/structlab/checks/formula.py`（338 行）

```text
Excel の数式をプログラムで再計算するための小さな評価器。

Excel が保存した計算結果（キャッシュ値）とは独立に、セルの値から数式を計算し直して照合する。
対応していない関数・構文は Unsupported を送出し、呼び出し側は「未検証」として記録する
（対応していないものを「一致」と扱わない）。
```

- 定数 `ERRORS` = `('#DIV/0!', '#REF!', '#VALUE!', '#NAME?', '#N/A', '#NUM!', '#NULL!')`
- class `Unsupported` — 
- class `FormulaError` — 数式の評価中に Excel のエラー値に相当する状態になった。
- 定数 `_TOKEN` = `re.compile('\n    (?P<ws>\\s+)\n  | (?P<str>"(?:[^"]|"")*")\n  | (?P<ref>(?:(?:\'[^\']+\'|[A-Za-z_][\\w.]*)!)?\\$?[A-Za-z]{1,3}\\$?\\d+(?::\\$?[A-Za-z]{1,3}\\$?…`
- `tokenize(s: str)` — 
- `col_to_num(c: str)` — 
- `num_to_col(n: int)` — 
- `split_ref(ref: str, default_sheet: str)` — 
- `expand_range(a: str, b: str)` — 
- `_nums(args: list)` — 
- `_pairs(ys, xs)` — 
- `_slope(ys, xs)` — 
- `_intercept(ys, xs)` — 
- `_round(x, n=0, mode='half')` — 
- `_div0(f)` — 
- class `Evaluator` — resolve(sheet, cell) → セルの値（数値・文字列・None）。数式セルの場合も保存値を返す想定。
  - `evaluate(self, formula: str)` — 
  - `_peek(self)` — 
  - `_eat(self, val=None)` — 
  - `_compare(self)` — 
  - `_concat(self)` — 
  - `_num(v)` — 
  - `_add(self)` — 
  - `_mul(self)` — 
  - `_pow(self)` — 
  - `_unary(self)` — 
  - `_primary(self)` — 
  - `_cell(self, sh: str, c: str, in_range: bool=False)` — 
- `close_enough(a: Any, b: Any, rel: float=1e-09, abs_tol: float=1e-09)` — 

## `src/structlab/checks/integration.py`（165 行）

```text
複数資料の統合時のチェック: 動画・PDF・Excel の間で、数値・提出条件・指示・作業手順の一致と矛盾。

- 数値: 「断面積は78.5 mm2」のような 項目＋数値＋単位 を資料ごとに抜き出し、同じ項目・単位で値が違えば不一致
- Excel: 見出し行（例:「断面積(mm2)」）の下に数値が1つだけある列を定数として、資料の数値と照合
- 提出条件: 提出形式・期限・枚数・有効数字（既存の keyed_facts を利用）
- 指示: 資料間で向きが逆の指示（既存の関連付けの結果を集計）
- 作業手順: 統合理解の手順が実在する資料を根拠にしているか
```

- 定数 `STAGE` = `'integration'`
- 定数 `UNIT_NORM` = `{'mm²': 'mm2', 'mm^2': 'mm2', 'N/mm²': 'N/mm2', 'N/mm^2': 'N/mm2', '㎜': 'mm', '㎝': 'cm', '％': '%'}`
- 定数 `_UNITS` = `'(?:N/mm2|N/mm²|N/mm\\^2|mm2|mm²|mm\\^2|mm|cm|kN|MPa|GPa|N|kg|με|%|％|秒|分)'`
- 定数 `_FACT` = `re.compile('(?P<term>[一-龥ァ-ヴーA-Za-z]{2,12}?)(?:は|が|：|:|=|＝)\\s*(?P<num>[-+]?\\d+(?:\\.\\d+)?)\\s*(?P<unit>' + _UNITS + ')')`
- 定数 `_HEAD` = `re.compile('^(?P<term>[^()（）]+?)\\s*[（(](?P<unit>[^()（）]+)[)）]\\s*$')`
- `_unit(u: str)` — 
- `numeric_facts(text: str)` — 
- `excel_constants(db: DB, fid: int, fhash: str)` — 見出し「項目(単位)」の列に数値が1つだけある場合、その数値を定数とみなす。(項目, 値, 単位, セル)
- `check_corpus(db: DB, run_id: int | None=None)` — 

## `src/structlab/checks/operations.py`（78 行）

```text
運用・全体のチェック（O1〜O5）と、実行の最後にまとめて記録する抽出工程の項目（B14・B15）。

B14（AIでしか読めない内容）・B15（単位ごとのAI抽出の根拠）は抽出工程の担当だが、意味理解のAIが後から
同じ単位を解析することがあるため、実行の最後の時点の状態で記録する。いずれも既存の結果の集約で、再解析はしない。
```

- `record_operations(db: DB, cfg, run_id: int | None=None, *, ai_note: str='', ai_blocked: str | None=None, external_allowed: bool=False)` — 
- `_extraction_tail(db: DB, run_id)` — 
- `_semantic_coverage(db: DB, run_id)` — 

## `src/structlab/checks/report.py`（95 行）

```text
チェック結果の出力（results/checks/）。
```

- 定数 `ORDER` = `['mismatch', 'failed', 'needs_review', 'unverified', 'ok']`
- `summary(db: DB)` — 
- 定数 `MODE_LABEL` = `{'runtime': '実行時に記録', 'design': '仕組みで保証（テストで確認）', 'future': '提出物・最終工程で実行'}`
- `catalog_rows(db: DB)` — カタログの各項目に最新の結果を付ける。カタログに無いチェック（未登録）も返す。
- `write_catalog_report(db: DB, cfg)` — 
- `write_checks_report(db: DB, cfg)` — 

## `src/structlab/checks/reverify.py`（171 行）

```text
独立した再検証（structlab reverify）。

各工程内のチェックとは別に、すでに出ている結論（指示とその分類・条件）を正解として扱わず、
元の発話・画面・資料の該当範囲だけから指示を取り出し直して、機械的に突き合わせる。

  1. 規則による再抽出（常に・無料）: 元の範囲の文から言い回しで指示を拾い直す
  2. AIによる再抽出（--allow-external-ai のときだけ・有料）: 最初の解釈・全体理解・対象の指示を一切見せず、
     元の範囲だけを渡して指示を列挙させる（盲検）
  3. 突き合わせ: 対象の近い指示が見つかるか、分類・条件が一致するか
     食い違いは revisions に「修正案（未適用）」として前後と理由を残す。自動で書き換えない。
     どちらの再抽出でも確認できないものは「未検証」とし、検証済みにしない。

優先順位: 要確認・不一致・見落とし候補 → 必須・禁止・条件付き（重要） → 確実性が低いもの。
同じ入力で再検証済みのものは繰り返さない（既存の照合との無意味な重複を避ける）。
```

- 定数 `STAGE` = `'reverify'`
- 定数 `BLIND_SCHEMA` = `_obj({'directives': {'type': 'array', 'items': _obj({'content': _S, 'directive_class': {'type': 'string', 'enum': CLASS_ENUM}, 'base_strength': STRENGTH_ENUM, '…`
- 定数 `BLIND_INSTRUCTION` = `f"## 作業（独立した再検証: 指示の列挙）\n次の元の発話・画面・資料だけを読み、ここに含まれる指示（禁止・非推奨・必須・要望・推奨・任意・条件付き・その他）を\n1件ずつすべて列挙してください。他の解析結果は与えていません。原文に無いことを推測で加えないでください。\nquote には根拠の原文をそのまま引用して…`
- 定数 `SIM_MATCH` = `0.4`
- `_eff(cls: str | None, base: str | None)` — 
- `_compare(target, cand_cls: str | None, cand_base: str | None, cand_cond: str | None)` — 
- `targets(db: DB, limit: int)` — 
- `reverify(db: DB, cfg, *, provider=None, budget=None, run_id: int | None=None, limit: int | None=None, expand: float=30.0)` — 
- `_blind(db: DB, provider, budget, run_id, frow, d, win, wtext: str, max_img: int)` — 
- `summary_json(db: DB)` — 

## `src/structlab/checks/semantic_checks.py`（232 行）

```text
局所理解・全体理解時と、指示抽出時のチェック。

既存の検証（根拠の引用照合・分類の向き/強さの検査・見落とし候補・第3回照合・訂正/矛盾の関連付け）はそのまま使い、
ここでは次の不足分だけを行う:
  理解: 時刻・場面番号の参照の妥当性、訂正/補足/例外/条件変更や整合説明の根拠が原文にあるか、
        規則が原文から検出した訂正と全体理解の訂正の一致、相互照合の収束
  指示: 発言者（先生と断定してよいか）、対象作業、条件、および既存の照合状態の集計
```

- 定数 `_EV_STATUS` = `{'evidence_matched': 'ok', 'evidence_not_found': 'needs_review', 'no_local_text': 'unverified'}`
- `check_understanding(db: DB, doc, frow, scenes: list[Scene], run_id: int | None=None)` — 
- `_scene_level(db, did, scenes, kw)` — 
- `check_directives(db: DB, doc, frow, cfg, run_id: int | None=None)` — 
- 定数 `_RESOLVED` = `('verified', 'revised')`
- `_directive_details(db, did, frow, doc, ds, kw)` — 既存の照合結果（第3回照合・関連付け）を、要件ごとの項目として記録する（照合は再実行しない）。

## `src/structlab/classify.py`（119 行）

```text
ローカル規則による分類候補の抽出と、AI結果の根拠照合。

AIを使わない場合でも、資料・発言の原文から10分類の「候補」を抜き出す。
これは要約ではなく原文の抜粋であり、推測で補完しない。
```

- 定数 `_COMPILED` = `{k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in RULES.items()}`
- 定数 `_SENT_SPLIT` = `re.compile('(?<=[。．！？!?])\\s*|\\n+')`
- `split_sentences(text: str)` — 
- `classify_sentence(s: str)` — 
- `rule_items(segments: Iterable[dict], max_items: int=400)` — segments: {text, origin, page?, t_start?, t_end?}
- `normalize(s: str)` — 
- `verify_evidence(evidence: str, corpus: str)` — AI が示した根拠（引用）がローカル抽出テキストに含まれるか照合する。
- `keyed_facts(text: str)` — 

## `src/structlab/cli.py`（852 行）

```text
コマンドライン: structlab <command>

init         設定ファイル config.yaml を作成
check        実行環境・接続・設定を確認（元資料は読むだけ）
scan         ファイル一覧の取得と新規・更新・削除の検出だけを行う
run          スキャン → 解析 → 結果出力（途中停止しても次回続きから）
status       処理状況を表示
report       解析結果（Markdown/JSON）を再生成
retry        失敗した部分を再試行対象に戻す
reanalyze    指定ファイルを強制的に再解析
errors       未解決のエラー一覧
estimate     AI解析（有料）を始める前の呼び出し回数・費用の概算（APIは呼ばない）
search       指示・場面の理解をキーワード検索
show-directive 指示1件の根拠・前後の文脈・関連・照合結果を表示
dropbox-auth Dropbox のリフレッシュトークン取得（PKCE・読み取り専用。値は表示しない）
connect-check Dropbox・Claude API の接続確認（読み取りのみ・課金なし）
read         資料の読み取り基盤（棚卸し・場面分割・OCR・文字起こし・AIの書き起こし・照合）
```

- `_setup_logging(cfg, verbose: bool)` — 
- class `RunLock` — 同じ作業ディレクトリで2つの処理が同時に走らないようにする。
- class `Syncer` — 
  - `pull_state(self)` — 
  - `maybe_push(self)` — 
  - `push(self, results: bool=True)` — 
- `cmd_init(args)` — 
- `cmd_check(args)` — 
- `_open_db(cfg)` — 
- `cmd_scan(args)` — 
- `run_once(cfg, args)` — 
- `cmd_run(args)` — 
- `cmd_status(args)` — 
- `cmd_report(args)` — 
- `cmd_retry(args)` — 
- `cmd_reanalyze(args)` — 
- `cmd_errors(args)` — 
- `cmd_estimate(args)` — AI解析（有料）を始める前の概算。APIは呼ばない。
- `cmd_search(args)` — 
- `cmd_show_directive(args)` — 
- `cmd_checks(args)` — 
- `cmd_reverify(args)` — 独立した再検証。AIは --allow-external-ai のときだけ（有料）。
- `cmd_check_draft(args)` — 提出物の下書き（Excel・文章）を元資料・計算・指示と照合する（提出物は作らない）。
- `cmd_style(args)` — 文章スタイル（最終工程のみ）。profile: PDFの読み込み、apply: 確定した文章の表現調整。
- `cmd_feedback(args)` — 教授などのフィードバックを、内容を推測せずにそのまま記録する。
- `cmd_trace(args)` — 
- `cmd_dropbox_auth(args)` — PKCE でリフレッシュトークンを取得し、権限600のファイルに保存する（値は表示しない）。
- `cmd_read(args)` — 資料の読み取り（棚卸し・ローカル読み取り・AIの書き起こし（許可時）・照合）。元資料は読むだけ。
- `cmd_requirements(args)` — 要求事項の台帳の照合（解析の完了時・Excel 作成時・レポート作成時）。
- `cmd_cost(args)` — 費用の発生箇所（工程・依頼の種類ごと）と最適化ごとの効果（API は呼ばない）。
- `cmd_pilot(args)` — 費用を下げる候補の品質の比較（小さな見本で、今の方式×2 と候補を比べる）。
- `cmd_queue(args)` — 候補 G5: Claude Code で解析する依頼の受け渡し（status / next / show / answer）。
- `cmd_connect_check(args)` — 
- `main(argv: list[str] | None=None)` — 

## `src/structlab/config.py`（246 行）

```text
設定ファイル（YAML）の読み込み。秘密情報は環境変数からのみ読む。
```

- 定数 `FORBIDDEN_KEYS` = `{'api_key', 'access_token', 'refresh_token', 'app_secret', 'token', 'password'}`
- class `ConfigError` — 
- `_deep_merge(base: dict, override: dict)` — 
- `_check_no_secrets(d: Any, path: str='')` — 
- class `Section` — dict だが属性アクセスもできる。
- class `Config` — 
  - `work_dir(self)` — 
  - `db_path(self)` — 
  - `results_dir(self)` — 
  - `cache_dir(self)` — 
  - `validate(self)` — 
- `load_config(path: str | os.PathLike | None=None, overrides: dict | None=None)` — 

## `src/structlab/connect.py`（374 行）

```text
接続確認（`structlab connect-check`）。読み取りのみで、Dropbox には何も書き込まない。

1. 認証情報の有無（値は表示しない）
2. Dropbox: トークンの更新、解析対象フォルダの存在、ファイル一覧（種類別の件数・フォルダ別の件数・例）
3. 既存の解析プログラムへの接続: 一覧を一時DBで scan し（アップロード完了待ちの判定を含む）、
   動画・PDF・Excel を種類ごとに1件（最小のもの）だけ取得して、ハッシュ照合と解析単位への分割（plan）まで行い、削除する
4. Claude API: 無料のモデル一覧の取得だけでキーの有効性を確認する（有料の解析は呼ばない）

GitHub Actions では Secrets の登録状況（SECRET_SET_<名前>=true/false）も表示する。
```

- 定数 `SAMPLE_KINDS` = `('video', 'pdf', 'excel')`
- 定数 `KIND_LABEL` = `{'video': '動画', 'audio': '音声', 'pdf': 'PDF', 'excel': 'Excel', 'excel_legacy': 'Excel(旧.xls)', 'image': '画像', 'zip': 'ZIP', 'text': 'テキスト', 'docx': 'Word', 'ppt…`
- 定数 `ANTHROPIC_SECRET_NAMES` = `('ANTHROPIC_API_KEY', 'CLAUDE_API_KEY', 'ANTHROPIC_KEY', 'CLAUDE_KEY')`
- class `_ListedSource` — 一覧を一度だけ取得して scan に渡す（Dropbox への問い合わせを二重にしない）。
  - `iter_files(self)` — 
- `_err(e: Exception)` — 
- `_dropbox_error_hint(e: Exception)` — 
- `run_connect_check(cfg, *, download: bool=True, claude: bool=True, max_sample_mb: int=1000, out: Callable[[str], None]=print, client=None, ai_client=None, summary_path: str | None=None, http=None)` — 
- 定数 `TOKEN_URL` = `'https://api.dropboxapi.com/oauth2/token'`
- 定数 `AUTHORIZE_URL` = `'https://www.dropbox.com/oauth2/authorize'`
- `diagnose_oauth(cred: dict, say, http=None)` — OAuth 設定の診断（コードやトークンを新しく作らない・何も書き込まない）。
- class `_Stop` — 
- `_not_found_hint(dbx, root: str, say)` — 
- `_summarize_listing(files: list[RemoteFile], cfg, say, res)` — 
- `_scan_dry(files, cfg, tmp: Path, say, res)` — 
- `_sample_downloads(files, cfg, source, tmp: Path, max_mb: int, say, fail, res)` — 
- `_claude_check(cfg, ai_client, say, fail, res)` — 

## `src/structlab/costmodel.py`（225 行）

```text
費用の発生箇所を、工程・依頼の種類ごとに分けて見積もる（API は呼ばない）。

入力: 読み取りの結果（reading.json と保存した画面）と、意味理解の場面（semantic.db。読み取り結果から作ったもの）。
実測できる量（場面の数、発話・画面の文字数、画像の大きさ）は実際の値を使い、まだ測れない量（AI が書く量、
資料全体の理解の長さなど）は low / mid / high の仮定で幅を持たせる。仮定は ASSUME にまとめてある。

最適化ごとの効果（何円減るか）も、同じ式で「その最適化が無い場合」と比べて出す。

  structlab cost --reading <読み取り結果>... --semantic-db <semantic.db> --out <出力先>
```

- 定数 `YEN_PER_USD` = `155`
- 定数 `PRICE` = `{'in': 4.0, 'out': 20.0}`
- 定数 `CASES` = `('low', 'mid', 'high')`
- 定数 `ASSUME` = `{'tok_per_char': (0.8, 1.0, 1.2), 'global_brief_chars': (8000, 14000, 20000), 'scene_analysis_chars': (1500, 2500, 4000), 'reading_ai_chars_per_unit': (1500, 25…`
- 定数 `PRIME_MIN_GROUP` = `6`
- 定数 `SYS_TOKENS` = `400`
- 定数 `INSTR` = `{'scene': 430, 'synthesis': 420, 'reinterpret': 250, 'directives': 1130, 'verify': 360}`
- `_a(name, c)` — 
- `usd(inp: float, out: float, batch: bool=True)` — 
- `cached_cost_factor(hit: float, ttl_1h: bool=False)` — キャッシュする先頭部分 1 トークンあたりの入力の倍率（書き込み 2 倍〔1 時間〕/1.25 倍、読み出し 0.05 倍）。
- `prefix_units(n: float, mode: str, case: str)` — 同じ先頭部分（P トークン）を持つ n 件の依頼で、先頭部分にかかる入力を「P の何倍か」で返す。
- `_semantic(db_path: Path, case: str, directive_prefix_cache: bool, prefix_layout: bool, compact: bool, batch: bool, verify_group: int=1, parallel: bool=True, priming: bool=True, only: str | None=None, layout_scope: str='both', reinterpret_delta: bool=False)` — 意味理解の費用。parallel: 指示の抽出・照合を資料ごとに 1 回の Batch にまとめる（内容は同じ）。
- `semantic(db_path: Path, case: str='mid', **kw)` — 
- `relocate(rec: dict, base: Path)` — 成果物を別の場所に展開した読み取り結果の画像のパスを、展開先（base/frames, base/pages）に直す（見積もり用）。
- `report(reading_dirs: list[Path], db_path: Path | None, out: Path)` — 工程ごと・最適化ごとの費用（low / mid / high）を書く。

## `src/structlab/db.py`（414 行）

```text
SQLite による処理状況・解析結果の管理。

テーブル:
  files     … 検出したファイル（ZIP内部のファイルも parent_id 付きで登録）
  units     … 解析単位（PDFページ・動画区間・シート範囲・画像など）。再開・再試行の単位
  items     … 分類済みの知見（出典・時刻・由来・検証状態付き）
  timeline  … 動画の音声・映像の時刻対応
  errors    … 失敗記録
  api_usage … AI API の使用量
  runs      … 実行履歴
  kv        … 任意のキー値
```

- 定数 `SCHEMA_VERSION` = `1`
- 定数 `SCHEMA` = `"\nCREATE TABLE IF NOT EXISTS files (\n    id INTEGER PRIMARY KEY,\n    path TEXT NOT NULL,\n    path_key TEXT NOT NULL UNIQUE,\n    parent_id INTEGER REFERENCE…`
- 定数 `MIGRATIONS` = `[('directives', 'speaker', 'TEXT')]`
- class `DB` — 
  - `close(self)` — 
  - `tx(self)` — 
  - `q(self, sql: str, args: tuple | list=())` — 
  - `q1(self, sql: str, args: tuple | list=())` — 
  - `x(self, sql: str, args: tuple | list=())` — 
  - `get_kv(self, key: str, default: str | None=None)` — 
  - `set_kv(self, key: str, value: str)` — 
  - `start_run(self)` — 
  - `end_run(self, run_id: int, status: str, stats: dict)` — 
  - `log_error(self, *, run_id: int | None, stage: str, message: str, detail: str='', file_id: int | None=None, unit_id: int | None=None)` — 
  - `recover_interrupted(self)` — 前回の異常終了で running のまま残った単位・ファイルを pending/ready に戻す。
  - `add_items(self, rows: list[dict[str, Any]])` — 
  - `usage_totals(self, run_id: int | None=None)` — 
  - `backup_to(self, dest: Path)` — 実行中でも整合性のあるコピーを作る（Dropbox 同期用）。

## `src/structlab/media.py`（478 行）

```text
ffmpeg / tesseract / 文字起こしのラッパー。動画は区間ごとに切り出して処理し、全体をメモリに載せない。
```

- class `MediaError` — 
- `_run(cmd: list[str], timeout: int=1800, capture: bool=True)` — 
- class `ProbeInfo` — 
- `probe(path: Path)` — 
- `extract_audio(path: Path, start: float, length: float, out_wav: Path)` — 
- `sample_gray_frames(path: Path, start: float, length: float, interval: float, w: int=320, h: int=180, crop: str | None=None)` — 区間内を interval 秒ごとに低解像度グレースケールでサンプリング（シーン検出用）。
- `iter_gray_frames(path: Path, start: float, length: float, interval: float, w: int, h: int, crop: str | None=None)` — sample_gray_frames と同じだが1枚ずつ返す（細かい間隔で長い動画を見てもメモリを使わない）。
- `save_lossless(src_png: Path)` — ffmpeg で取り出した PNG を WebP（可逆）にする。元の画素は1つも変えずに容量を小さくする。
- `scene_scores(frames: list[tuple[float, np.ndarray]], pixel_delta: int=24)` — 直前のサンプルから「明るさが pixel_delta 以上変わった画素の割合」(0〜1)。
- class `Region` — 動画の中で資料（スライド・アプリ画面）が映っている矩形（元動画の画素座標）。
  - `is_full(self)` — 
  - `ffmpeg_crop(self)` — 
  - `vf(self)` — 回転の補正と切り出しの ffmpeg フィルタ（どちらも不要なら None）。
  - `as_dict(self)` — 
- 定数 `TRANSPOSE` = `{90: 'transpose=2', 270: 'transpose=1'}`
- `_runs(flags: np.ndarray, max_gap: int=0)` — True が続く区間。max_gap 以下の短い途切れ（資料内の罫線など）はつなげる。
- `grab_gray(path: Path, t: float, w: int, h: int, crop: str | None=None)` — 
- `ui_orientation(frame: np.ndarray)` — 画面録画の1フレーム（縮小したグレースケール）で、ブラウザ・端末の明るい帯がどこにあるかから画面の向きを推定する。
- `orientation_runs(times: list[float], frames: list[np.ndarray], min_len: int=3)` — フレームごとの向きを、min_len 回未満の短い揺れを前後に合わせてならし、(開始, 終了, 向き) の区間にする。
- `detect_content_region(path: Path, info: ProbeInfo, start: float=0.0, length: float | None=None, samples: int=16, work_w: int=360, rotate: int=0)` — 画面録画（スマホで再生したスライドなど）で、時計・アドレスバー・黒い余白を除いた資料の領域を求める。
- `_content_box_once(stack: np.ndarray, frac: float, edge: float, line: float=0.25)` — 
- `crop_is_safe(path: Path, t: float, region: Region, limit: float=0.02)` — 時刻 t の画面で、切り出す範囲の外が普段の姿（余白・ブラウザの表示）のままかを確かめる。
- `outside_activity(stack: np.ndarray, box: tuple[int, int, int, int], pad: int=3)` — 切り出す範囲（少し広げる）の外で、フレーム間で明るさが大きく変わる画素の割合。
- `_player_frame(sub: np.ndarray)` — 領域の外周が、ほぼ一様な暗い余白（ブラウザの動画プレーヤーの背景）かどうか。
- `content_box(stack: np.ndarray, max_depth: int=2)` — 縮小した画面（フレーム×高さ×幅）から資料の領域 (上, 下, 左, 右) を求める。
- `extract_frame(path: Path, t: float, out_jpg: Path, max_width: int=1280, region: Region | None=None, min_width: int=0)` — t 秒のフレームを保存。region があれば資料の領域だけを切り出し、min_width まで拡大（小さい文字のOCR・AI用）。
- class `OCR` — 
  - `label(self)` — 
  - `image_to_text(self, image: Path)` — 
- class `Transcriber` — 
  - `transcribe(self, wav: Path, offset: float)` — 戻り値: [{start, end, text}]（動画先頭からの絶対秒）
- class `MockTranscriber` — テスト用: 設定 transcription.mock_script（[{start,end,text}]）のうち区間に重なるものを返す。
  - `transcribe(self, wav: Path, offset: float)` — 
- class `FasterWhisperTranscriber` — 
  - `transcribe(self, wav: Path, offset: float)` — 
- `make_transcriber(cfg)` — 

## `src/structlab/models.py`（106 行）

```text
分類カテゴリ・由来・ファイル種別などの定義。
```

- `kind_for(path: str)` — 
- class `RemoteFile` — ソース（Dropbox/ローカル）上のファイル情報。
- class `UnitSpec` — 
- class `Item` — 
- `fmt_time(sec: float | None)` — 

## `src/structlab/pilot.py`（233 行）

```text
費用を下げる候補の「品質が同じか」を、本番の前に小さな見本で確かめる（確かめられた候補だけを使う）。

AI の出力は同じ依頼でも毎回少し違うため、次の3つを比べる:
  baseline … 今の方式
  repeat   … 今の方式をもう一度（同じ方式どうしの揺れの大きさ）
  candidate… 候補の方式
採用の条件（どちらも満たすときだけ採用。満たさなければ今の方式のまま）:
  1. baseline の項目が candidate に含まれる割合 ≥ baseline の項目が repeat に含まれる割合 − 許容差（既定 0.02）
  2. 重要な項目（禁止・必須の指示、数値、数式、セル番地）で、candidate だけが落としたものが repeat より多くない

  structlab pilot compare --kind reading|semantic --baseline DIR --repeat DIR --candidate DIR --out DIR
  structlab pilot plan     （候補・見本・確かめるための費用・見込める削減の一覧）
```

- 定数 `NUM` = `re.compile('(?<![A-Za-z])[-+]?\\d+(?:[.,]\\d+)?')`
- 定数 `CELL` = `re.compile('\\b[A-Z]{1,3}\\$?\\d{1,5}\\b')`
- 定数 `CRITICAL_CLASSES` = `{'prohibited', 'mandatory', 'conditional'}`
- 定数 `CANDIDATES` = `[{'id': 'C1', 'config': {'semantic.cache_layout': 'prefix'}, 'opts': {'prefix_layout': True}, 'what': '意味理解の再解釈・照合で、指示文と資料全体の理解を依頼の先頭（照合では画像より前）に置いてキャッシュする（内容は同…`
- 定数 `ARM_OPTS` = `{'C1v': {'prefix_layout': True, 'layout_scope': 'verify'}, 'C4': {'verify_group': 5}, 'C1v+C4': {'prefix_layout': True, 'layout_scope': 'verify', 'verify_group'…`
- 定数 `STAGE_ROWS` = `{'verify': ('3 元資料との照合',), 'reinterpret': ('1 再解釈', '1 全体の理解', '2 指示の抽出', '3 元資料との照合'), 'all': None}`
- `plan_costs(db_path: Path | None, sample: str='事前説明1')` — 候補ごとの、見本の比較にかかる追加の費用と本番 1 回あたりの削減（mid）。
- `_norm(s: str)` — 
- `reading_items(ai: dict | None)` — 読み取り結果から比べる項目（数値・セル番地・数式・操作・設定・音声の要点・本文）。
- 定数 `VERIFY_VERDICTS` = `{'verified', 'revised', 'rejected', 'needs_review', 'class_mismatch_unresolved', 'found_in_verify'}`
- `semantic_items(directives: list[dict])` — 指示の一覧から比べる項目。照合（第3回）の判定も比べる（照合の段階だけを比べる見本で使う）。
- `_contains(item: str, pool: set[str], fuzzy: bool)` — 
- `containment(a: dict[str, set[str]], b: dict[str, set[str]], fuzzy: bool=False)` — 
- 定数 `CRITICAL_KINDS` = `{'reading': ('number', 'cell', 'formula', 'setting'), 'semantic': ('critical',)}`
- `decide(kind: str, base: dict, repeat: dict, cand: dict, tol: float=0.02)` — 
- `load_reading(d: Path)` — 
- `load_semantic(d: Path)` — 
- `compare(kind: str, baseline: Path, repeat: Path, candidate: Path, tol: float=0.02)` — 
- 定数 `ARMS` = `{'C1v': {'stage': 'verify', 'semantic': {'cache_layout': 'prefix_verify'}}, 'C4': {'stage': 'verify', 'semantic': {'verify_group': 5}}, 'C1v+C4': {'stage': 'ver…`
- `branch_run(cfg, db_path: Path, record: dict, arm: str | None, out: Path, provider, log=print)` — 見本の資料 1 件について、DB を複製して指定の段階から先を実行する（arm=None なら今の方式）。
- `branch_compare(cfg, db_path: Path, record: dict, arm: str, out: Path, provider, log=print)` — 段階を分けた比較を 1 回で行う: 今の方式で 2 回（baseline・repeat）と候補（candidate）を実行して比べる。

## `src/structlab/pipeline.py`（614 行）

```text
解析パイプライン: ファイル → 解析単位 → ローカル抽出 → （許可があれば）AI解析 → DB保存。

- 単位ごとに結果を確定させるので、途中で止まっても次回は未完了の単位から再開する
- 同じ content_hash の解析済み単位は再実行しない。ファイルが更新されれば新しいハッシュで解析し直す
- 失敗した単位は記録し、max_attempts まで次回以降に再試行する
```

- class `StopRun` — 安全に処理を中断する（次回続きから）。
- class `Budget` — 
  - `check(self)` — 
- class `Pipeline` — 
  - `_semantic_ai_call(self, req: AIRequest, uid: int | None)` — 意味理解層からの AI 呼び出し。使用量の上限・記録は単位ごとの解析と共通。
  - `_record_run_checks(self)` — 取得工程（A）と運用・全体（O）の項目、実行の最後の状態で記録する抽出工程の項目（B14・B15）。
  - `_run_extraction_checks(self, row, local_path: Path | None)` — 資料抽出工程に組み込んだチェック（時刻対応・文字起こし・OCR・数値・数式・Excel再計算・読み取り失敗）。
  - `_run_semantic(self, row, local_path: Path | None)` — 
  - `install_signal_handlers(self)` — 
  - `should_stop(self)` — 
  - `_checkpoint(self)` — 
  - `run(self)` — 
  - `_set_file(self, fid: int, status: str, detail: str | None=None, **extra)` — 
  - `_file_error(self, row, stage: str, err: BaseException, retryable: bool=True)` — 
  - `process_file(self, row, local_path: Path | None=None, depth: int=0)` — 
  - `_ctx(self, fid: int, unit_id: int | None)` — 
  - `_process_units(self, row, local_path: Path)` — 
  - `_finalize_file(self, row)` — 
  - `_source_label(self, row, proc, spec: UnitSpec)` — 
  - `_process_unit(self, row, u, proc, local_path: Path)` — 
  - `_extract_record(ex: Extraction)` — 
  - `_item_row(self, row, uid: int, it: Item)` — 
  - `_ai_unit(self, row, uid: int, spec: UnitSpec, proc, ex: Extraction, page: int | None)` — 
  - `_focus_covered(self, row, t: float)` — 
  - `_ai_failed(self, row, uid: int, e: BaseException, retryable: bool, n_images: int)` — 
  - `ai_backfill(self)` — ローカル抽出は済んでいるが AI 解析が未完了の単位を処理する（許可・予算が後から得られた場合など）。
  - `_process_zip(self, row, zpath: Path, depth: int)` — 

## `src/structlab/processors/__init__.py`（29 行）

- 定数 `UNSUPPORTED_HINTS` = `{'excel_legacy': '旧形式 .xls は未対応です。Excel で .xlsx に保存し直してください。', 'other': '未対応の形式です（一覧には記録済み）。'}`
- `get_processor(kind: str)` — 

## `src/structlab/processors/base.py`（66 行）

```text
ファイル種別ごとの処理器の共通部分。

処理器は (1) plan: ファイルを小さな解析単位に分割 (2) extract: 1単位をローカル抽出
の2段階で動く。AI解析はパイプライン側が Extraction から組み立てる。
```

- class `Extraction` — 
- class `Ctx` — 
- class `Processor` — 
  - `plan(self, path: Path, ctx: Ctx)` — 
  - `extract(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 
  - `label(self, unit: UnitSpec)` — 
- class `UnsupportedFile` — 

## `src/structlab/processors/excel.py`（231 行）

```text
Excel (.xlsx/.xlsm): シート構造・セル・数式・グラフ定義を調査する。マクロは実行しない。
```

- 定数 `NS` = `{'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main', 'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'rel': 'http://sc…`
- 定数 `MAX_XML` = `50 * 1024 * 1024`
- 定数 `CHART_TYPES` = `{'lineChart': '折れ線', 'scatterChart': '散布図', 'barChart': '棒', 'bar3DChart': '3D棒', 'pieChart': '円', 'areaChart': '面', 'radarChart': 'レーダー', 'bubbleChart': 'バブル',…`
- `_read_xml(zf: zipfile.ZipFile, name: str)` — 
- `_rels(zf: zipfile.ZipFile, part: str)` — 
- `_texts(el)` — 
- `parse_chart(zf: zipfile.ZipFile, part: str)` — 
- `workbook_structure(path: Path)` — 
- `_norm_formula(f: str)` — コピーされた数式をまとめるため、行番号を # に置換。
- class `ExcelProcessor` — 
  - `plan(self, path: Path, ctx: Ctx)` — 
  - `label(self, unit: UnitSpec)` — 
  - `extract(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 
  - `_structure(self, path: Path)` — 
  - `_sheet(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 

## `src/structlab/processors/image.py`（53 行）

```text
画像: 文字（OCR）・図・数式・表はAI解析、ローカルでは寸法とOCRのみ。
```

- class `ImageProcessor` — 
  - `plan(self, path: Path, ctx: Ctx)` — 
  - `label(self, unit: UnitSpec)` — 
  - `extract(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 

## `src/structlab/processors/pdf.py`（102 行）

```text
PDF: ページ単位でテキスト・表・図・数式候補を抽出する。
```

- 定数 `MATH_CHARS` = `re.compile('[=＝σεΔδπΣ∑∫√×÷±≦≧≤≥∝∞²³⁻¹αβγλμφθ]|\\^|/\\s*[A-Za-z0-9(]')`
- 定数 `MATH_FONT` = `re.compile('math|symbol|cmmi|cmsy|cmex|stix|cambria', re.IGNORECASE)`
- `_open(path: Path)` — 
- class `PDFProcessor` — 
  - `plan(self, path: Path, ctx: Ctx)` — 
  - `label(self, unit: UnitSpec)` — 
  - `extract(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 

## `src/structlab/processors/text.py`（123 行）

```text
テキスト（.txt/.md/.csv）と Word/PowerPoint（.docx/.pptx）の本文抽出。
```

- 定数 `MAX_XML` = `50 * 1024 * 1024`
- `detect_encoding(path: Path)` — 
- class `TextProcessor` — 
  - `plan(self, path: Path, ctx: Ctx)` — 
  - `label(self, unit: UnitSpec)` — 
  - `extract(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 
- 定数 `W` = `'{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'`
- 定数 `A` = `'{http://schemas.openxmlformats.org/drawingml/2006/main}'`
- 定数 `M` = `'{http://schemas.openxmlformats.org/officeDocument/2006/math}'`
- `_xml(zf: zipfile.ZipFile, name: str)` — 
- `_maths(root)` — 
- class `OfficeProcessor` — 
  - `plan(self, path: Path, ctx: Ctx)` — 
  - `label(self, unit: UnitSpec)` — 
  - `extract(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 

## `src/structlab/processors/video.py`（193 行）

```text
動画・音声: 時間区間ごとに音声の文字起こしと映像のシーン解析を行い、時刻で対応付ける。
```

- `align(speech: list[dict], keyframes: list[dict], seg_end: float)` — 発話と画面（キーフレーム）を時刻で対応付ける。
- `pick_keyframes(times: list[float], scores: list[float], threshold: float, max_n: int)` — 
- `focus_spec(s: float, e: float, reason: str, duration: float)` — 重要場面の追加解析単位（最長30秒、動画の終端を超えない）。
- `_merge_windows(wins: list[tuple[float, float, str]])` — 
- class `VideoProcessor` — 
  - `plan(self, path: Path, ctx: Ctx)` — 
  - `label(self, unit: UnitSpec)` — 
  - `extract(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 
  - `_focus(self, path: Path, unit: UnitSpec, ctx: Ctx)` — 

## `src/structlab/reading/__init__.py`（0 行）


## `src/structlab/reading/ai_read.py`（596 行）

```text
AI による画像の読み取り（有料・明示的な許可があるときだけ）: PDF のページ・動画の場面を、見えるとおりに構造化して書き起こす。

解釈・要約ではなく「書き起こし」が目的。数値・記号は画像のとおりに写し、グラフから読んだ値は概算（approx）と明記させる。
ローカルで得た文字（PDF のテキスト層・OCR）は手がかりとして渡すが、結果はあとで verify.py が独立に照合する。
```

- 定数 `SYSTEM` = `'あなたは建築構造実験の授業資料（PDFのページ、講義動画の画面）を正確に書き起こす担当者です。\n解釈・要約・補足はせず、画像に見えている内容だけを構造化して書き起こしてください。\n\n厳守:\n- 文字・数値・単位・記号は画像のとおりに写す（全角/半角・上付き・下付きもできるだけ保つ。勝手に丸めない・換算しない）…`
- 定数 `_STR` = `{'type': 'string'}`
- 定数 `SCHEMA` = `{'type': 'object', 'additionalProperties': False, 'required': ['title', 'text_blocks', 'tables', 'formulas', 'figures', 'operations', 'unreadable', 'notes'], 'p…`
- 定数 `EST_IMAGE_TOKENS` = `2500`
- 定数 `EST_FIXED_INPUT` = `2600`
- 定数 `EST_OUTPUT` = `{'pdf_page': 4000, 'video_scene': 3000, 'video_ops': 3500, 'experiment_window': 3000, 'speech_fix': 3000}`
- 定数 `EST_SPEECH_INPUT` = `7000`
- 定数 `EST_ESCALATION_RATE` = `0.15`
- `pdf_page_request(file_label: str, page: dict)` — 
- `video_scene_request(file_label: str, scene: dict, fmt_time)` — 
- `estimate_calls(records: dict[str, dict])` — ローカルの読み取り結果から、AI の呼び出し回数と概算トークンを数える（APIは呼ばない）。
- 定数 `SPEECH_SYSTEM` = `'あなたは建築構造実験の講義音声の文字起こしを校正する担当者です。\n音声認識が専門用語を同音・類音の別の語に誤っている箇所だけを直してください（例: 梁→「針」、加力→「火力」）。\n\n厳守:\n- 直してよいのは、渡した「資料に出てくる用語」にある語への訂正だけ。用語リストに無い語へは直さない\n- 言い回し・文…`
- 定数 `SPEECH_SCHEMA` = `{'type': 'object', 'additionalProperties': False, 'required': ['corrections', 'uncertain'], 'properties': {'corrections': {'type': 'array', 'items': {'type': 'o…`
- 定数 `SPEECH_CHUNK` = `120`
- `speech_chunks(speech: list[dict])` — 
- `speech_fix_request(file_label: str, start_index: int, segs: list[dict], vocab: str, fmt_time)` — 
- 定数 `EXPERIMENT_SYSTEM` = `'あなたは鉄筋コンクリート梁の加力実験の経過を記録した動画を観察し、見えている事実だけを記録する担当者です。\n推測や一般論で補わず、画像から確認できることだけを書いてください。\n\n厳守:\n- 試験体のどの面が映っているか: 基準画像（撮影開始時に映っていた面＝表面）と比べて、表面(front)・裏面(back)…`
- 定数 `EXPERIMENT_SCHEMA` = `{'type': 'object', 'additionalProperties': False, 'required': ['face', 'face_evidence', 'view', 'specimen_state', 'cracks', 'loading', 'readings', 'actions', 'v…`
- `experiment_window_request(file_label: str, scene: dict, fmt_time, reference: str | None, who: str)` — 
- 定数 `EXPERIMENT_GROUP` = `1`
- 定数 `EXPERIMENT_GROUP_SYSTEM` = `EXPERIMENT_SYSTEM + '\n\n複数の区間をまとめて渡します（時刻順。各区間の画像には区間番号が付いています）:\n- windows に、渡した区間すべてについて、区間の順に1件ずつ記録する（区間番号 window を必ず書く）\n- 最初の区間では、見えているひび割れをすべて書く（change は…`
- 定数 `EXPERIMENT_GROUP_SCHEMA` = `{'type': 'object', 'additionalProperties': False, 'required': ['windows'], 'properties': {'windows': {'type': 'array', 'items': {**EXPERIMENT_SCHEMA, 'required'…`
- `window_frames(scene: dict)` — 
- `experiment_group_request(file_label: str, scenes: list[dict], fmt_time, reference: str | None, previous: tuple[float, str] | None, who: str)` — 
- `experiment_groups(scenes: list[dict], size: int=EXPERIMENT_GROUP)` — 実験経過の区間を、録画の区切り（segment）をまたがずに size 個ずつまとめる。
- 定数 `EST_GROUP_OUTPUT_BASE` = `1500`
- 定数 `EST_GROUP_OUTPUT_PER_WINDOW` = `900`
- 定数 `EST_DETAIL_OUTPUT_BASE` = `5000`
- 定数 `EST_DETAIL_OUTPUT_PER_FRAME` = `350`
- 定数 `EST_ZOOMS_PER_CHUNK` = `3.0`
- 定数 `EST_ZOOM_TOKENS` = `2500`
- 定数 `EST_ZOOM_OUTPUT_BASE` = `1500`
- 定数 `EST_ZOOM_OUTPUT_PER_IMAGE` = `300`
- `image_tokens(path: str | Path, max_edge: int=2576)` — 画像1枚の入力トークン（公式の式 ⌈幅/28⌉×⌈高さ/28⌉。長辺 2576・4784 トークンを超えると縮小されて数えられる）。
- `_kind_family(kind: str)` — 見積もりの依頼の種類から、同じ指示文（キャッシュの先頭部分）を持つまとまりの名前を返す。
- `plan_requests(rec: dict)` — 資料1件で送る依頼の一覧 [(種類, 画像トークン, 本文の推定トークン, 項目, キャッシュする画像のトークン)]（API は呼ばない）。
- `estimate_plan(records: dict[str, dict], *, system_tokens: int=1800, price_in: float=4.0, price_out: float=20.0, batch: bool=True, cache_hit: float=0.5, prefix_factor=None)` — 最適化した構成での見積もり（資料ごと）。入力は画像の実寸から計算、出力は推測（EST_*）。
- 定数 `DETAIL_EVENTS_PER_CALL` = `12`
- 定数 `ZOOMS_PER_CALL` = `10`
- 定数 `DETAIL_SYSTEM` = `SYSTEM + '\n\nこの動画は Excel の作り方・レポートの作り方の説明で、後で同じ Excel・レポートを作るために使います。細部まで正確に読んでください:\n- 画像は元の映像から取り出した最高画質のもの。「全体」は 2 倍、「部分拡大」「操作した部分の拡大」は 3 倍に拡大してある。\n  小さな文字…`
- 定数 `_ZOOM_REQ` = `{'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['frame', 'x', 'y', 'w', 'h', 'target', 'what'], 'properties': {'frame'…`
- 定数 `DETAIL_SCHEMA` = `{**SCHEMA, 'required': SCHEMA['required'] + ['speech_points', 'settings_shown', 'zoom_requests'], 'properties': {**SCHEMA['properties'], 'speech_points': {'type…`
- 定数 `ZOOM_SYSTEM` = `SYSTEM + '\n\n1回目の読み取りで「小さくて読めない・確かでない」とされた範囲を、元の映像の画素から 4 倍に拡大した画像を渡します。\n各画像の中の文字・数値・式・設定を、見えるとおりに正確に書き起こしてください。1回目の読み取り（参考）と違えば correction に書く。\n拡大しても読めない部分は…`
- 定数 `ZOOM_SCHEMA` = `{'type': 'object', 'additionalProperties': False, 'required': ['readings'], 'properties': {'readings': {'type': 'array', 'items': {'type': 'object', 'additional…`
- `detail_chunks(scene: dict, size: int=DETAIL_EVENTS_PER_CALL)` — 
- `detail_images(scene: dict, k: int, chunks: list[list[dict]], fmt_time, event_scale: float | None=None)` — k 番目の部分で送る画像（元の解像度の可逆画像から作る）。
- `video_detail_requests(file_label: str, scene: dict, fmt_time, event_scale: float | None=None)` — 場面の代表画面・部分拡大・変化したすべての画面（変わった部分の拡大つき）・音声を、多ければ複数回に分けて読む。
- `_frame_at(scene: dict, label: str)` — AI が書いた画面番号（F0 = 代表、F1… = 変化した画面の順）に当たる元の画面。
- `zoom_requests(file_label: str, scene: dict, pass1: list[dict | None], fmt_time)` — 1回目で AI が求めた範囲を元の画素から 4 倍に拡大して読む（第2段）。求めが無ければ送らない。
- `merge_detail(parts: list[dict | None], zooms: list[dict | None] | None=None)` — 分けて読んだ結果を1つの場面の結果にまとめる（どれかが欠けていれば notes と unreadable に書く）。

## `src/structlab/reading/corpus.py`（322 行）

```text
資料をまたぐ整理（読み取りの最後に行う）。結果は reading/ の下に保存する（Dropbox には書き込まない）。

1. materials.md / materials.json: 全ファイルの一覧。内容が完全に同一のファイルは代表の結果を使い、別資料として記録する。
   似ている資料（同じ種類で内容の一部が共通）は、共通部分と違う部分（時刻の範囲）を記録する
2. experiment_teacher.md / .json: 先生の実験経過動画を、役割の順（1→2）に有効区間だけつないだ時間軸。
   録画の切れ目は「記録されていない時間」として明示し、補完しない。末尾の除外（冒頭画面に戻った部分）の時刻と根拠を記録
3. experiment_student.md / .json: 生徒の実験動画の区間ごとの観察（表面・裏面、ひび割れ、操作）と面の切り替わり
4. experiment_correspondence.md / .json: 先生と生徒の動画の対応（撮影時刻ではなく、試験体の変化・操作から判断。AI許可時のみ）

  python -m structlab.reading.corpus <読み取り結果のフォルダ>... --out <出力先> [-c 設定] [--allow-external-ai]
```

- 定数 `CORR_SYSTEM` = `'あなたは鉄筋コンクリート梁の加力実験について、同じ実験を別々に撮影した2つの記録（先生の動画・生徒の動画）を\n照らし合わせる担当者です。撮影の開始時刻や動画の経過時間が同じだからといって対応させてはいけません。\n試験体の状態（ひび割れの出現・進展・破壊）、加力の段階、計測器の表示、人の操作など、記録された事実が一…`
- 定数 `CORR_SCHEMA` = `{'type': 'object', 'additionalProperties': False, 'required': ['pairs', 'unmatched_student', 'notes'], 'properties': {'pairs': {'type': 'array', 'items': {'type…`
- `_speech_text(rec: dict)` — 
- `_segment_coverage(a: dict, b: dict)` — a の発話のうち b にも（ほぼ同じ文で）ある割合と、b に無い a の時刻範囲。
- `compare_materials(items: list, records: dict[str, dict])` — 
- `_window_row(sc: dict)` — 
- `teacher_timeline(records: dict[str, dict])` — 
- `student_observations(records: dict[str, dict])` — 
- `correspondence(teacher: dict, student: dict, provider, log=print, batch: bool=False)` — 
- `_md_windows(rows: list[dict], timeline: bool)` — 
- `analyze_corpus(cfg, out: Path, items: list, records: dict[str, dict], provider=None, log=print)` — 
- `main(argv: list[str] | None=None)` — 

## `src/structlab/reading/detail.py`（169 行）

```text
Excel・レポートの作り方の動画で AI に送る画像を、保存した元の解像度の可逆画像（WebP lossless）から作る。

- 全体: 元の 2 倍（最大 2576）
- 部分拡大（代表画面）: 2×2 に少し重ねて分け、文字・線がある部分だけ（白紙の部分は拡大しない）を元の 3 倍
- 操作した部分の拡大: 直前の画面との差がある範囲（セル・数式バー・ダイアログ・メニューなど）を元の 3 倍。
  画面全体が変わったとき・差がごく小さいとき（マウスの移動など）は作らない
- AI が求めた部分の拡大（第2段）: 1回目の読み取りで「小さくて読めない・不確か」と AI が示した範囲を元の 4 倍
どれも元の画素から作る（拡大した画像からさらに拡大しない）。作った画像は frames/derived/ に保存し、作り直さない。
```

- 定数 `MAX_EDGE` = `2576`
- 定数 `OVERVIEW_SCALE` = `2.0`
- 定数 `TILE_SCALE` = `3.0`
- 定数 `CHANGE_SCALE` = `3.0`
- 定数 `ZOOM_SCALE` = `4.0`
- 定数 `TILE_OVERLAP` = `0.1`
- 定数 `TILE_MIN_INK` = `0.01`
- 定数 `CHANGE_MARGIN` = `0.06`
- `_open(path: str | Path)` — 
- `_out(src: Path, tag: str)` — 
- `_scaled(im, scale: float)` — 
- `derive(src: str | Path, tag: str, scale: float, box: tuple[int, int, int, int] | None=None)` — src（元の解像度）の box（画素）を scale 倍にした可逆画像。
- `overview(src: str | Path, scale: float=OVERVIEW_SCALE)` — 全体の画面（既定は元の 2 倍）。scale=1 は元の解像度のまま（候補 D1、既定では使わない）。
- `_ink(gray: np.ndarray)` — 文字・線（隣の画素との明るさの差が大きい画素）。
- 定数 `TILE_NAMES` = `['左上', '右上', '左下', '右下']`
- `tile_boxes(src: str | Path)` — 代表画面の 2×2（少し重ねる）のうち、文字・線がある部分の範囲（画素）。
- `content_tiles(src: str | Path)` — 代表画面の 2×2 の部分拡大（文字・線がある部分だけ）。
- `scaled_size(w: int, h: int, scale: float)` — 
- `planned_sizes(rep: str, events: list[str], prev: str | None)` — 送る画像の大きさ（ファイルを作らずに計算。見積もり用）: 全体・部分拡大・変わった部分の拡大。
- `change_box(prev: str | Path, cur: str | Path)` — 直前の画面との差がある範囲（画素）。差が無い・ごく小さい・画面全体のときは None。
- `change_zoom(prev: str | Path | None, cur: str | Path)` — 
- `zoom_box(src: str | Path, rel: dict)` — AI が示した範囲（画面に対する割合 0〜1）を画素に。少し広げる。
- `ai_zoom(src: str | Path, rel: dict, n: int)` — 

## `src/structlab/reading/inventory.py`（107 行）

```text
資料の棚卸し: 全ファイルを「解析する資料」「内容が同一の重複」「資料ではない補助ファイル」などに分ける。

重複は Dropbox の content_hash（内容のハッシュ）が同じものだけを重複とみなす（名前が似ているだけでは重複にしない）。
同じ内容のファイルが複数あるときは、名前に " (1)" などの付かないもの・浅い階層のもの・先に置かれたものを代表にする。
```

- 定数 `COPY_SUFFIX` = `re.compile('\\s*\\((\\d+)\\)$|\\s+(\\d+)$|\\s*のコピー$|\\s*copy$', re.IGNORECASE)`
- 定数 `HELPER_EXT` = `{'.sh', '.bat', '.command', '.ps1', '.py', '.js', '.json', '.yaml', '.yml', '.ini', '.plist'}`
- 定数 `READABLE_KINDS` = `{'pdf', 'video', 'audio', 'image', 'excel', 'text', 'docx', 'pptx', 'zip'}`
- class `InventoryItem` — 
- `_stem_key(path: str)` — 
- `_rank(rf: RemoteFile)` — 
- `build_inventory(files: list[RemoteFile], ignore_patterns: list[str] | None=None)` — 
- `inventory_markdown(items: list[InventoryItem])` — 
- `as_dicts(items: list[InventoryItem])` — 

## `src/structlab/reading/merge.py`（93 行）

```text
GitHub Actions で資料ごとに並列に読み取った結果（summary.json）を1つにまとめて表示する。

  python -m structlab.reading.merge <読み取り結果のフォルダ>...
PDF はどのジョブでも読むので、同じ資料は1回だけ数える。
```

- `merge(dirs: list[Path])` — 
- `to_markdown(m: dict)` — 

## `src/structlab/reading/pdf_read.py`（121 行）

```text
PDF のローカル読み取り（無料）: 文字情報（テキスト層）を位置付きで取り出し、見た目の行・数値・画像領域を記録する。

テキスト層がある PDF では文字そのものは正確に取れるが、取り出し順が見た目と違うことがある
（例: 表の見出しが数値の後に出る）。そのため位置（bbox）を保ち、同じ高さに並ぶ文字を「見た目の行」としてまとめる。
図・グラフ・写真は画像なので文字情報が無く、AIによる画像の読み取りと照合が必要になる。
```

- 定数 `NUM` = `re.compile('[-−+]?\\d+(?:[.,]\\d+)*(?:/\\d+)?')`
- `_lines(page)` — 行ごとの文字と位置。ページに回転指定がある場合は、表示される向きの座標に直す。
- `visual_rows(lines: list[dict], page_width: float)` — 縦位置が重なる行をまとめ、左から並べる（表の行・ラベルと値の対応を見た目どおりに保つ）。
- `read_pdf(path: Path, out_dir: Path, dpi: int=144)` — 

## `src/structlab/reading/report.py`（202 行）

```text
読み取り結果の報告（Markdown / JSON）。資料ごとの書き起こしと照合、全体の精度指標・要確認・未照合を書く。
```

- 定数 `STATUS` = `{'ok': '照合済み', 'needs_review': '要確認', 'unverified': '未照合', 'text_only': 'テキスト層で取得'}`
- `_ai_md(ai: dict | None)` — 
- `_experiment_md(ai: dict)` — 
- `_pdf_md(rec: dict)` — 
- `_video_md(rec: dict)` — 
- `write_reports(cfg, out: Path, items, records: dict, est: dict, usage: dict, *, ai_enabled: bool)` — 

## `src/structlab/reading/runner.py`（595 行）

```text
資料読み取り基盤の実行（`structlab read`）。元資料は読むだけで、結果は作業フォルダ（既定 work/reading）にだけ書く。

1. 一覧 → 棚卸し（重複・補助ファイルの判別）
2. 代表ファイルごとにローカルの読み取り（無料）: PDF は位置付きテキスト層、動画は場面分割・代表画面・OCR・文字起こし
3. （許可がある場合だけ）AI による画像の書き起こし。結果はファイルに保存し、同じ内容なら再送しない
4. 照合（無料）: 書き起こしをテキスト層・OCR・対応する PDF のページと突き合わせる
5. 報告: inventory.md、files/<資料>/reading.md、summary.md（精度の指標・要確認・未照合）、estimate.md
```

- 定数 `READ_KINDS` = `{'pdf', 'video', 'audio'}`
- 定数 `READ_VERSION` = `6`
- 定数 `TERM` = `re.compile('[一-龥ァ-ヶー]{2,}')`
- `_safe(name: str)` — 
- `_key(rf: RemoteFile)` — 
- `role_for(cfg, path: str)` — 資料の役割（configs の reading.roles。例: 先生・生徒の実験経過動画）。一致しなければ {}。
- `remux(src: Path, dest: Path)` — 映像・音声をそのまま（再圧縮せずに）MKV に入れ直す。目次の不完全な録画で、途中の画面の取り出しが
- `_plain_audio_index(src: Path)` — ffmpeg で扱える最初の音声トラックの番号（無ければ None）。
- `vocabulary(pdf_records: list[dict], base: str='', limit: int=60)` — PDF の文字情報から専門用語の候補を集め、文字起こしの手がかりにする。
- class `Reader` — 
  - `inventory(self, files: list[RemoteFile])` — 
  - `local_read(self, source, rf: RemoteFile, kind: str, transcriber)` — 
  - `_ocr_pdf_images(self, rec: dict)` — 図・写真のあるページは、画像の中の文字も照合に使えるようOCRしておく（テキスト層には含まれないため）。
  - `_ai_cached(self, rec: dict, item_key: str, build, provider=None)` — AI の結果をファイルに保存し、同じ資料・同じ項目なら呼び直さない（失敗は未照合として残る）。
  - `flush_batch(self)` — 集めた依頼を Batch API で送り（50% 引き）、終わるまで待って結果を保存する。送った件数を返す。
  - `_parallel(self, jobs: list)` — 
  - `ai_read(self, rec: dict)` — 
  - `_fix_speech(self, rec: dict)` — 文字起こしの専門用語の誤認識を、資料（スライドの書き起こし・OCR・PDF）の用語を根拠に訂正する。
  - `verify(self, records: dict[str, dict])` — 
  - `_ocr_accuracy(rec: dict, page_index: dict)` — PDF と同じ内容の場面で、ローカルOCRがPDFの文字をどれだけ読めたか（OCR精度の実測）。
- `run_reading(cfg, *, only: list[str] | None=None, allow_ai: bool=False, provider=None, out_dir: Path | None=None, log=print, source=None, transcriber=None, escalation=None, counter=None)` — 
- `request_hash(req)` — 依頼の指紋（指示文・本文・出力の形・画像の中身）。同じ指紋の保存済みの結果だけを使い直す。
- `all_requests(rec: dict, vocab: str='')` — 資料1件で AI に送る依頼をすべて組み立てる（送らない）。入力トークンの実測（count_tokens）に使う。
- `count_tokens(records: dict[str, dict], counter, vocab: str='', log=print)` — 依頼ごとの入力トークンを API の count_tokens で数える（推論しないので課金なし）。
- `list_targets(cfg, source, with_hash: bool=False)` — 並列実行の単位: 資料1件ずつ（PDF はどのジョブでもローカルに読むが、AI で読むのはその PDF のジョブだけ）。
- `role_key(cfg, path: str)` — 資料の役割の設定の指紋。役割の設定を変えた資料だけを読み直す（他の資料の保存済みの読み取りはそのまま使う）。
- `_make_transcriber(cfg, pdf_records: list[dict], log, model: str | None=None)` — model: 資料の役割で指定した文字起こしのモデル（Excel・レポートの作り方は精度の高い large-v3）。
- `clean_cache(cfg)` — 

## `src/structlab/reading/semantic_bridge.py`（398 行）

```text
読み取り工程の結果（reading.json）を意味理解層（semantic）に渡す橋渡し。

読み取り工程で AI が画面・ページを読んだ結果（書き起こし）と文字起こしを、意味理解層の「場面」として使う。
画像は読み取り工程で読み済みなので、第1回（場面の理解）・第2回（指示の抽出）では送らない（同じ画像を二重に送らない）。
第3回（元資料との照合）では、指示の根拠の時刻の画面（保存済みのフレーム・ページ画像）を送って確かめる。

場面の長さは従来の意味理解と同じ（動画は video.segment_seconds、既定 120 秒。PDF は1ページ）。
先生の実験経過動画は除外した末尾（valid_end 以降）を含めない。

  python -m structlab.reading.semantic_bridge <読み取り結果のフォルダ>... --out <出力先> -c 設定 [--allow-external-ai]
```

- 定数 `MATERIAL_FORMAT` = `'json'`
- `compact(x, indent: str='')` — JSON の値を、キー・値・順序をすべて保ったまま、括弧・引用符を省いた行の形にする（情報は減らさない）。
- `_scalar(v)` — 
- `_ai_json(ai: dict | None)` — 
- `_screen_text(sc: dict)` — 
- `reading_fingerprint(rec: dict)` — 読み取り結果のうち意味理解に渡す部分の指紋。
- `load_records(db: DB, records: dict[str, dict], segment: float=120.0)` — reading.json を files / units / timeline に入れる。戻り値: file_id の一覧。
- class `BatchGate` — 複数のスレッド（資料ごとの意味理解）からの AI 呼び出しを集め、全員が待ちになったら Batch API でまとめて送る。
  - `enter(self)` — 
  - `leave(self)` — 
  - `call(self, req)` — 
  - `call_many(self, reqs: list)` — 1つのスレッドから、互いに依存しない複数の依頼をまとめて待つ（スレッドとしては 1 人分の待ち）。
  - `_take(self)` — 
  - `_state(self)` — 
  - `_save_state(self, st: dict)` — 
  - `_send(self, batch)` — 
- `run_semantic(cfg, out: Path, records: dict[str, dict], provider=None, *, batch: bool=True, log=print)` — 意味理解（規則＋AI の3段階）と資料横断の統合を実行し、out/semantic/ に報告を書く。
- `main(argv: list[str] | None=None)` — 

## `src/structlab/reading/verify.py`（162 行）

```text
読み取り結果の照合（無料）。AIの書き起こしを、独立に得た文字（PDFのテキスト層・OCR・対応するPDFのページ）と突き合わせる。

- 網羅: 元の文字（テキスト層・OCR）の行が、書き起こしに含まれているか（取りこぼしの検出）
- 正確さ: 書き起こしの数値が、元の文字に存在するか（読み違い・作り出しの検出）
  グラフから読んだ概算値（approx）は元の文字に無いのが普通なので照合対象外とし、「概算」と明示する
- 動画の画面が PDF のページと同じ内容なら、PDF のテキスト層（正確な文字）とも照合する
照合できない（元の文字が無い）ものは「未照合」とし、成功には数えない。
```

- 定数 `NUM` = `re.compile('\\d+(?:\\.\\d+)?(?:/\\d+)?')`
- 定数 `DASHES` = `str.maketrans({'−': '-', '－': '-', 'ー': '-', '―': '-', '‐': '-', '–': '-', '—': '-', '～': '~', '，': ',', '、': ',', '。': '.', '：': ':', '（': '(', '）': ')'})`
- `norm(s: str)` — 
- `numbers(s: str)` — 
- `ai_text(ai: dict, include_approx: bool=False)` — 
- `line_coverage(ref_lines: list[str], text: str, threshold: float=0.8)` — ref_lines のうち text に（ほぼ）含まれる行の割合と、含まれない行。
- `similarity(a: str, b: str)` — 3文字単位の一致度（Jaccard）。動画の画面と PDF のページの対応付けに使う。
- `check_numbers(ai: dict, sources: list[str])` — 書き起こしの数値（概算を除く）が、照合元のどれかに含まれるか。
- `verify_pdf_page(page: dict, ai: dict | None)` — 
- `link_scenes_to_pdf(scenes: list[dict], pdf_pages: list[tuple[str, dict]], threshold: float=0.25)` — 動画の場面の文字（OCR）と PDF の各ページの文字を比べ、同じ内容のページを結び付ける。
- `verify_video_scene(scene: dict, ai: dict | None, pdf_page: dict | None=None)` — 
- `apply_speech_corrections(speech: list[dict], corrections: list[dict], vocab_text: str)` — AIの訂正案を、根拠（資料の用語に訂正後の語があるか）を確かめてから適用する。元の文字起こしは text_raw に残す。

## `src/structlab/reading/video_read.py`（559 行）

```text
動画のローカル読み取り（無料）: 画面を「場面（スライド・操作画面・実験映像の区間）」に分け、場面ごとの代表画面・OCR・発話を対応付ける。

1. 画面の大きさ（縦向き・横向き）が変わる区切りを見つけ、区切りごとに資料の領域を検出する
   （スマホの画面録画では時計・アドレスバー・黒い余白・プレーヤーの操作部を除く）
2. 1秒ごとの画面の変化から場面を区切る（スライドの切り替え／操作／カメラ映像は一定間隔）。
   画面の上端だけが一時的に変わる場面（通知のバナー）は前の場面に含め、代表画面には使わない
3. 場面ごとに、場面内で最も典型的な（一時的な表示の無い）画面を高解像度で切り出してOCR
4. 同じ画面に戻った場面は「再表示」として同じ内容に結び付ける（AIの読み取りを重複させない）
5. 音声: 音声トラックの長さと無音区間を調べ、音声がある部分だけを文字起こしする（無音・音声の無い部分は書き起こさない）
6. 実験経過の動画（役割 experiment）は一定間隔の区間に分け、末尾で録画の冒頭画面に戻った部分（trim_end）は除外する
```

- 定数 `SLIDE_CUT` = `0.015`
- 定数 `OPS_CUT` = `0.12`
- 定数 `SMALL_CHANGE` = `0.004`
- 定数 `OPS_RATE` = `3.0`
- 定数 `MIN_SCENE` = `3.0`
- 定数 `TOP_BAND` = `0.16`
- 定数 `SILENCE_DB` = `-50`
- 定数 `SILENCE_MIN` = `2.0`
- 定数 `HALLUCINATION` = `re.compile('ご視聴ありがとうございました|チャンネル登録|字幕|おやすみなさい|ブーブー')`
- `dhash(img: np.ndarray, size: int=16)` — 
- `hamming(a: int, b: int)` — 
- `frame_size_at(path: Path, t: float)` — t 秒の画面の大きさ（回転の指定を反映した、実際に表示される大きさ）。
- `size_segments(path: Path, info: media.ProbeInfo, step: float=20.0)` — 画面の大きさ（向き）が一定の区間 [{start, end, w, h}]。録画の途中で回転した場合に複数になる。
- `_stream_seconds(s: dict)` — ストリームの長さ。MKV では duration が無く tags.DURATION（"00:20:27.626000000"）にある。
- `split_by_orientation(path: Path, segs: list[dict], log=print, step: float=2.0)` — 画面録画の途中で端末を回し、画面の内容が横倒しになっている部分（録画の縦横は同じまま）を分ける。
- `audio_profile(path: Path, info: media.ProbeInfo, until: float)` — 音声トラックの長さと無音区間。音声が無い部分は文字起こししない（書き起こしを作らない）ために使う。
- `voiced_spans(audio: dict, until: float, min_len: float=1.0)` — 
- `in_ranges(t: float, ranges: list[list[float]])` — 
- `transcribe_spans(path: Path, out_dir: Path, transcriber, spans: list[tuple[float, float]], chunk: float, audio: dict)` — 
- `segment_scenes(times: list[float], frames: list[np.ndarray], start: float, end: float, force_mode: str | None=None, window: float=20.0)` — 変化量で場面に分ける。戻り値: 場面 [{start, end, events}], モード（slides / operations / camera / experiment）。
- `merge_notifications(scenes: list[dict], times: list[float], frames: list[np.ndarray])` — 上端の帯だけが変わった場面（通知のバナーの表示・消去）を前の場面に含める。
- `typical_time(sc: dict, times: list[float], frames: list[np.ndarray])` — 場面内で最も典型的な画面の時刻（場面の中央値の画面に最も近いもの。通知などの一時的な表示を避ける）。
- 定数 `FRAME_MIN_EDGE` = `1000`
- 定数 `FRAME_FULL_EDGE` = `1568`
- 定数 `FRAME_MAX_EDGE` = `2576`
- `frame_size(region: media.Region, mode: str='native', min_edge: int=FRAME_MIN_EDGE)` — extract_frame に渡す幅（資料の部分の長辺が規則どおりになる幅）。
- 定数 `DENSE_INTERVAL` = `0.2`
- 定数 `DENSE_BRIEF` = `3`
- `dense_changes(path: Path, start: float, end: float, w: int, h: int, crop: str | None, interval: float=DENSE_INTERVAL)` — interval 秒ごとに画面を見て、意味のある変化（マウスの移動などの小さな変化を除く）のあった時刻を返す。
- `merge_dense(events: list[dict], dense: list[dict], max_bits: int=2)` — 0.5 秒ごとの変化と 0.2 秒ごとの走査の変化をまとめる（同じ時刻・同じ画面は1つに）。
- `distinct_events(events: list[dict], times: list[float], frames: list[np.ndarray], max_bits: int=2)` — 場面内の画面の変化をすべて残す（短時間だけ表示された操作・設定を落とさない）。
- `sinfo_of(seg: dict, info: media.ProbeInfo)` — 
- `save_frame(path: Path, t: float, out: Path, region: media.Region, info: media.ProbeInfo, mode: str='native')` — 画面を保存する。切り出す範囲の外に普段と違う表示がある画面（安全確認に通らない画面）は切り出さずに全体で保存する。
- `detect_return_to_start(times: list[float], frames: list[np.ndarray], audio: dict, duration: float)` — 実験映像を再生し終えて録画の冒頭画面に戻った時刻を、次の3つがそろったときだけ決める（推測では除外しない）:
- `ocr_image(ocr: media.OCR, path: Path)` — スライド・画面向けのOCR: グレースケール化して、疎な文字（psm 11）と段落（psm 6）の結果を合わせる。
- `read_video(path: Path, out_dir: Path, *, ocr: media.OCR, transcriber: media.Transcriber | None, interval: float=1.0, max_events_per_scene: int=4, frame_width: int=FRAME_MIN_EDGE, detail: bool=False, asr_chunk: float=600.0, role: dict | None=None, meta_path: Path | None=None, log=print)` — role: {"kind": "experiment", "window": 15, "trim_end": True} など（資料の役割。configs の reading.roles）

## `src/structlab/report.py`（328 行）

```text
解析結果の出力（Markdown / JSON）と処理状況の集計。
```

- 定数 `TZ` = `timezone.utc`
- `_iso(ts: Any)` — 
- `cite(r)` — 出典表記: ファイル名 + ページ番号 / 動画の時刻
- `current_items(db: DB)` — 
- `item_json(r: dict)` — 
- `find_contradictions(db: DB)` — 
- `collect_status(db: DB, cfg=None)` — 
- 定数 `STATUS_LABELS` = `{'discovered': 'アップロード完了待ち', 'ready': '解析待ち/再開待ち', 'processing': '処理中', 'done': '完了', 'partial': '一部失敗', 'failed': '失敗', 'skipped': 'スキップ（上限）', 'unsupported': '…`
- `status_markdown(st: dict)` — 
- `_fmt_item(r: dict)` — 
- `write_reports(db: DB, cfg)` — 

## `src/structlab/requirements.py`（395 行）

```text
要求事項の台帳と照合（本番解析の完了時・Excel 作成時・レポート作成時に同じ台帳で照合する）。

台帳 = 利用者の指示（requirements/process.yaml、コミット済み）＋ 資料から抽出した先生の要求事項（意味理解層の指示一覧）
       ＋ 読み取りで判読できなかった箇所（未確認事項）。

照合の結果は項目ごとに次のどれか（確かめられないものを成功にしない）:
  成功 … 自動の確認で満たしていることを確かめた
  不一致 … 自動の確認で満たしていないことが分かった
  要確認 … 自動では判断できない・根拠が弱い。人が元資料・元動画の該当箇所で確認する
  未検証 … 確認の仕組みが無い・まだ実行していない工程
  未実施 … その工程（AI の読み取り等）を今回は行っていない
  矛盾 … 資料や指示の間で食い違いがあり、どちらも破棄せずに残している
  対象外 … この工程では確かめない（別の工程で確かめる）

  structlab requirements verify --stage analysis --reading <読み取り結果> --corpus <資料横断> --semantic <意味理解> --out <出力先>
  structlab requirements checklist --stage excel|report --semantic <意味理解> --out <出力先>
  structlab requirements verify --stage excel|report --evidence <作成時の確認記録.json> ... （作成段階での再照合）
```

- 定数 `ROOT` = `Path(__file__).resolve().parents[2]`
- 定数 `PROCESS_PATH` = `ROOT / 'requirements' / 'process.yaml'`
- 定数 `STATUSES` = `('成功', '不一致', '要確認', '未検証', '未実施', '矛盾', '対象外')`
- 定数 `EXCEL_WORDS` = `re.compile('Excel|エクセル|セル|グラフ|数式|関数|シート|近似|散布図|軸|凡例|オートフィル|コピー|貼り付け|列|行|表計算|計算')`
- 定数 `REPORT_WORDS` = `re.compile('レポート|報告書|考察|書式|章|提出|図番号|表番号|ページ|表紙|結論|目的|文献|参考|書き方|記入|手書き|印刷|PDF')`
- `load_process(path: Path=PROCESS_PATH)` — 
- `teacher_items(semantic_dir: Path | None)` — 意味理解層の指示一覧（directives.json）を要求事項の項目にする（すべて。分類・強さは変えない）。
- `unreadable_items(reading_dirs: list[Path])` — 読み取りで判読できなかった箇所（AI の unreadable）を未確認事項として台帳に載せる。
- class `Ctx` — 
  - `cj(self, name: str)` — 
  - `videos(self, pred=lambda r: True)` — 
  - `ai_used(self)` — 
- `_ok(cond: bool, good: str, bad: str)` — 
- `_units(r: dict)` — 
- `check_auto(name: str, c: Ctx)` — 
- `verify_analysis(cfg, reading_dirs: list[Path], corpus: Path | None, semantic: Path | None)` — 
- `checklist(stage: str, semantic: Path | None, reading_dirs: list[Path] | None=None)` — 作成段階で照合する項目（利用者の指示＋先生の要求事項＋未確認事項）。evidence を埋めて verify に渡す。
- `verify_stage(stage: str, items: list[dict], evidence: dict)` — 作成段階の照合。evidence: {項目ID: {status, where, note}}。記録が無い・根拠が無い項目は成功にしない。
- `write(rows: list[dict], out: Path, stage: str)` — 

## `src/structlab/safety.py`（175 行）

```text
安全性: ZIP爆弾・危険パス対策、ディスク容量確認、ハッシュ計算。
```

- 定数 `DROPBOX_BLOCK` = `4 * 1024 * 1024`
- 定数 `CHUNK` = `1024 * 1024`
- class `SafetyError` — 
- class `DiskSpaceError` — 
- `dropbox_content_hash(path: Path)` — Dropbox の content_hash と同じアルゴリズム（4MiBブロックのSHA256を連結してSHA256）。
- class `DropboxHasher` — ストリーミングで受け取りながら content_hash を計算する。
  - `update(self, data: bytes)` — 
  - `hexdigest(self)` — 
- `ensure_free_space(directory: Path, need_bytes: int, min_free_mb: int)` — 
- `safe_member_path(name: str)` — ZIP内のパスを検証し、展開先からはみ出さない相対パスを返す。
- `decode_zip_name(info: zipfile.ZipInfo)` — 日本語Windowsで作られたZIP（CP932）の文字化けを補正する。
- class `ZipLimits` — 
- class `ExtractedMember` — 
- `safe_extract_zip(zip_path: Path, dest: Path, limits: ZipLimits, min_free_mb: int=0)` — ZIPを安全に展開する。戻り値は (展開したファイル, スキップ理由のリスト)。
- `sanitize_filename(s: str, maxlen: int=150)` — 

## `src/structlab/scanner.py`（109 行）

```text
ソースを走査し、新規・更新・削除・アップロード途中のファイルを判定して DB に記録する。
```

- class `ScanResult` — 
  - `summary(self)` — 
- `is_ignored(path: str, patterns: list[str])` — 
- `remote_file_of(row)` — 
- `scan(db: DB, source: Source, cfg, now: float | None=None)` — 

## `src/structlab/semantic/__init__.py`（5 行）

```text
意味理解層: 場面の局所理解・資料全体の理解・相互照合・指示の網羅的抽出と照合・資料横断の統合。
```


## `src/structlab/semantic/corpus.py`（134 行）

```text
複数資料（動画・PDF・Excel説明・レポート説明・事前資料）を横断した実験全体の統合理解。

資料ごとの全体理解（scope='document'）とは別に scope='corpus' として保存する。
```

- `best_docs(db: DB)` — ファイルごとに採用する解析結果: AI意味理解が完了していればそれ、なければ規則のみの結果。
- `_dir_line(d)` — 
- `corpus_key(db: DB, docs: list)` — 
- `integrate(engine)` — 
- `engine_eligible()` — 

## `src/structlab/semantic/engine.py`（998 行）

```text
意味理解エンジン。

資料（動画・PDF・Excel など）ごとに次の段階を順に実行し、各段階の結果を DB に確定させる（途中停止しても続きから）。

  local モード（常に実行・AI不使用）
    rules      : 言い回しによる指示候補の抽出（A〜H 分類・条件・訂正/補足/例外/条件変更の標識）
    verify     : 原文との照合（規則抽出なので照合は自明。判断が曖昧なものを要確認に）
    relations  : 訂正・補足・例外・条件変更・繰り返し・矛盾の関連付け

  ai モード（AIが有効で、外部送信が明示的に許可された場合のみ）
    第1回 scenes      : 場面ごとの局所理解（音声・映像・前後の文脈を統合）
          synthesis   : 資料全体の理解
          reinterpret : 全体理解を使った各場面の再解釈 → 全体理解の更新（変更がなくなるまで、上限 max_rounds）
    第2回 directives  : 全体理解を踏まえた指示の重点抽出（規則候補を全件確認させ、見落としを検出）
    第3回 verify      : 元の発話・画面・資料との照合（曖昧・不一致・見落とし候補は範囲を広げて再解析）
          relations   : 関連付け
```

- 定数 `ELIGIBLE_KINDS` = `('video', 'audio', 'pdf', 'image', 'excel', 'text', 'docx', 'pptx')`
- 定数 `FINAL_VERDICTS` = `('verified', 'revised', 'rejected', 'class_mismatch_unresolved')`
- class `BudgetExceeded` — AI使用量の上限。今回の実行ではこれ以上 AI を呼ばない（次回続きから）。
- `_j(x: Any)` — 
- `_clip(s: str, n: int)` — 
- `apply_updates(cur: dict, updates: list[dict])` — 候補C5: 現在の解釈に、変わった項目の新しい値を重ねる（値は JSON。読めなければ文字列のまま）。
- class `SemanticEngine` — 
  - `ai_active(self)` — 
  - `process_file(self, frow, local_path: Path | None=None)` — 
  - `backfill(self)` — 解析済みファイルのうち、意味理解が未完了のものを処理する（AIを後から許可した場合など）。
  - `_doc_row(self, frow, mode: str)` — 
  - `_doc(self, frow, mode: str)` — 
  - `_set_doc(self, doc_id: int, **kw)` — 
  - `_step(self, doc_id: int, unit_id: int, step: str)` — 
  - `_mark(self, doc_id: int, unit_id: int, step: str, status: str, detail: str='')` — 
  - `_pending(self, doc_id: int, unit_id: int, step: str)` — 
  - `_ai(self, req: AIRequest, unit_id: int | None)` — 
  - `_batching(self)` — 互いに依存しない依頼をまとめて送れるか（Batch の門がある場合だけ）。まとめない場合は従来どおり
  - `_ai_many(self, items: list[tuple[AIRequest, int | None]])` — 互いに依存しない依頼をまとめて送る。戻り値は各依頼の応答か AIError（順序は items と同じ）。
  - `_run_local(self, frow, scenes: list[Scene])` — 
  - `_post_checks(self, frow, mode: str, scenes: list[Scene])` — 意味理解・指示抽出の工程に組み込んだチェック（AI不使用）。
  - `_extract_rule_directives(self, doc_id: int, frow, scenes: list[Scene], candidate: bool)` — 
  - `_insert_directive(self, doc_id: int, frow, sc: Scene | None, d: dict)` — 
  - `_speaker(self, claimed: str | None, line_origin: str | None)` — 発言者。話者識別はしていないので、利用者が「動画の音声は先生」と確認した場合（semantic.video_speaker:
  - `_verify_local(self, doc_id: int, frow, scenes: list[Scene], mode: str)` — 元の発話・画面・資料との機械的な照合。戻り値は要再解析の (指示ID, 問題点)。
  - `_class_guard(cls: str, base: str | None, quote: str)` — AI の分類が原文の言い回しの強さ・向きと食い違っていないか。
  - `_set_verification(self, did: int, status: str, notes: list[str])` — 
  - `_run_ai(self, frow, scenes: list[Scene], local_path: Path | None)` — 
  - `_latest(self, did: int, unit_id: int)` — 
  - `_brief(a: dict | None)` — 
  - `_stage1_scenes(self, did: int, frow, scenes: list[Scene])` — 
  - `_save_scene(self, did: int, sc: Scene, ps: int, analysis: dict, changes: list | None)` — 
  - `_legacy_items(self, frow, sc: Scene, a: dict, analyzer: str)` — 従来の10分類（knowledge.md）へも反映する（単位ごとのAI解析を意味理解で置き換えた場合）。
  - `_scene_digest(self, did: int, scenes: list[Scene])` — 
  - `_synthesize(self, did: int, frow, scenes: list[Scene], version: int, changes: list)` — 
  - `_understanding(self, did: int)` — 
  - `_understanding_version(self, did: int)` — 
  - `_global_brief(g: dict | None)` — 
  - `_reinterpret_loop(self, did: int, frow, scenes: list[Scene], start_round: int)` — 
  - `_stage2_directives(self, did: int, frow, scenes: list[Scene])` — 
  - `_directive_result(self, did: int, frow, scenes, sc: Scene, cands, r)` — 
  - `_directive_from_ai(self, x: dict, sc: Scene, scenes: list[Scene], analyzer: str)` — 
  - `_fill_context(d: dict, scenes: list[Scene], sc: Scene)` — AIが前後の文脈・画面を返さなかった場合、時刻同期した元の発話・画面から補う（補ったことを記録）。
  - `_save_ai_directives(self, did: int, frow, scenes, sc: Scene, cands, data: dict, analyzer: str)` — 
  - `_stage3_verify(self, did: int, frow, scenes: list[Scene], local_path: Path | None)` — 
  - `_verify_one(self, did: int, frow, scenes: list[Scene], dir_id: int, issues: list[str], g: dict | None, local_path: Path | None)` — 
  - `_verify_request(self, did: int, frow, scenes: list[Scene], dir_id: int, issues: list[str], g: dict | None, local_path: Path | None)` — 
  - `_verify_apply(self, did: int, frow, scenes: list[Scene], d, issues: list[str], r)` — 
  - `_reanalyze_media(self, frow, d, scenes: list[Scene], local_path: Path | None, expand: float)` — 曖昧な指示の前後を、元の映像・音声から取り直す（元ファイルが手元にある場合）。
  - `_verify_group(self, did: int, frow, scenes: list[Scene], items: list, g: dict | None, local_path: Path | None)` — 候補: 同じ場面の複数の指示を1回で照合する（範囲・画面は各指示の分を合わせ、重複だけ除く）。
  - `_apply_verdict(self, did: int, frow, scenes, d, issues: list[str], data: dict, analyzer: str)` — 

## `src/structlab/semantic/estimate.py`（109 行）

```text
AI解析を始める前の、呼び出し回数・トークン数・費用の概算（実際のAPIは呼ばない）。

あくまで目安。日本語は1文字≒0.7〜1.2トークン、画像1枚≒1,600トークン（長辺1568px）として計算する。
出力トークンは段階ごとの典型値の範囲で見積もる。料金は ai.price_*_per_mtok（設定値）を使う。
```

- 定数 `IMG_TOKENS` = `1600`
- 定数 `OUT` = `{'scene': (800, 2500), 'synthesis': (2000, 8000), 'reinterpret': (300, 2500), 'directives': (500, 4000), 'verify': (300, 1200), 'corpus': (3000, 12000), 'legacy…`
- `estimate(db: DB, cfg)` — 
- `estimate_text(e: dict)` — 

## `src/structlab/semantic/material.py`（133 行）

```text
既存の解析単位（動画区間・PDFページ・シート範囲など）と時刻同期タイムラインから「場面」を組み立てる。
```

- 定数 `SCENE_TYPES` = `('video_segment', 'pdf_page', 'image', 'excel_workbook', 'excel_sheet', 'text_chunk', 'office_part')`
- class `Line` — 
- class `Scene` — 
  - `duration(self)` — 
  - `where(self)` — 
- `is_time_based(kind: str)` — 
- `build_scenes(db: DB, frow)` — 
- `lines_text(lines: list[Line])` — 
- `window_lines(scenes: list[Scene], t0: float | None, t1: float | None, page: int | None, expand: float)` — 照合用に、時刻（またはページ）の前後を広げた範囲の元の発話・画面・資料を集める。

## `src/structlab/semantic/prompts.py`（236 行）

```text
意味理解層の指示文と出力スキーマ。

出力スキーマは Anthropic の構造化出力（json_schema）向けに、全プロパティ required・
additionalProperties false で記述している。null を許す値は anyOf で表す。
```

- 定数 `SEMANTIC_VERSION` = `'sem-1'`
- 定数 `_N` = `{'anyOf': [{'type': 'number'}, {'type': 'null'}]}`
- 定数 `_S` = `{'type': 'string'}`
- 定数 `_SN` = `{'anyOf': [{'type': 'string'}, {'type': 'null'}]}`
- 定数 `_I` = `{'type': 'integer'}`
- 定数 `_AS` = `{'type': 'array', 'items': _S}`
- 定数 `_AI` = `{'type': 'array', 'items': _I}`
- `_obj(props: dict)` — 
- 定数 `CLASS_ENUM` = `list(DIRECTIVE_CLASSES)`
- 定数 `STRENGTH_ENUM` = `{'anyOf': [{'type': 'string', 'enum': [c for c in CLASS_ENUM if c not in ('conditional', 'other')]}, {'type': 'null'}]}`
- 定数 `CERTAINTY` = `{'type': 'string', 'enum': ['high', 'medium', 'low']}`
- 定数 `DISCOURSE_TYPES` = `['correction', 'supplement', 'exception', 'condition_change', 'emphasis']`
- 定数 `COMMON_SYSTEM` = `'あなたは建築構造実験（鋼材の引張試験など）の授業動画・資料を、学生の立場で深く理解する解析者です。\n目的は、文字起こし・OCR・画像説明の羅列や短い要約ではなく、先生が「何を」「なぜ」説明し、\n資料全体で何を伝え、学生に何をしてほしいのか・してはいけないのかを、根拠付きで理解することです。\n\n厳守事項:\n-…`
- 定数 `SCENE_ANALYSIS` = `_obj({'speech': _S, 'screen': _S, 'operations': _S, 'meaning': _S, 'purpose': _S, 'relation_prev': _S, 'relation_next': _S, 'audience': _S, 'task': _S, 'deliver…`
- 定数 `SCENE_SCHEMA` = `_obj({'analysis': SCENE_ANALYSIS})`
- 定数 `SCENE_INSTRUCTION` = `'## 作業（第1回: 場面の局所理解）\n次の「対象場面」について、音声（発話）と映像（画面・操作）を時刻で対応付けて統合し、場面の意味を理解してください。\n1. 先生が何を話しているか 2. 画面に何が表示されているか 3. どのような操作・実験か 4. その意味 5. なぜそれを行うのか\n6. 直前・直後の説…`
- 定数 `_SCENE_REFS` = `_AI`
- 定数 `GLOBAL_SCHEMA` = `_obj({'theme': _S, 'purpose': _S, 'experiment_purpose': _S, 'message': _S, 'flow': {'type': 'array', 'items': _obj({'order': _I, 'description': _S, 'scene_seqs'…`
- 定数 `GLOBAL_INSTRUCTION` = `'## 作業（第1回: 資料全体の意味理解）\n各場面の理解結果を統合し、資料（動画）全体で先生が何を説明し、何を伝えようとしているのかを理解してください。\nテーマ・目的・実験と提出物の目的・伝えたい内容・説明の流れ・論理構造・各場面の役割・作業の正しい順序と依存関係・結論・\n重要事項・訂正/補足/例外/条件変更・…`
- 定数 `REINTERPRET_SCHEMA` = `_obj({'revised': {'type': 'boolean'}, 'changes': {'type': 'array', 'items': _obj({'field': _S, 'before': _S, 'after': _S, 'reason': _S})}, 'analysis': SCENE_ANA…`
- 定数 `REINTERPRET_DELTA_SCHEMA` = `_obj({'revised': {'type': 'boolean'}, 'changes': {'type': 'array', 'items': _obj({'field': _S, 'before': _S, 'after': _S, 'reason': _S})}, 'updated_fields': {'t…`
- 定数 `REINTERPRET_DELTA_NOTE` = `'## 返し方（変わった項目だけ）\n変更がなければ revised=false とし、updated_fields は空にしてください（現在の解釈を書き直さない）。\n変更した項目は、updated_fields にその項目の名前と、変更後の完全な値（その項目の形のまま JSON で表した文字列）を入れてください。\…`
- 定数 `REINTERPRET_INSTRUCTION` = `'## 作業（局所理解と全体理解の相互照合）\n資料全体の理解結果を踏まえて、この場面の解釈を見直してください。\n後の場面での訂正・補足・例外・条件変更によって、この場面の説明の意味や有効性が変わる場合は、その内容を反映し、\n変更点を changes に（変更前・変更後・理由）記録してください。変更がなければ re…`
- 定数 `DIRECTIVE_ITEM` = `_obj({'content': _S, 'directive_class': {'type': 'string', 'enum': CLASS_ENUM}, 'base_strength': STRENGTH_ENUM, 'condition': _SN, 'target_task': _S, 'deliverabl…`
- 定数 `DIRECTIVE_SCHEMA` = `_obj({'directives': {'type': 'array', 'items': DIRECTIVE_ITEM}, 'dismissed_candidates': {'type': 'array', 'items': _obj({'candidate': _I, 'reason': _S})}})`
- 定数 `CLASS_GUIDE` = `'\n'.join((f'- {k}: {v}' for k, v in DIRECTIVE_CLASSES.items()))`
- 定数 `DIRECTIVE_INSTRUCTION` = `f'## 作業（第2回: 指示・禁止事項・注意事項の重点抽出）\n資料全体の理解と場面の理解を踏まえ、対象場面に含まれる先生の指示を、漏れなく、1つずつ独立して抽出してください。\n分類:\n{CLASS_GUIDE}\n規則:\n- 言葉遣いだけでなく前後の説明と全体の文脈から意味を判断する。ただし強さ（禁止/非推奨…`
- 定数 `VERIFY_SCHEMA` = `_obj({'verdict': {'type': 'string', 'enum': ['confirmed', 'revised', 'rejected', 'unresolved']}, 'reason': _S, 'evidence_quote': _S, 'revised': {'anyOf': [_obj(…`
- 定数 `VERIFY_INSTRUCTION` = `'## 作業（第3回: 元の映像・音声・資料との照合）\n「確認対象」の指示を、広げた範囲の元の発話・画面・資料と照合し、見落とし・誤解・分類ミス・条件の見落としがないか確認してください。\n- 元の発話・資料で確認できれば confirmed、内容・分類・条件・時刻の修正が必要なら revised（修正後を revi…`
- 定数 `VERIFY_GROUP_SCHEMA` = `_obj({'results': {'type': 'array', 'items': _obj({'id': _I, **VERIFY_SCHEMA['properties']})}})`
- 定数 `VERIFY_GROUP_NOTE` = `'複数の「確認対象」をまとめて渡します。それぞれを独立に、上の作業どおり照合し、results に確認対象ごとに1件ずつ\n（id は確認対象の番号）書いてください。1件ずつ別々に照合した場合と同じ深さで確認し、他の確認対象の結論に引きずられないこと。\n'`
- 定数 `_IDS` = `_AI`
- 定数 `CORPUS_SCHEMA` = `_obj({'experiment_purpose': _S, 'experiment_content': _S, 'procedure': {'type': 'array', 'items': _obj({'step': _I, 'action': _S, 'sources': _AS, 'depends_on': …`
- 定数 `CORPUS_INSTRUCTION` = `'## 作業（複数資料を横断した実験全体の統合理解）\n複数の動画・PDF・Excel説明・レポート説明・事前資料の理解結果と指示一覧を横断し、実験全体として\n目的・内容・手順・データ処理・Excel作成方法・レポート作成方法・作業の順序と依存関係・最終的な提出物と要件を理解してください。\n- 同じ指示が複数資料に…`
- `scene_prompt(source: str, scene_label: str, material: str, prev_summary: str, next_material: str)` — 

## `src/structlab/semantic/relations.py`（183 行）

```text
指示同士の関連付け（訂正・補足・例外・条件変更・繰り返し・矛盾）。資料内と資料横断の両方。

方針:
- 「訂正」で前の指示を無効（superseded）にするのは、訂正の言い回し（「訂正します」「さっき〜と言いましたが」等）が
  原文に明示されている場合だけ。AIの判断だけの訂正は候補（unresolved）として残し、前の指示は無効にしない。
- 向きが逆（してよい／いけない）で対象が似ている指示は、訂正として説明できない限り「未解決の矛盾」として記録し、
  どちらが正しいかは決めない。
```

- 定数 `REL_NAME` = `{'correction': 'correction_of', 'supplement': 'supplement_to', 'exception': 'exception_to', 'condition_change': 'condition_change_of'}`
- 定数 `REL_LABEL` = `{'correction': '訂正', 'supplement': '補足', 'exception': '例外', 'condition_change': '条件変更'}`
- 定数 `SIM_REL` = `0.3`
- 定数 `SIM_CONFLICT` = `0.5`
- 定数 `SIM_SAME` = `0.6`
- 定数 `SIM_SAME_CORPUS` = `0.6`
- 定数 `ACTIVE` = `('effective', 'unresolved_conflict', 'superseded')`
- `_pol(d)` — 
- `_order(d)` — 
- `_link(db: DB, a: int, b: int, rel: str, scope: str, basis: str, note: str, status: str='recorded')` — 
- `_candidates(ds: list)` — 内容語を共有する組だけを比較対象にする（全組み合わせ比較を避ける）。
- `value_conflict(a: str, b: str)` — 同じ項目（提出形式・期限・枚数・有効数字）で値が異なれば説明を返す。
- `link_document(db: DB, doc_id: int)` — 
- `link_corpus(db: DB, doc_ids: list[int])` — 資料横断: 同じ指示の関連付けと、資料間で向きが逆の指示（矛盾候補）の記録。

## `src/structlab/semantic/report.py`（508 行）

```text
意味理解層の出力（results/semantic/）と解析網羅率。

出力一覧:
  scenes/*.md|json        1. 資料別の各場面の詳細な意味解析
  understanding/*.md|json 2-3. 資料全体の意味理解・説明内容・作業手順
  directives.md           4. 指示・禁止事項・注意事項の一覧（網羅性重視・分類別）
  directives.json|csv     5. 各指示の分類・条件・根拠（全項目）
  requirements.md         6. 複数資料を統合した要件一覧
  experiment.md|json      7. 実験全体の目的・手順・提出物の統合理解
  unconfirmed.md          8. 未確認事項
  conflicts.md            9. 矛盾する指示
  changes.md             10. 訂正・補足・例外・条件変更
  failures.md            11. 解析に失敗した箇所
  coverage.md|json       12. 解析網羅率と検証状況
```

- 定数 `VER_LABEL` = `{'rule_extracted': '規則で原文から抽出（文脈未考慮）', 'candidate': '規則候補', 'corroborated': 'AI抽出と一致', 'omission_candidate': '見落とし候補（AI未抽出・未照合）', 'verified_local': '根拠を原文で機械照合済…`
- 定数 `EFF_LABEL` = `{'effective': '有効', 'superseded': '訂正により無効', 'unresolved_conflict': '未解決の矛盾あり', 'merged': 'AI抽出に統合', 'dismissed': '指示ではないと判断', 'rejected': '照合で否定'}`
- 定数 `REL_LABEL` = `{'correction_of': '訂正', 'supplement_to': '補足', 'exception_to': '例外', 'condition_change_of': '条件変更', 'conflicts_with': '矛盾', 'same_as': '同一・繰り返し', 'corroborated_…`
- 定数 `ORIGIN_LABEL` = `{'explicit': '明示', 'ai_inference': 'AIの推論'}`
- 定数 `SPEAKER_LABEL` = `{'teacher_confirmed': '先生（利用者が確認済み）', 'teacher_inferred': '先生とAIが推定（未確認）', 'unknown': '発言者未確認（先生の指示と断定していない）', 'document': '資料の記述', 'screen': '画面の表示', 'student'…`
- 定数 `OPEN_VER` = `('needs_review', 'omission_candidate', 'class_mismatch_unresolved', 'found_in_verify', 'unverified')`
- `_name(path: str, fid: int)` — 
- `where(d)` — 
- `directive_record(db: DB, d)` — 
- `_fmt_dir(r: dict)` — 
- `_intervals(scenes, ok_units: set[int])` — 
- `coverage_for(db: DB, frow, doc)` — 
- `collect_coverage(db: DB)` — 
- `_pct(x)` — 
- `write_semantic_reports(db: DB, cfg)` — 
- `search(db: DB, keyword: str, limit: int=50)` — 指示・場面の理解・全体理解をキーワードで検索し、出典（資料・時刻）付きで返す。

## `src/structlab/semantic/rules.py`（215 行）

```text
日本語の言い回しに基づく指示の分類と、訂正・補足・例外・条件変更の検出（AIを使わない規則）。

ここでの判定は「明示された言い回し」の検出であり、意味理解ではない。役割は次の3つ:
  1. AIを使わない場合の指示一覧（網羅の下限）
  2. AIが抽出した指示の見落とし検査（規則で見つかったのにAIが拾っていない候補）
  3. AIが指示の強さ・向き（してよい/いけない）を勝手に変えていないかの検査
```

- 定数 `NEGATIVE` = `{'prohibited', 'discouraged'}`
- 定数 `POSITIVE` = `{'mandatory', 'request', 'recommended', 'optional'}`
- 定数 `STRENGTH` = `{'prohibited': 3, 'discouraged': 1, 'mandatory': 3, 'request': 2, 'recommended': 1, 'optional': 0}`
- 定数 `_COMPILED` = `[(c, re.compile(p)) for c, p in _PATTERNS]`
- 定数 `_CONDITION` = `re.compile('(?P<cond>[^。、]{1,40}?(場合は?|ときは?|時は?|際は?|ならば?|であれば|でなければ|以外は|に限り|限りは?))')`
- 定数 `_CAUTION` = `re.compile('注意|気をつけ|気を付け|ポイント|重要|大事|大切|間違えやすい|ミスしやすい|よくある間違い')`
- 定数 `_SENT` = `re.compile('(?<=[。．！？!?])\\s*|\\n+')`
- `norm(s: str)` — 
- `classify(text: str)` — (分類, 根拠となった表現)。指示表現が無ければ (None, None)。
- `polarity(cls: str | None)` — 
- `discourse_markers(text: str)` — 
- `condition_of(text: str)` — 
- `split_clauses(sentence: str)` — 1つの発言に複数の指示が含まれる場合に分ける。
- class `RuleDirective` — 
- `directives_in(text: str)` — テキスト（1発言・1文〜数文）から規則で指示候補を取り出す。
- `bigrams(s: str)` — 
- 定数 `_MODAL_TAIL` = `re.compile('(してはいけない|しないでください|してください|すること|しなければならない|した方がいい|してもよい|しないこと|ください|ません|ます|です|ない)$')`
- `target_of(content: str)` — 指示の対象部分（述語の言い回しを除いた部分）。向きの異なる指示の比較に使う。
- 定数 `_WORD` = `re.compile('[一-龥々〆ヵヶ]+|[ァ-ヴー]{2,}|[A-Za-z][A-Za-z0-9.]*|[0-9]+(?:\\.[0-9]+)?')`
- 定数 `_STOP` = `{'絶対', '必', '今日', '次', '最後', '全部', '全', '場合', '時', '方', '事', '物', '様'}`
- `content_words(s: str)` — 内容語（漢字・カタカナ・英数字の連続）。言い回し（ひらがなの述語・助詞）を除いて対象を比べるために使う。
- `target_similarity(a: str, b: str)` — 2つの指示が同じ対象（作業・物）について述べているかの目安。
- `similarity(a: str, b: str)` — 

## `src/structlab/sources/__init__.py`（13 行）

- `make_source(cfg)` — 

## `src/structlab/sources/base.py`（33 行）

```text
ファイル取得元の共通インターフェース。元ファイルは読み取り専用で扱う。
```

- class `SourceError` — 
- class `SourceChanged` — 取得中・取得前にファイルが更新された（アップロード途中の可能性）。
- class `Source` — 
  - `iter_files(self)` — ルート以下のファイルを再帰的に列挙する（ページングはこの中で処理）。
  - `fetch(self, rf: RemoteFile, dest: Path, min_free_mb: int=0)` — ファイルをローカルに用意してパスを返す。大容量でもメモリに一括で載せない。
  - `is_local_passthrough(self)` — fetch がコピーせず元パスを返す（削除してはいけない）なら True。

## `src/structlab/sources/dropbox_source.py`（246 行）

```text
Dropbox API ソース（読み取り専用）と、結果保存用の書き込み先（出力フォルダ限定）。

認証情報は環境変数（GitHub Actions では Secrets から渡す）か、権限600のファイルからだけ読む。値は表示しない:
  DROPBOX_APP_KEY + DROPBOX_REFRESH_TOKEN (+ DROPBOX_APP_SECRET)  … 推奨（自動更新。PKCE で取得した場合は secret 不要）
  DROPBOX_ACCESS_TOKEN                                           … 短期トークン（4時間程度で失効）
  ~/.config/structlab/dropbox_refresh_token, dropbox_app_key      … `structlab dropbox-auth` が保存するファイル
    （場所は DROPBOX_REFRESH_TOKEN_FILE / DROPBOX_APP_KEY_FILE で変更可。環境変数の方が優先）
```

- 定数 `T` = `TypeVar('T')`
- 定数 `CRED_DIR` = `Path('~/.config/structlab')`
- 定数 `READ_SCOPES` = `['files.metadata.read', 'files.content.read']`
- 定数 `SECRET_ENV_NAMES` = `('DROPBOX_APP_KEY', 'DROPBOX_APP_SECRET', 'DROPBOX_REFRESH_TOKEN', 'DROPBOX_ACCESS_TOKEN', 'ANTHROPIC_API_KEY')`
- `_from_file(env_name: str, default_name: str)` — 
- `_env(name: str, padded: list[str])` — 
- `dropbox_credentials()` — 使える認証情報と、その出どころ（env / file）。値そのものは呼び出し側で表示しないこと。
- `secret_values()` — ログ・画面に出してはいけない値（伏せ字にするため）。
- `redact(text: str)` — 
- `make_client()` — 
- `_with_retry(fn: Callable[[], T], what: str, attempts: int=5)` — 
- `_norm_root(p: str)` — 
- class `DropboxSource` — 
  - `_rel(self, path_display: str)` — 
  - `iter_files(self)` — 
  - `fetch(self, rf: RemoteFile, dest: Path, min_free_mb: int=0)` — ストリーミングでダウンロードし、Dropbox content_hash を照合する。
- class `DropboxOutput` — 解析結果・状態DBの保存先。指定した出力フォルダの外には書き込まない。
  - `_target(self, rel: str)` — 
  - `upload(self, local: Path, rel: str)` — 
  - `download_if_exists(self, rel: str, local: Path)` — 

## `src/structlab/sources/local.py`（52 行）

```text
ローカルディレクトリをソースとして扱う（テスト・PC上での実行用）。
```

- class `LocalSource` — 
  - `iter_files(self)` — 
  - `fetch(self, rf: RemoteFile, dest: Path, min_free_mb: int=0)` — 
  - `is_local_passthrough(self)` — 

## `src/structlab/style/__init__.py`（1 行）

```text
文章スタイルの適用（最終工程のみ）。
```


## `src/structlab/style/apply.py`（204 行）

```text
最終工程: 確定した文章の表現だけを本人の文章の特徴に寄せる。

前提: レポートの内容・構成・計算・考察が確定していること。この工程では内容を変えない。
- 適用前の文章を必ず style_runs に保存する
- 適用後に「内容が変わっていないか」を機械的に再検証する
    不一致（数値・数式・引用の変化）→ 採用しない（元の文章を最終版にする）
    要確認（否定・断定の強さ・結論文・新しい語・文の数の大きな変化）→ 既定では採用しない（style.accept_needs_review）
- スタイルが無効、またはPDF（プロファイル）が無ければ、元の文章をそのまま返す（処理は止めない）
```

- 定数 `STYLE_SYSTEM` = `'あなたは、すでに内容・構成・計算・考察が確定した実験レポートの文章について、表現だけを書き手本人の文章の特徴に寄せる編集者です。\n絶対に守ること:\n- 数値・単位・数式・計算結果・実験結果・根拠・引用（「」内）・教授の指示を一字一句変えない。\n- 文章の論理構造（主張と理由の対応・順序）と結論を変えない。主張の…`
- 定数 `SCHEMA` = `{'type': 'object', 'properties': {'rewritten': {'type': 'string'}, 'changes': {'type': 'array', 'items': {'type': 'object', 'properties': {'before': {'type': 's…`
- 定数 `_NUM` = `re.compile('[-+]?\\d+(?:\\.\\d+)?(?:[eE][-+]?\\d+)?\\s*(?:N/mm2|mm2|mm|cm|kN|MPa|GPa|N|kg|με|%|℃|°)?')`
- 定数 `_FORMULA` = `re.compile('[A-Za-zσεΔπ][A-Za-z0-9_σεΔπ]*\\s*=\\s*[^。、\\n]+|=[A-Z]+\\([^)]*\\)')`
- 定数 `_QUOTE` = `re.compile('「([^」]+)」')`
- 定数 `_NEG` = `re.compile('ない|ません|ず[、。]|ぬ[、。]|ではな')`
- 定数 `_HEDGE` = `re.compile('と考えられる|と考える|可能性|かもしれない|と思われる|と推測|であろう|らしい')`
- 定数 `_ASSERT` = `re.compile('である[。]|明らかに|必ず|確実に|間違いなく')`
- 定数 `_WORD` = `re.compile('[一-龥々]{2,}|[ァ-ヴー]{3,}|[A-Za-z]{3,}')`
- 定数 `_SENT` = `re.compile('(?<=[。．！？!?])')`
- `_n(s: str)` — 
- `_sentences(s: str)` — 
- `verify_preservation(original: str, styled: str)` — 適用前後で内容が変わっていないかの機械的な検査。
- `split_sections(md: str)` — Markdown の見出しごとに分ける。見出し・表・数式ブロックは文章調整の対象にしない。
- `apply_style(db: DB, cfg, text: str, *, provider=None, section: str='', kind: str='explanation')` — 
- `apply_to_markdown(db: DB, cfg, md: str, *, provider=None)` — 
- `ensure_profile(cfg, db: DB | None=None)` — 設定の PDF からプロファイルを作る（PDFが無ければ None。処理は止めない）。

## `src/structlab/style/profile.py`（158 行）

```text
本人の文章の特徴をまとめたPDFを読み込み、項目ごとに分解して「適用してよい項目」を選ぶ。

このPDFは最終工程（内容・構成・計算・考察が確定した後の文章表現の調整）にだけ使う。
資料の意味理解・指示の判断・計算・Excel作成・実験結果の評価には使わない（このモジュールはそれらから参照されない）。

PDFの全項目を機械的に適用しない:
- 入力時の特徴（誤字・脱字・助詞の脱落・句読点をほとんど使わない等）は正式なレポートの正確性を損なうため除外
- 就職活動の文書（ES・志望動機・自己PR）向けの項目は実験レポートには当てはまらないため除外
- 除外した項目と理由は profile.json にすべて残す
PDFはリポジトリに置かない（作業ディレクトリ work/style/ にだけ保存）。
```

- 定数 `_PART` = `re.compile('^第\\s*([1-9])\\s*部\\s*(.*)$')`
- 定数 `_SECTION` = `re.compile('^(?P<label>[①-⑳]|[A-Z]\\.|\\d{1,2}\\.)\\s*(?P<title>.+?)\\s*(?P<a>\\d+)\\s*[～~〜-]\\s*(?P<b>\\d+)\\s*$')`
- 定数 `_ITEM` = `re.compile('^(?P<no>\\d{1,4})\\.\\s*(?P<text>.+)$')`
- 定数 `_PAGENO` = `re.compile('^\\d{1,3}$')`
- 定数 `ACCURACY_EXCLUDE` = `re.compile('誤字|脱字|変換ミス|入力ミス|打ち間違|助詞.{0,6}(抜|欠|省)|句点.{0,8}(使わない|ほとんど|省)|読点.{0,6}少|絵文字|顔文字|疑問符|空白.{0,6}入れ|改行せず|ひらがな|入力速度')`
- 定数 `GENRE_EXCLUDE` = `re.compile('ES|設問|面接|志望|採用|自己PR|自己ＰＲ|就活|企業|貢献したい|社会に貢献|人物像|応募')`
- class `StyleItem` — 
- `default_policy()` — 
- `repeated_lines(pages: list[str], ratio: float=0.3)` — 多くのページに繰り返し出る行（ヘッダー・フッター）。
- `parse_text(text: str, policy: dict | None=None, skip: set[str] | None=None)` — 
- `_judge(it: StyleItem, policy: dict)` — 
- `build_profile(pdf: Path, out: Path, policy: dict | None=None, db=None)` — 
- `load_profile(path: Path)` — 
- `select_rules(prof: dict, max_rules: int, kind: str='')` — 適用可能な項目から、節ごとに均等に（決まった順で）選ぶ。全項目を一度に使わない。

## `src/structlab/token_store.py`（238 行）

```text
GitHub Actions で Dropbox の認可コードをリフレッシュトークンに交換し、GitHub Secrets に登録する。

  python -m structlab.token_store preflight   … Secrets を書き込めるか事前確認（試験用の Secret を作って消す）
  python -m structlab.token_store exchange    … Secret DROPBOX_AUTH_CODE を交換 →
                                                 DROPBOX_REFRESH_TOKEN に登録 → DROPBOX_AUTH_CODE を削除

GITHUB_TOKEN（Actions の自動トークン）は Secrets を書き込めないため、
このリポジトリの「Secrets: 読み書き」だけを許可した fine-grained PAT（Secret GH_SECRETS_TOKEN）を使う。
値は GitHub の公開鍵で暗号化（libsodium sealed box）して送り、平文はチャット・コード・コミット・ログに出さない
（ログには ::add-mask:: で伏せ字指定もする）。コードは数分で失効するので、依存ライブラリの準備より先に
標準ライブラリだけで交換できるようにしてある（暗号化に使う PyNaCl は交換の後でよい）。
```

- 定数 `TOKEN_URL` = `'https://api.dropboxapi.com/oauth2/token'`
- 定数 `GITHUB_API` = `'https://api.github.com'`
- 定数 `SECRET_NAME` = `'DROPBOX_REFRESH_TOKEN'`
- 定数 `CODE_SECRET` = `'DROPBOX_AUTH_CODE'`
- 定数 `TEST_SECRET` = `'STRUCTLAB_SECRET_WRITE_TEST'`
- class `TokenError` — 
- class `_Resp` — 
  - `json(self)` — 
- class `UrllibHttp` — requests が無い段階でも使える最小の HTTP（標準ライブラリのみ）。
  - `request(self, method: str, url: str, *, data: dict | None=None, json_body=None, headers=None)` — 
- `_env(name: str)` — 
- `mask(value: str)` — GitHub Actions のログで値を伏せ字にする（この行自体はログに表示されない）。
- `token_file()` — 
- `write_private(path: Path, data: str)` — 
- `exchange_code(code: str, app_key: str, app_secret: str, http=None)` — 認可コード → リフレッシュトークン。戻り値の refresh_token は表示しないこと。
- class `SecretsWriter` — 
  - `_call(self, method: str, path: str, body=None)` — 
  - `_fail(self, what: str, r: _Resp)` — 
  - `public_key(self)` — 
  - `put(self, name: str, value: str)` — 
  - `delete(self, name: str)` — 
  - `exists(self, name: str)` — 
- `_writer(http=None)` — 
- `cmd_preflight(http=None, out=print)` — 本人認証の前に、交換と登録に必要なものがそろっているかを確かめる（Dropbox のコードは使わない）。
- `cmd_exchange(http=None, out=print)` — DROPBOX_AUTH_CODE を交換し、権限600の一時ファイルに保存する（次の store で Secrets に登録）。
- `cmd_store(http=None, out=print)` — 一時ファイルのトークンを Secret DROPBOX_REFRESH_TOKEN に登録し、使い終わった DROPBOX_AUTH_CODE を削除する。
- `main(argv: list[str] | None=None)` — 

## `src/structlab/trace.py`（87 行）

```text
結果がどの工程を経て作られたかの追跡（教授などのフィードバックの原因箇所の確認用）。

指示1件や文章調整1件について、工程ごとの材料・判断・チェック結果を順に並べる:
  資料の読み取り（抽出・文字起こし・画面）→ 意味理解（場面の解釈の版・全体理解）→ 指示抽出（分類・照合）
  → 計算（Excelの再計算）→ 文章生成（スタイル適用前後）
原因の判定はしない（どの工程の問題かは、人が追跡結果を見て feedback に記録する）。
```

- 定数 `STAGES` = `{'extraction': '資料の読み取り', 'understanding': '意味理解', 'directives': '指示抽出', 'calculation': '計算', 'writing': '文章生成', 'unknown': '不明'}`
- `_checks(db: DB, where: str, args)` — 
- `trace_directive(db: DB, did: int)` — 
- `trace_style(db: DB, rid: int)` — 
