"""各工程に組み込んだチェックと、独立した再検証・提出物の下書きチェック。"""
from __future__ import annotations

import json
import re
import zipfile

import pytest

import dummy_data
from conftest import needs_ffmpeg, run_pipeline
from structlab.checks.extraction import ocr_quality, recalc_workbook, suspicious_numbers
from structlab.checks.formula import Evaluator, FormulaError, Unsupported
from structlab.db import DB
from test_semantic import _cfg, _pdf


def _checks(db, stage, check_type=None):
    q, a = "SELECT * FROM checks WHERE stage=?", [stage]
    if check_type:
        q += " AND check_type=?"
        a.append(check_type)
    return db.q(q, a)


# ======================= 数式の再計算 =======================
CELLS = {("S", "A1"): 2.0, ("S", "A2"): 4.0, ("S", "B1"): 1.0, ("S", "B2"): 3.0, ("S", "D2"): 78.5, ("T", "A1"): 10.0,
         ("S", "E1"): "#DIV/0!"}


def ev(f):
    return Evaluator(lambda s, c: CELLS.get((s, c)), "S").evaluate(f)


@pytest.mark.parametrize("f,v", [
    ("=A1*1000/$D$2", 2000 / 78.5), ("=AVERAGE(A1:A2)", 3.0), ("=SUM(A1:B2)+T!A1", 20.0), ("='T'!A1*2", 20.0),
    ("=SLOPE(A1:A2,B1:B2)", 1.0), ("=INTERCEPT(A1:A2,B1:B2)", 1.0), ("=ROUND(2.5,0)", 3.0), ("=ROUND(-2.5,0)", -3.0),
    ("=(A1+A2)^2-SQRT(A2)", 34.0), ("=10%*A1", 0.2), ("=-A1+3", 1.0), ("=MAX(A1:B2)-MIN(A1:B2)", 3.0),
    ('=IF(A1>1,"y","n")', "y"), ("=STDEV(A1:A2)", 2 ** 0.5),
])
def test_formula_evaluator(f, v):
    assert ev(f) == pytest.approx(v) if isinstance(v, float) else ev(f) == v


def test_formula_unsupported_and_errors():
    with pytest.raises(Unsupported):
        ev("=VLOOKUP(1,A1:B2,2)")
    with pytest.raises(FormulaError):
        ev("=A1/C9")
    with pytest.raises(FormulaError):
        ev("=E1+1")


def _set_cached(xlsx, values: dict[str, str]):
    """openpyxl で作ったブックには計算結果（キャッシュ値）が無いので、テスト用に書き込む。"""
    tmp = xlsx.with_suffix(".tmp")
    with zipfile.ZipFile(xlsx) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zi.infolist():
            data = zi.read(i)
            if i.filename == "xl/worksheets/sheet1.xml":
                s = data.decode()
                for cell, v in values.items():
                    s = re.sub(rf'(<c r="{cell}"[^>]*>)<f>(.*?)</f>(<v\s*/>|<v></v>)?', rf"\1<f>\2</f><v>{v}</v>", s)
                data = s.encode()
            zo.writestr(i, data)
    tmp.replace(xlsx)


def _cfgx(cfg_factory, src):
    return cfg_factory(src, ai={"provider": "none"})


def test_excel_recalc_ok_mismatch_unverified(tmp_path, cfg_factory):
    x = dummy_data.make_excel(tmp_path / "src" / "d.xlsx", rows=3)
    good = {f"C{i}": repr((i - 1) * 2.0 * 1000 / 78.5) for i in range(2, 5)}
    good["C6"] = repr(sum((i - 1) * 2.0 * 1000 / 78.5 for i in range(2, 5)) / 3)
    _set_cached(x, good)
    cfg = _cfgx(cfg_factory, tmp_path / "src")
    db = DB(cfg.db_path)
    db.x("INSERT INTO files(id,path,path_key,kind,status) VALUES(1,'d.xlsx','d.xlsx','excel','done')")
    c = recalc_workbook(db, cfg, db.q1("SELECT * FROM files"), x)
    assert c["ok"] == 4 and c["mismatch"] == 0
    bad = dict(good, C3="999")
    _set_cached(x, bad)
    c = recalc_workbook(db, cfg, db.q1("SELECT * FROM files"), x)
    assert c["mismatch"] == 1
    row = db.q1("SELECT * FROM checks WHERE check_type='excel_cell' AND status='mismatch'")
    assert "データ!C3" in row["detail"] and json.loads(row["expected"]) == 999
    # 計算結果が保存されていないブック（openpyxl で作っただけ）は「未検証」であって成功ではない
    y = dummy_data.make_excel(tmp_path / "y.xlsx", rows=3)
    c = recalc_workbook(db, cfg, {"id": 2, "path": "y.xlsx"}, y)
    assert c["ok"] == 0 and c["unverified"] == 4
    assert db.q1("SELECT status FROM checks WHERE check_type='excel_recalc' AND target_id='2'")["status"] == "unverified"


def test_text_quality_helpers():
    assert ocr_quality("STEP 2 =AVERAGE(B2:B10)")[0] == "ok"
    assert ocr_quality("ビーーーーーーー ￤￤￤")[0] == "needs_review"
    assert ocr_quality("")[0] == "unverified"
    assert suspicious_numbers("荷重は1O.5 kN、変位は0.25 mm") == ["1O.5"]


# ======================= 抽出時のチェック（パイプラインに組み込み） =======================
@needs_ffmpeg
def test_extraction_checks_in_pipeline(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    st = {r["check_type"]: r["status"] for r in _checks(db, "extraction")}
    assert st["av_sync"] == "ok" and st["transcription"] == "ok" and st["frames"] == "ok"
    assert st["excel_recalc"] == "unverified"  # ダミーExcelには計算結果が保存されていない
    assert "numbers" in st and "formula" in st
    # 時刻対応の崩れを検出: 発話に「その後に表示された画面」を対応付けた状態にする
    r = db.q1("SELECT t.* FROM timeline t WHERE speech LIKE '%AVERAGE%'")
    db.x("UPDATE timeline SET frame_time=? WHERE id=?", (r["t_start"] + 5, r["id"]))
    from structlab.checks.extraction import check_file
    f = db.q1("SELECT * FROM files WHERE kind='video'")
    check_file(db, cfg, f, None)
    row = db.q1("SELECT * FROM checks WHERE check_type='av_sync' AND status='mismatch'")
    assert row and "その後の画面" in row["actual"]
    md = (cfg.results_dir / "checks" / "checks.md").read_text(encoding="utf-8")
    assert "「未検証」は成功ではありません" in md and "資料抽出" in md


@needs_ffmpeg
def test_no_speech_with_audio_needs_review(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir, transcription={"backend": "mock", "mock_script": []})
    db, _, _, _ = run_pipeline(cfg)
    assert {r["status"] for r in _checks(db, "extraction", "transcription")} == {"needs_review"}
    cfg2 = cfg_factory(src_dir, transcription={"backend": "none"}, output={"work_dir": str(cfg.work_dir) + "2"})
    db2, _, _, _ = run_pipeline(cfg2)
    assert {r["status"] for r in _checks(db2, "extraction", "transcription")} == {"unverified"}


# ======================= 理解・指示のチェック =======================
@pytest.fixture
def lecture_src(tmp_path):
    src = tmp_path / "src"
    dummy_data.make_video(src / "講義/第1回_データ整理.mp4", tmp_path / "w")
    _pdf(src / "資料/レポート作成説明.pdf", ["レポートはWordで提出してください。", "断面積は78.5 mm2とする。",
                                           "試験機には触ってもよいです。"])
    return src


@needs_ffmpeg
def test_understanding_and_directive_checks(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True})
    db, _, _, _ = run_pipeline(cfg)
    st = {(r["check_type"], r["status"]) for r in _checks(db, "understanding")}
    assert ("reconciliation", "ok") in st and ("global_refs", "ok") in st and ("correction_consistency", "ok") in st
    assert ("scene_grounding", "ok") in st
    sp = {r["status"] for r in _checks(db, "directives", "speaker")}
    assert sp == {"unverified"}  # 話者識別をしていないので、先生の指示と断定しない
    vids = db.q("SELECT d.* FROM directives d JOIN sem_docs s ON s.id=d.doc_id WHERE s.mode='ai' AND d.source_path LIKE '%.mp4'")
    assert vids and all(d["speaker"] == ("none" if d["origin"] == "ai_inference" else "unknown") for d in vids)
    pdfs = db.q("SELECT d.* FROM directives d JOIN sem_docs s ON s.id=d.doc_id WHERE s.mode='ai' AND d.source_path LIKE '%.pdf'")
    assert pdfs and all(d["speaker"] == ("none" if d["origin"] == "ai_inference" else "document") for d in pdfs)
    md = (cfg.results_dir / "semantic" / "directives.md").read_text(encoding="utf-8")
    assert "発言者未確認（先生の指示と断定していない）" in md
    # 全体理解に存在しない場面番号・原文に無い根拠を入れると検出する
    from structlab.checks.semantic_checks import check_understanding
    from structlab.semantic.material import build_scenes
    doc = db.q1("SELECT d.* FROM sem_docs d JOIN files f ON f.id=d.file_id WHERE f.kind='video' AND d.mode='ai'")
    g = db.q1("SELECT * FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version DESC", (doc["id"],))
    data = json.loads(g["data_json"])
    data["flow"].append({"order": 9, "description": "x", "scene_seqs": [42]})
    data["changes"][0]["evidence"] = "この発言は動画に存在しない"
    db.x("UPDATE sem_understanding SET data_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), g["id"]))
    f = db.q1("SELECT * FROM files WHERE id=?", (doc["file_id"],))
    check_understanding(db, doc, f, build_scenes(db, f))
    st = {r["check_type"]: r for r in _checks(db, "understanding") if r["file_id"] == f["id"]}
    assert st["global_refs"]["status"] == "mismatch" and "#42" in st["global_refs"]["detail"]
    assert st["change_evidence"]["status"] == "needs_review"


@needs_ffmpeg
def test_speaker_confirmed_by_user(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True, "video_speaker": "teacher"})
    db, _, _, _ = run_pipeline(cfg)
    vids = db.q("SELECT * FROM directives WHERE source_path LIKE '%.mp4' AND effective='effective'")
    assert vids and all(d["speaker"] == "teacher_confirmed" for d in vids)
    assert {r["status"] for r in _checks(db, "directives", "speaker") if r["detail"].startswith("動画")} == {"ok"}


def test_ai_teacher_claim_is_not_confirmed(tmp_path):
    from structlab.config import load_config
    from structlab.semantic.engine import SemanticEngine
    cfg = load_config(None, {"output": {"work_dir": str(tmp_path)}})
    e = SemanticEngine(cfg, DB(tmp_path / "s.db"))
    assert e._speaker("teacher", None) == "teacher_inferred"
    assert e._speaker("unknown", None) == "unknown"
    assert e._speaker(None, "document") == "document"


# ======================= 複数資料の統合チェック =======================
@needs_ffmpeg
def test_integration_numeric_and_submission(lecture_src, cfg_factory):
    (lecture_src / "資料" / "補足.txt").write_text("断面積は80.0 mm2で計算すること。レポートはPDFで提出してください。",
                                                encoding="utf-8")
    x = dummy_data.make_excel(lecture_src / "データ" / "測定.xlsx", rows=3)
    cfg = _cfg(cfg_factory, lecture_src)
    db, _, _, _ = run_pipeline(cfg)
    rows = {r["target_id"]: r for r in _checks(db, "integration")}
    num = rows["断面積[mm2]"]
    assert num["status"] == "mismatch"
    vals = {v["value"] for v in json.loads(num["actual"])}
    assert vals == {78.5, 80.0}
    assert any("測定.xlsx" in v["source"] and "D2" in v["source"] for v in json.loads(num["actual"]))  # Excel の定数も照合
    assert rows["提出形式"]["status"] == "mismatch"
    assert x.exists()


# ======================= 独立した再検証 =======================
@needs_ffmpeg
def test_reverify_independent_and_not_overwriting(lecture_src, cfg_factory):
    from test_semantic import MockProvider

    class Flipper(MockProvider):  # 第3回照合でも食い違いを解消できなかった状態を作る
        def analyze(self, req):
            r = super().analyze(req)
            if req.task == "directive_verify" and "直接入力" in req.text:
                r.data = {"verdict": "unresolved", "reason": "x", "evidence_quote": "", "revised": None,
                          "additional_directives": []}
            return r

    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True})
    db, _, _, _ = run_pipeline(cfg, provider=Flipper())
    d = db.q1("SELECT * FROM directives WHERE content LIKE '%直接入力%' AND extracted_by LIKE 'ai:%'")
    assert d["directive_class"] == "recommended" and d["verification"] == "needs_review"
    from structlab.checks.reverify import reverify
    c = reverify(db, cfg, provider=None)  # 規則だけ（無料）
    row = db.q1("SELECT * FROM checks WHERE stage='reverify' AND target_id=?", (str(d["id"]),))
    assert row["status"] == "mismatch" and "向きが逆" in row["detail"]
    rv = db.q1("SELECT * FROM revisions WHERE target_id=?", (str(d["id"]),))
    assert rv["before"] == "recommended" and rv["after"] == "prohibited" and rv["status"] == "proposed"
    assert db.q1("SELECT directive_class FROM directives WHERE id=?", (d["id"],))["directive_class"] == "recommended"  # 自動では書き換えない
    # AIの推論（原文に無い）は、どちらの再抽出でも確認できない → 未検証（検証済みにしない）
    inf = db.q1("SELECT * FROM directives WHERE origin='ai_inference' AND effective='effective'")
    if inf:
        assert db.q1("SELECT status FROM checks WHERE stage='reverify' AND target_id=?", (str(inf["id"]),))["status"] == "unverified"
    # 同じ入力では繰り返さない
    c2 = reverify(db, cfg, provider=None)
    assert c2.get("skipped_same_input", 0) == sum(v for k, v in c.items() if k != "skipped_same_input")
    # AIによる盲検の再抽出: 最初の解釈・対象の指示を渡さない
    prov = MockProvider()
    db.x("DELETE FROM checks WHERE stage='reverify'")
    reverify(db, cfg, provider=prov)
    blind = [x for x in prov.calls if x.task == "blind_directives"]
    assert blind and all("直接入力" not in x.text or "## 確認対象" not in x.text for x in blind)
    assert all("資料全体の理解" not in x.text for x in blind)
    row = db.q1("SELECT * FROM checks WHERE stage='reverify' AND target_id=?", (str(d["id"]),))
    act = json.loads(row["actual"])
    assert act["blind_ai"]["status"] == "mismatch" and act["rules"]["status"] == "mismatch"


# ======================= 将来の提出物（下書き）のチェック =======================
@needs_ffmpeg
def test_check_draft_text_and_excel(lecture_src, cfg_factory, tmp_path):
    cfg = _cfg(cfg_factory, lecture_src)
    db, _, _, _ = run_pipeline(cfg)
    from structlab.checks.deliverable import check_draft
    draft = tmp_path / "下書き.md"
    draft.write_text("# 結果\n断面積は78.5 mm2とした。降伏点は245.3 N/mm2であった。荷重は78.5であった。\n", encoding="utf-8")
    check_draft(db, cfg, draft, fmt="PDF")
    rows = {(r["check_type"], r["target_id"].split(":", 1)[1]): r for r in _checks(db, "deliverable")}
    assert rows[("numbers", "78.5")]["status"] == "ok"
    assert rows[("numbers", "245.3")]["status"] == "unverified"  # 元資料で確認できない数値は成功にしない
    sub = [r for k, r in rows.items() if k[0] == "submission"]
    assert sub and sub[0]["status"] == "needs_review"  # 動画はPDF、資料はWord → どれに従うか要確認
    assert any(k[0] == "directive_compliance" and r["status"] == "unverified" for k, r in rows.items())
    # Excel の下書き: 散布図・近似曲線・凡例を指示と照合
    x = dummy_data.make_excel(tmp_path / "draft.xlsx", rows=3)
    check_draft(db, cfg, x)
    rows = {r["check_type"]: r for r in _checks(db, "deliverable") if r["target_type"] == "draft:draft.xlsx"}
    assert rows["chart_type"]["status"] == "ok" and rows["trendline"]["status"] == "ok" and rows["legend"]["status"] == "ok"
    assert rows["excel_recalc"]["status"] == "unverified"
