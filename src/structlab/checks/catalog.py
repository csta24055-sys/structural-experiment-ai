"""システム全体のチェック項目の一覧（カタログ）。

先にシステム全体として必要な検証要件（R1〜R15）を定め、それを満たすチェック項目を、最も適した工程に割り当てる。
既存の検証も含めて全項目をここに登録し、各工程のチェックを合わせたときに要件が網羅されることを一覧で確認できるようにする。

mode:
  runtime  … 実行のたびに結果を checks テーブルに記録する
  design   … 仕組み（コード・設定の検証）で常に保証し、自動テストで確認する（実行時の記録はしない）
  future   … 提出物の作成など、今は存在しない工程で実行する（下書きを渡せば今でも実行できる）
conditional: その状況のときだけ記録される（例: Excel が含まれるとき、不一致があるときだけの明細）
origin: 前回までに実装済みの検証か（existing）、この改修で追加したか（new）。既存の検証は再実行せず結果を記録する
"""
from __future__ import annotations

REQUIREMENTS: dict[str, str] = {
    "R1": "資料を全件・最新版・改変なしで取得し、アップロード途中の物を使わない",
    "R2": "危険なファイル・上限を超えるファイルを安全に扱う",
    "R3": "資料の内容（全ページ・全区間・全セル、文字・数値・数式・表・グラフ）を漏れなく正確に読み取る",
    "R4": "音声と映像を時刻で正しく対応付ける",
    "R5": "場面ごとの解釈が原文（発話・画面・資料）に根拠を持ち、前後の説明と整合する",
    "R6": "資料全体の解釈が場面の解釈と整合し、訂正・補足・例外・条件変更・矛盾を反映する",
    "R7": "指示を漏れなく、正しい分類・条件・発言者・対象・根拠で記録する",
    "R8": "指示同士の関係（訂正・例外・繰り返し・矛盾）を、根拠なく決めつけずに扱う",
    "R9": "複数の資料の間で、数値・提出条件・指示・作業手順の一致と矛盾を確認する",
    "R10": "数値・数式・計算を再現できる",
    "R11": "提出物（Excel・レポート）が資料・計算・指示・提出条件と一致する（将来の工程）",
    "R12": "文章スタイルの調整で内容が変わらず、スタイルは最終工程だけに使う",
    "R13": "最初の結論に依存しない独立した確認ができる",
    "R14": "処理と解析の網羅状況・失敗・未処理・上限を隠さず示す",
    "R15": "元資料を書き換えず、外部送信は許可時のみ、秘密情報・個人情報を保存しない",
}

STAGE_ORDER = ["acquisition", "extraction", "understanding", "directives", "integration", "deliverable", "style",
               "reverify", "operations"]


def _c(cid, req, stage, check_type, title, how, origin="new", mode="runtime", conditional=False, ai=False, tests=()):
    return {"id": cid, "requirements": req if isinstance(req, list) else [req], "stage": stage, "check_type": check_type,
            "title": title, "how": how, "origin": origin, "mode": mode, "conditional": conditional, "ai": ai,
            "tests": list(tests)}


CATALOG: list[dict] = [
    # ---------------- 資料の取得（acquisition） ----------------
    _c("A1", "R1", "acquisition", "scan_complete", "フォルダ以下の全件列挙", "一覧取得のページングを最後まで行えたか", "existing"),
    _c("A2", "R1", "acquisition", "upload_stability", "アップロード途中の除外", "更新直後・ハッシュ変化直後のファイルを待機に回したか",
       "existing", conditional=True),
    _c("A3", "R1", "acquisition", "download_integrity", "取得内容の照合", "スキャン時のリビジョンを取得し、Dropbox content_hash と照合済みか",
       "existing"),
    _c("A4", "R1", "acquisition", "version_current", "最新版の解析結果", "結果が現在の版（content_hash）に対応しているか", "existing"),
    _c("A5", "R1", "acquisition", "ignored", "一時ファイル等の除外", "~$*・*.part 等を対象外にした記録", "existing", conditional=True),
    _c("A6", "R1", "acquisition", "duplicate", "同一内容の重複", "内容が同一のコピーを1回だけ解析したか", "existing", conditional=True),
    _c("A7", "R2", "acquisition", "zip_safety", "ZIPの安全な展開", "危険なパス・シンボリックリンク・暗号化・ZIP爆弾を展開しなかったか",
       "existing", conditional=True),
    _c("A8", "R2", "acquisition", "size_limit", "サイズ・入れ子の上限", "上限超過でスキップしたファイル", "existing", conditional=True),
    _c("A9", "R3", "acquisition", "format_supported", "対応形式", "未対応形式（旧.xls 等）を解析済みと扱っていないか", "existing",
       conditional=True),
    _c("A10", "R14", "acquisition", "file_processing", "ファイル単位の処理結果", "処理に失敗・一部失敗したファイル", "existing",
       conditional=True),
    # ---------------- 資料抽出（extraction） ----------------
    _c("B1", "R3", "extraction", "read", "読み取り失敗", "失敗した単位（ページ・区間・シート範囲）", "new", conditional=True),
    _c("B2", "R3", "extraction", "unit_coverage", "分割の網羅", "全ページ・全時間・全行が解析単位で隙間なく覆われ、すべて抽出済みか"),
    _c("B3", "R4", "extraction", "av_sync", "音声と映像の時刻対応", "発話が区間内か、発話時点で表示済みの画面を対応付けたか", conditional=True),
    _c("B4", ["R3", "R4"], "extraction", "transcription", "文字起こし", "未実施・発話ゼロ・低信頼の区間", conditional=True),
    _c("B5", "R3", "extraction", "frames", "画面の取得", "映像のある区間でキーフレームを取得できたか", conditional=True),
    _c("B6", "R3", "extraction", "ocr", "OCR", "文字化けの疑い・OCRなし（スキャン・画面・画像）", conditional=True),
    _c("B7", ["R3", "R10"], "extraction", "numbers", "数値の誤読", "数字と英字 O/l/I の混入（資料・画像・画面の文字）"),
    _c("B8", ["R3", "R10"], "extraction", "formula", "数式の構文", "数式候補の括弧の対応", conditional=True),
    _c("B9", "R3", "extraction", "table_structure", "表の構造", "抽出した表の列数のそろい", conditional=True),
    _c("B10", ["R3", "R10"], "extraction", "excel_recalc", "Excelの再計算", "全数式を入力値から計算し直し保存値と照合（未対応関数は未検証）",
       conditional=True),
    _c("B11", ["R3", "R10"], "extraction", "excel_cell", "Excelのセル明細", "不一致・エラー値・空セル参照・未検証のセル", conditional=True),
    _c("B12", "R3", "extraction", "excel_truncated", "Excelのセル数上限", "上限で記録を打ち切ったシート範囲", conditional=True),
    _c("B13", "R3", "extraction", "chart_refs", "グラフ定義の参照", "系列が存在するシートを参照しているか", conditional=True),
    _c("B14", ["R3", "R14"], "extraction", "needs_ai", "AIでしか読めない内容", "図・写真・画面の内容がAI未確認のまま残っていないか", "existing"),
    _c("B15", "R5", "extraction", "legacy_evidence", "単位ごとのAI抽出の根拠", "従来の10分類の項目の根拠が原文にあるか", "existing",
       conditional=True, ai=True),
    # ---------------- 局所理解・全体理解（understanding） ----------------
    _c("C1", ["R5", "R14"], "understanding", "understanding", "意味理解の実施", "AIによる意味理解が未実施（規則のみ）であることの明示",
       conditional=True),
    _c("C2", "R5", "understanding", "scene_grounding", "場面の解釈の根拠", "要点の時刻・訂正等の根拠の引用が場面の原文にあるか", ai=True,
       conditional=True),
    _c("C3", "R5", "understanding", "scene_neighbors", "前後の説明との関係", "各場面で直前・直後との関係が解釈されているか", ai=True,
       conditional=True),
    _c("C4", ["R5", "R14"], "understanding", "unreadable", "読み取り不能の明示", "場面ごとの読み取り不能箇所", ai=True, conditional=True),
    _c("C5", "R6", "understanding", "global_refs", "全体理解の参照", "場面番号・手順番号の参照が実在するか", ai=True, conditional=True),
    _c("C6", "R6", "understanding", "change_evidence", "訂正・補足・整合説明の根拠", "全体理解が挙げた根拠が原文にあるか", ai=True,
       conditional=True),
    _c("C7", "R6", "understanding", "correction_consistency", "訂正の把握の一致", "原文の訂正の言い回しと全体理解の訂正が一致するか",
       ai=True, conditional=True),
    _c("C8", "R6", "understanding", "reconciliation", "相互照合の収束", "局所と全体の再解釈が収束したか", "existing", ai=True,
       conditional=True),
    _c("C9", ["R6", "R14"], "understanding", "global_completeness", "全体理解の欠落", "局所理解に失敗した場面を含まない全体理解の警告",
       ai=True, conditional=True),
    _c("C10", "R6", "understanding", "apparent_contradictions", "一見矛盾する説明", "整合できなかった説明の食い違いを未解決として残したか",
       "existing", ai=True, conditional=True),
    _c("C11", "R6", "understanding", "scene_role_coverage", "全場面の位置付け", "全場面が全体の流れ・役割に位置付けられているか", ai=True,
       conditional=True),
    # ---------------- 指示抽出（directives） ----------------
    _c("D1", "R7", "directives", "classification", "分類と原文の言い回し", "分類の向き・強さが原文と矛盾しないか（第3回照合の結果）", "existing"),
    _c("D2", "R7", "directives", "evidence", "根拠の引用", "根拠の引用が元の範囲にあるか（第3回照合の結果）", "existing"),
    _c("D3", "R7", "directives", "time_located", "根拠の時刻", "動画の指示に時刻があるか", "existing", conditional=True),
    _c("D4", "R7", "directives", "condition", "適用条件", "条件付き指示に条件があるか"),
    _c("D5", "R7", "directives", "speaker", "発言者", "話者識別なしの音声を先生の指示と断定していないか", conditional=True),
    _c("D6", "R7", "directives", "target", "対象作業", "指示の対象作業が特定されているか", ai=True, conditional=True),
    _c("D7", "R7", "directives", "omission", "見落とし候補", "規則候補とAI抽出の突き合わせで残った見落とし候補", "existing"),
    _c("D8", "R7", "directives", "inference_separation", "推論の区別", "AIの推論を明示された指示・先生の発言として扱っていないか", "existing"),
    _c("D9", ["R7", "R14"], "directives", "verify_cap", "照合の上限", "上限のため再解析していない指示", "existing", conditional=True),
    _c("D10", "R8", "directives", "relations", "訂正・例外等の関係先", "関係先を特定できない／AIだけが主張する訂正", "existing"),
    _c("D11", "R8", "directives", "doc_conflicts", "資料内の矛盾", "向きが逆の指示を未解決の矛盾として記録したか", "existing"),
    # ---------------- 複数資料の統合（integration） ----------------
    _c("E1", ["R9", "R10"], "integration", "numeric", "資料間の数値", "項目・単位が同じ数値（Excelの定数を含む）の一致", conditional=True),
    _c("E2", "R9", "integration", "submission", "提出条件", "提出形式・期限・枚数・有効数字の一致", conditional=True),
    _c("E3", ["R8", "R9"], "integration", "directive_conflicts", "資料間の指示の矛盾", "向きが逆・値が異なる指示", "existing"),
    _c("E4", "R9", "integration", "common_directives", "共通の指示", "複数資料に共通する指示の関連付け", "existing"),
    _c("E5", "R9", "integration", "procedure", "作業手順の根拠", "統合理解の手順が実在する資料・手順を根拠にしているか"),
    _c("E6", ["R9", "R14"], "integration", "corpus_completeness", "統合の対象", "全資料の解析・意味理解が統合に含まれているか"),
    # ---------------- 提出物（deliverable・将来） ----------------
    _c("F1", ["R10", "R11"], "deliverable", "excel_recalc", "提出Excelの再計算", "数式の再現性", mode="future", conditional=True),
    _c("F2", "R11", "deliverable", "units", "単位", "列見出し・数値の単位", mode="future", conditional=True),
    _c("F3", "R11", "deliverable", "chart_type", "グラフの種類", "指示（散布図など）との一致", mode="future", conditional=True),
    _c("F4", "R11", "deliverable", "trendline", "近似曲線", "指示との一致", mode="future", conditional=True),
    _c("F5", "R11", "deliverable", "legend", "凡例", "指示との一致", mode="future", conditional=True),
    _c("F6", "R11", "deliverable", "axis_titles", "軸タイトル", "全グラフの軸タイトル", mode="future", conditional=True),
    _c("F7", ["R10", "R11"], "deliverable", "numbers", "数値の出典", "文章中の数値が元資料・計算で確認できるか", mode="future", conditional=True),
    _c("F8", "R11", "deliverable", "sections", "必要な章", "指示された章（考察など）の有無", mode="future", conditional=True),
    _c("F9", "R11", "deliverable", "discussion", "考察", "考察の有無と、妥当性が未検証であることの明示", mode="future", conditional=True),
    _c("F10", "R11", "deliverable", "submission", "提出条件", "提出形式・ページ数", mode="future", conditional=True),
    _c("F11", "R11", "deliverable", "directive_compliance", "指示の遵守", "必須・禁止・条件付き指示の確認リスト（人が確認）", mode="future",
       conditional=True),
    # ---------------- 文章スタイル（style・最終工程） ----------------
    _c("G1", "R12", "style", "preservation", "内容の保存", "数値・数式・引用・否定・断定の強さ・結論・書き足しの検査", mode="future",
       conditional=True, ai=True),
    _c("G2", "R12", "style", "profile_scope", "適用する項目の範囲", "正確性を損なう項目・就活向け項目の除外と理由", mode="future",
       conditional=True),
    _c("G3", "R12", "style", "isolation", "最終工程だけで使う", "解析・指示判断・計算・チェックのコードがスタイルを参照しない", mode="design",
       tests=["test_style_is_isolated_from_analysis"]),
    _c("G4", "R12", "style", "original_saved", "適用前の保存", "適用前の文章を必ず保存", mode="design",
       tests=["test_style_disabled_or_missing_pdf_does_not_stop", "test_style_apply_and_rejects_content_changes"]),
    # ---------------- 独立した再検証（reverify） ----------------
    _c("H1", ["R13", "R7"], "reverify", "directive", "指示の独立した再抽出", "規則・AI盲検で元の範囲から取り出し直して突き合わせ",
       conditional=True),
    _c("H2", ["R13", "R10"], "reverify", "recalculation", "数値・数式の独立した再計算", "B10・E1 が入力値から毎回計算し直している（重複して行わない）",
       mode="design", tests=["test_excel_recalc_ok_mismatch_unverified", "test_integration_numeric_and_submission"]),
    # ---------------- 運用・全体（operations） ----------------
    _c("O1", "R14", "operations", "processing_completeness", "処理の網羅", "未処理・待機・失敗のファイルが残っていないか", "existing"),
    _c("O2", "R14", "operations", "semantic_coverage", "解析網羅率", "各段階を処理した時間（単位）の割合と未処理の範囲", "existing"),
    _c("O3", "R14", "operations", "ai_budget", "AI使用量の上限", "上限で止めた処理が残っていないか", "existing"),
    _c("O4", "R15", "operations", "external_permission", "外部送信の許可", "許可なしに外部AIを使っていないか", "existing"),
    _c("O5", "R15", "operations", "source_readonly", "元資料の保護", "出力先が元資料のフォルダと重ならず、取得側に書き込み処理がない", "existing"),
    _c("O6", "R15", "operations", "secrets", "秘密情報", "設定ファイルにAPIキー等を書かない（読み込み時に拒否）", "existing", mode="design",
       tests=["test_secrets_in_config_rejected"]),
    _c("O7", "R15", "operations", "private_style_pdf", "個人情報（文章スタイルPDF）", "PDFと読み取り結果をリポジトリに含めない（.gitignore）",
       mode="design", tests=["test_style_pdf_not_in_repository"]),
    _c("O8", ["R1", "R14"], "operations", "resume_idempotent", "再開・重複処理の防止", "途中停止から再開し、完了済みを再処理しない", "existing",
       mode="design", tests=["test_stop_and_resume_without_rework", "test_second_run_does_nothing", "test_second_ai_run_makes_no_new_calls"]),
]

BY_TYPE = {(c["stage"], c["check_type"]): c for c in CATALOG}


def coverage_matrix() -> dict[str, dict[str, list[str]]]:
    """要件 × 工程 → チェック項目ID"""
    m: dict[str, dict[str, list[str]]] = {r: {s: [] for s in STAGE_ORDER} for r in REQUIREMENTS}
    for c in CATALOG:
        for r in c["requirements"]:
            m[r][c["stage"]].append(c["id"])
    return m
