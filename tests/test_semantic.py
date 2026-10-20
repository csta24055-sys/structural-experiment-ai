"""意味理解層: 局所理解・全体理解・相互照合・指示の網羅的抽出と分類・照合・資料横断・網羅率。

AI部分は MockProvider（決定的な模擬応答）で、多段階の処理のつながりと検証機構を確認する。
実際のAIの理解力・精度はここでは検証していない（docs/VERIFICATION.md 参照）。
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

import dummy_data
from conftest import needs_ffmpeg, run_pipeline
from structlab.ai.mock_provider import MockProvider
from structlab.semantic import rules
from structlab.semantic.rules import directives_in

LECTURE = [
    (2, "今日は鋼材の引張試験のデータ整理をします。"),
    (5, "まず荷重と変位をExcelに入力してください。"),
    (8, "グラフは散布図にして、凡例は消さないでください。"),
    (12, "応力度は平均値を使って計算すること。"),
    (17, "試験機には絶対に触らないでください。"),
    (23, "考察は長く書かなくてもいいです。"),
    (26, "近似直線を引いた方がいいです。"),
    (29, "欠席した場合は、必ず友達からデータをもらってください。"),
    (33, "[[MISS]]表紙には学籍番号を必ず書くこと。"),
    (36, "[[MISCLASS]]セルの値を直接入力してはいけません。"),
    (41, "さっき平均値を使うと言いましたが、訂正します。応力度は中央値を使って計算すること。"),
    (44, "ただし、試験体Bは除いてください。"),
    (47, "レポートはPDFで提出してください。"),
]
PDF_LINES = ["レポートはWordで提出してください。", "グラフは散布図にしてください。", "試験機には触ってもよいです。",
             "有効数字は3桁にすること。"]


def _pdf(path, lines):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    c.setFont("HeiseiKakuGo-W5", 12)
    y = 780
    for s in lines:
        c.drawString(50, y, s)
        y -= 22
    c.showPage()
    c.save()


@pytest.fixture
def lecture_src(tmp_path):
    src = tmp_path / "src"
    dummy_data.make_video(src / "講義/第1回_データ整理.mp4", tmp_path / "w")
    _pdf(src / "資料/レポート作成説明.pdf", PDF_LINES)
    return src


def _cfg(cfg_factory, src, **over):
    script = [{"start": a, "end": a + 2.5, "text": b} for a, b in LECTURE]
    over.setdefault("transcription", {"backend": "mock", "mock_script": script})
    return cfg_factory(src, **over)


def _dirs(db, mode):
    return db.q("SELECT d.* FROM directives d JOIN sem_docs s ON s.id=d.doc_id WHERE s.mode=? ORDER BY d.t_start, d.id",
                (mode,))


# ======================= 規則（言い回しによる分類） =======================
@pytest.mark.parametrize("text,expected", [
    ("試験機には絶対に触らないでください。", ["prohibited"]),
    ("セルの値を直接入力してはいけません。", ["prohibited"]),
    ("できれば手書きは避けた方がいいです。", ["discouraged"]),
    ("有効数字は3桁にすること。", ["mandatory"]),
    ("必ず単位を書いてください。", ["mandatory"]),
    ("表紙をつけてほしいです。", ["request"]),
    ("近似直線を引いた方がいいです。", ["recommended"]),
    ("考察は長く書かなくてもいいです。", ["optional"]),
    ("レポートはWordでもPDFでも構いません。", ["optional"]),
    ("欠席した場合は、必ず友達からデータをもらってください。", ["conditional"]),
    ("ここは間違えやすいので気をつけて。", ["other"]),
    ("グラフは散布図にして、凡例は消さないでください。", ["request", "prohibited"]),  # 1発言に2つの指示
    ("今日は引張試験をします。", []),
    # 話し言葉
    ("ここで数字を手で打ち込んじゃダメです。", ["prohibited"]),
    ("単位は書かないとダメだよ。", ["mandatory"]),
    ("グラフのタイトルは消さないでね。", ["prohibited"]),
    ("データは保存しといてね。", ["request"]),
    ("先に表を作っといた方がいいです。", ["recommended"]),
])
def test_rule_classes(text, expected):
    assert [d.directive_class for d in directives_in(text)] == expected


def test_rule_condition_and_base_strength():
    d = directives_in("欠席した場合は、必ず友達からデータをもらってください。")[0]
    assert d.condition == "欠席した場合は" and d.base_strength == "mandatory"


def test_rule_discourse_carries_to_next_instruction():
    d = directives_in("さっき平均を取ると言いましたが、訂正します。中央値を使ってください。")
    assert len(d) == 1 and "correction" in d[0].discourse and "中央値" in d[0].content
    assert "exception" in directives_in("ただし、試験体Bは除いてください。")[0].discourse


def test_te_form_split_is_marked_uncertain():
    a, b = directives_in("グラフは散布図にして、凡例は消さないでください。")
    assert a.certainty == "low" and "推定" in a.note and b.certainty == "medium"


def test_target_similarity():
    assert rules.target_similarity("試験機には絶対に触らないでください。", "試験機には触ってもよいです。") == 1.0
    assert rules.target_similarity("グラフは散布図にして", "近似直線を引いた方がいいです") == 0.0


# ======================= local モード（AI不使用・既定） =======================
@needs_ffmpeg
def test_local_mode_extracts_directives_with_evidence(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, ai={"provider": "none"})
    db, _, _, _ = run_pipeline(cfg, use_mock=False)
    assert db.q1("SELECT COUNT(*) c FROM api_usage")["c"] == 0
    ds = _dirs(db, "local")
    by = Counter(d["directive_class"] for d in ds if d["source_path"].endswith(".mp4"))
    assert by["prohibited"] == 3 and by["optional"] == 1 and by["recommended"] == 1 and by["conditional"] == 1
    for d in ds:  # 根拠: 資料名・時刻（動画）またはページ・発言・前後の文脈
        assert d["source_path"] and d["evidence"] and d["origin"] == "explicit" and d["extracted_by"] == "rule"
        if d["source_path"].endswith(".mp4"):
            assert d["t_start"] is not None and d["speech"]
        else:
            assert d["page"] == 1
    miss = next(d for d in ds if "学籍番号" in d["content"])
    assert miss["context_before"] and miss["context_after"]
    assert miss["screen"].startswith("frame_")  # 発話時に表示されていた画面
    # 明示的な訂正: 前の指示（平均値）は無効、後の指示（中央値）が有効
    old = next(d for d in ds if "平均値を使って計算" in d["content"])
    new = next(d for d in ds if "中央値" in d["content"])
    assert old["effective"] == "superseded" and new["effective"] == "effective"
    link = db.q1("SELECT * FROM directive_links WHERE from_id=? AND to_id=? AND relation='correction_of'", (new["id"], old["id"]))
    assert link["status"] == "resolved_explicit"
    exc = next(d for d in ds if "試験体B" in d["content"])
    assert db.q1("SELECT * FROM directive_links WHERE from_id=? AND relation='exception_to'", (exc["id"],))


@needs_ffmpeg
def test_local_mode_reports_and_coverage(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, ai={"provider": "none"})
    run_pipeline(cfg, use_mock=False)
    out = cfg.results_dir / "semantic"
    for name in ("directives.md", "directives.json", "directives.csv", "requirements.md", "experiment.md", "unconfirmed.md",
                 "conflicts.md", "changes.md", "failures.md", "coverage.md", "coverage.json"):
        assert (out / name).exists(), name
    assert list((out / "scenes").glob("*.md")) and list((out / "understanding").glob("*.md"))
    md = (out / "directives.md").read_text(encoding="utf-8")
    for lbl in rules.DIRECTIVE_CLASSES.values():
        assert f"## {lbl}" in md
    u = next((out / "understanding").glob("*mp4.md")).read_text(encoding="utf-8")
    assert "AIによる全体理解は未生成" in u
    assert "AIによる資料横断の統合理解は未生成" in (out / "experiment.md").read_text(encoding="utf-8")
    cov = json.loads((out / "coverage.json").read_text(encoding="utf-8"))
    v = next(c for c in cov if c["kind"] == "video")
    assert v["mode"] == "local" and v["metrics"]["規則による指示抽出"]["ratio"] == 1.0
    assert "第1回 局所理解" not in v["metrics"]  # AI未実行の段階を「完了」と見せない
    text = (out / "coverage.md").read_text(encoding="utf-8")
    assert "すべての指示を漏れなく理解できたことを意味しません" in text and "検出率" in text
    conf = (out / "conflicts.md").read_text(encoding="utf-8")
    assert "試験機には触ってもよいです" in conf and "提出形式の値が異なる" in conf


@needs_ffmpeg
def test_untranscribed_audio_shows_in_coverage(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, ai={"provider": "none"}, transcription={"backend": "none"})
    run_pipeline(cfg, use_mock=False)
    cov = json.loads((cfg.results_dir / "semantic" / "coverage.json").read_text(encoding="utf-8"))
    v = next(c for c in cov if c["kind"] == "video")
    assert v["metrics"]["文字起こし"]["ratio"] == 0.0 and v["metrics"]["文字起こし"]["missing"]
    assert v["metrics"]["画面取得"]["ratio"] == 1.0


# ======================= AI モード（模擬AI） =======================
@pytest.fixture
def ai_run(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True})
    db, _, stats, prov = run_pipeline(cfg)
    return cfg, db, stats, prov


@needs_ffmpeg
def test_three_stages_in_order_with_context(ai_run):
    cfg, db, _, prov = ai_run
    vid = [c for c in prov.calls if c.schema is not None and c.source_label.startswith("講義/")]
    order = [c.task for c in vid]
    first = {t: order.index(t) for t in set(order)}
    assert first["scene_analysis"] < first["global_synthesis"] < first["scene_reinterpret"] \
        < first["directive_extraction"] < first["directive_verify"]
    scenes = [c for c in vid if c.task == "scene_analysis"]
    assert len(scenes) == 3
    # 局所理解: 映像（時刻付きフレーム）と音声（時刻付き発話）を同時に渡し、前後の文脈も渡す
    assert all(c.images and all("の画面" in i.label for i in c.images) for c in scenes)
    assert "[00:00:17] 発話: 試験機には絶対に触らないでください。" in scenes[0].text
    assert "（なし：最初の場面）" in scenes[0].text and "（なし：最初の場面）" not in scenes[1].text
    assert "## 直後の場面の発話・内容（参考）\n[00:00:23]" in scenes[0].text
    # 再解釈には全体理解が渡される
    assert all("## 資料全体の理解" in c.text for c in vid if c.task == "scene_reinterpret")
    # 指示抽出には全体理解・前後の場面・規則候補が渡される
    de = [c for c in vid if c.task == "directive_extraction"]
    assert all("## 資料全体の理解" in c.text and "## 規則による候補" in c.text and "## 前後の場面" in c.text for c in de)


@needs_ffmpeg
def test_local_global_reconciliation_loop(ai_run):
    cfg, db, _, prov = ai_run
    doc = db.q1("SELECT d.* FROM sem_docs d JOIN files f ON f.id=d.file_id WHERE f.kind='video' AND d.mode='ai'")
    assert doc["status"] == "done" and doc["converged"] == 1 and doc["round"] == 2
    vers = db.q("SELECT * FROM sem_understanding WHERE scope='document' AND doc_id=? ORDER BY version", (doc["id"],))
    assert [v["version"] for v in vers] == [1, 2]  # 再解釈で変更があったので全体理解を更新
    # 後半の訂正を受けて、前半の場面の解釈が修正されている
    s1 = db.q("SELECT * FROM sem_scenes WHERE doc_id=? AND seq=1 ORDER BY pass", (doc["id"],))
    assert len(s1) == 3 and json.loads(s1[1]["changes_json"]) and not json.loads(s1[2]["changes_json"] or "[]")
    assert "後の場面で訂正" in json.loads(s1[-1]["analysis_json"])["meaning"]
    g = json.loads(vers[-1]["data_json"])
    assert g["changes"][0]["type"] == "correction" and g["changes"][0]["later_scene"] == 3
    md = next((cfg.results_dir / "semantic" / "understanding").glob("*mp4.md")).read_text(encoding="utf-8")
    for h in ("## テーマ", "## 説明の流れ", "## 作業手順と依存関係", "## 各場面の役割", "## 訂正・補足・例外・条件変更",
              "## 一見矛盾する説明", "## 最終的に守るべき要件", "## 学生に実行してほしいこと", "## 詳しい説明"):
        assert h in md


@needs_ffmpeg
def test_omission_and_misclassification_are_caught(ai_run):
    cfg, db, _, prov = ai_run
    ds = _dirs(db, "ai")
    # [[MISS]]: AIが拾わなかった指示を規則候補との突き合わせで検出し、第3回で元の発話と照合して採用
    miss = next(d for d in ds if "学籍番号" in d["content"])
    assert miss["extracted_by"] == "rule+verify" and miss["verification"] == "verified" and miss["effective"] == "effective"
    # [[MISCLASS]]: AIが禁止を推奨と分類 → 原文の言い回しと逆なので照合へ → 原文に基づいて禁止に修正
    mis = next(d for d in ds if "直接入力" in d["content"] and d["extracted_by"].startswith("ai:"))
    assert mis["directive_class"] == "prohibited" and mis["verification"] == "revised"
    notes = json.loads(mis["verification_notes"])
    assert any("原文の言い回し" in n and "逆" in n for n in notes) and any("修正前: [E. 推奨事項]" in n for n in notes)
    # 照合では元動画から画面を取り直し、区間をまたいで再文字起こししている
    ver = [c for c in prov.calls if c.task == "directive_verify" and "直接入力" in c.text]
    assert ver and any("（再取得）" in i.label for i in ver[0].images) and "再文字起こし" in ver[0].text
    # AIの推論は「推論」として区別され、明示された指示と混ざらない
    inf = [d for d in ds if d["origin"] == "ai_inference"]
    assert inf and all(d["extracted_by"].startswith("ai:") for d in inf)


@needs_ffmpeg
def test_directive_record_has_all_fields(ai_run):
    cfg, db, _, _ = ai_run
    recs = json.loads((cfg.results_dir / "semantic" / "directives.json").read_text(encoding="utf-8"))
    must = {"content", "class", "target_task", "condition", "source", "time_start", "time_end", "screen", "operation",
            "speech", "context_before", "context_after", "certainty", "unconfirmed", "origin", "relations",
            "verification", "effective"}
    assert recs and all(must <= set(r) for r in recs)
    r = next(r for r in recs if r["content"].startswith("試験機には絶対に") and r["extracted_by"].startswith("ai:"))
    assert r["time_start"] == 17 and r["source"].endswith(".mp4") and r["context_before"] and r["screen"]
    corr = next(r for r in recs if "中央値" in r["content"] and r["extracted_by"].startswith("ai:"))
    assert any(x["relation"] == "correction_of" and x["status"] == "resolved_explicit" for x in corr["relations"])


@needs_ffmpeg
def test_corpus_integration_is_separate_and_validated(ai_run):
    cfg, db, _, prov = ai_run
    corp = db.q("SELECT * FROM sem_understanding WHERE scope='corpus'")
    assert len(corp) == 1 and [c.task for c in prov.calls].count("corpus_integration") == 1
    data = json.loads(corp[0]["data_json"])
    assert data["_dropped_invalid_ids"] == [{"section": "deliverables", "ids": [999999]}]  # 存在しない指示番号は除外
    assert db.q1("SELECT COUNT(*) c FROM sem_understanding WHERE scope='document'")["c"] >= 2  # 資料単位とは別に保存
    links = db.q("SELECT * FROM directive_links WHERE scope='corpus' AND relation='conflicts_with'")
    assert len({frozenset((l["from_id"], l["to_id"])) for l in links}) == 2  # 触る⇔触るな、PDF⇔Word
    assert all(l["status"] == "unresolved" for l in links)
    same = db.q("SELECT * FROM directive_links WHERE scope='corpus' AND relation='same_as'")
    assert same  # 散布図の指示が動画とPDFに共通
    out = cfg.results_dir / "semantic"
    assert "最終的に提出するもの" in (out / "experiment.md").read_text(encoding="utf-8")
    assert "複数資料に共通する指示" in (out / "requirements.md").read_text(encoding="utf-8")


@needs_ffmpeg
def test_second_ai_run_makes_no_new_calls(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True})
    db, _, _, _ = run_pipeline(cfg)
    n = db.q1("SELECT COUNT(*) c FROM directives")["c"]
    db.close()
    db, _, _, prov = run_pipeline(cfg)
    assert prov.calls == [] and db.q1("SELECT COUNT(*) c FROM directives")["c"] == n


@needs_ffmpeg
def test_budget_stop_and_resume_without_redoing(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True}, ai={"provider": "mock", "max_calls_per_run": 8})
    db, _, stats, p1 = run_pipeline(cfg)
    assert stats["ai_blocked"] and len(p1.calls) == 8
    db.close()
    calls = list(p1.calls)
    for _ in range(10):
        db, _, stats, p = run_pipeline(cfg)
        calls += p.calls
        if not stats["ai_blocked"]:
            break
        db.close()
    assert db.q1("SELECT COUNT(*) c FROM sem_docs WHERE mode='ai' AND status='done'")["c"] == 2
    # 同じ場面の局所理解を2回以上依頼していない
    labels = Counter(c.source_label for c in calls if c.task == "scene_analysis")
    assert labels and max(labels.values()) == 1


@needs_ffmpeg
def test_failed_scene_is_reported_not_hidden(lecture_src, cfg_factory):
    class FailScene2(MockProvider):
        def analyze(self, req):
            if req.task == "scene_analysis" and "00:00:20–00:00:40" in req.source_label:
                from structlab.ai.base import AIError
                self.calls.append(req)
                raise AIError("mock: この区間は解析できない", retryable=False)
            return super().analyze(req)

    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True}, limits={"max_attempts": 1})
    db, _, _, _ = run_pipeline(cfg, provider=FailScene2())
    cov = json.loads((cfg.results_dir / "semantic" / "coverage.json").read_text(encoding="utf-8"))
    v = next(c for c in cov if c["kind"] == "video")
    m = v["metrics"]["第1回 局所理解"]
    assert m["done"] == 30.0 and m["total"] == 50.0 and m["missing"] == ["00:00:20–00:00:40"] and m["failed_units"] == 1
    fail = (cfg.results_dir / "semantic" / "failures.md").read_text(encoding="utf-8")
    assert "scene1" in fail and "解析できない" in fail
    g = json.loads(db.q1("SELECT data_json FROM sem_understanding WHERE scope='document' AND doc_id=(SELECT d.id FROM sem_docs d "
                         "JOIN files f ON f.id=d.file_id WHERE f.kind='video' AND d.mode='ai') ORDER BY version DESC")["data_json"])
    assert g["_failed_scenes"] == [2]


@needs_ffmpeg
def test_revision_that_flips_strength_is_not_accepted(lecture_src, cfg_factory):
    class Flipper(MockProvider):
        def analyze(self, req):
            r = super().analyze(req)
            if req.task == "directive_verify" and "直接入力" in req.text:
                r.data = {"verdict": "revised", "reason": "x", "evidence_quote": "セルの値を直接入力してはいけません。",
                          "revised": {"content": "セルの値は直接入力した方がいい", "directive_class": "recommended",
                                      "base_strength": None, "condition": None, "t_start": 36, "t_end": 36,
                                      "certainty": "high"}, "additional_directives": []}
            return r

    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True})
    db, _, _, _ = run_pipeline(cfg, provider=Flipper())
    d = next(d for d in _dirs(db, "ai") if "直接入力" in d["content"] and d["extracted_by"].startswith("ai:"))
    assert d["verification"] == "class_mismatch_unresolved" and d["directive_class"] == "recommended"  # 書き換えず要確認
    assert "セルの値は直接入力した方がいい" not in d["content"]
    assert "直接入力" in (cfg.results_dir / "semantic" / "unconfirmed.md").read_text(encoding="utf-8")


@needs_ffmpeg
def test_replace_unit_analysis_avoids_double_ai(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True, "replace_unit_analysis": True})
    db, _, _, prov = run_pipeline(cfg)
    tasks = Counter(c.task for c in prov.calls)
    assert tasks["video_segment"] == 0 and tasks["pdf_page"] == 0 and tasks["scene_analysis"] == 4
    assert db.q1("SELECT COUNT(*) c FROM units WHERE unit_type IN ('video_segment','pdf_page') AND ai_status!='done'")["c"] == 0


@needs_ffmpeg
def test_long_document_synthesis_is_split_not_truncated(lecture_src, cfg_factory):
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True, "max_synthesis_chars": 1500})
    _, _, _, prov = run_pipeline(cfg)
    assert [c.task for c in prov.calls].count("global_synthesis_part") >= 2


def test_ai_claimed_correction_without_marker_stays_unresolved(tmp_path):
    from structlab.db import DB
    from structlab.semantic.relations import link_document
    db = DB(tmp_path / "s.db")
    db.x("INSERT INTO files(id,path,path_key,kind,status) VALUES(1,'v.mp4','v.mp4','video','done')")
    db.x("INSERT INTO sem_docs(id,file_id,file_hash,version,mode) VALUES(1,1,'h','v','ai')")
    for i, (t, content, disc) in enumerate([(10, "応力度は平均値で計算すること。", "[]"),
                                             (40, "応力度は中央値で計算すること。", '["correction"]')], 1):
        db.x("INSERT INTO directives(id,doc_id,file_id,directive_class,content,source_path,t_start,t_end,speech,origin,"
             "extracted_by,discourse) VALUES(?,1,1,'mandatory',?,'v.mp4',?,?,?,'explicit','ai:x',?)", (i, content, t, t, content, disc))
    link_document(db, 1)
    l = db.q1("SELECT * FROM directive_links WHERE relation='correction_of'")
    assert l["status"] == "unresolved"
    assert db.q1("SELECT effective FROM directives WHERE id=1")["effective"] != "superseded"


def test_conflict_without_correction_is_recorded_not_resolved(tmp_path):
    from structlab.db import DB
    from structlab.semantic.relations import link_document
    db = DB(tmp_path / "s.db")
    db.x("INSERT INTO files(id,path,path_key,kind,status) VALUES(1,'v.mp4','v.mp4','video','done')")
    db.x("INSERT INTO sem_docs(id,file_id,file_hash,version,mode) VALUES(1,1,'h','v','local')")
    for i, (t, c, cls) in enumerate([(10, "グラフに凡例を入れてください。", "request"), (40, "グラフに凡例を入れないでください。", "prohibited")], 1):
        db.x("INSERT INTO directives(id,doc_id,file_id,directive_class,content,source_path,t_start,origin,extracted_by)"
             " VALUES(?,1,1,?,?,'v.mp4',?,'explicit','rule')", (i, cls, c, t))
    link_document(db, 1)
    assert {r["effective"] for r in db.q("SELECT effective FROM directives")} == {"unresolved_conflict"}
    assert db.q1("SELECT status FROM directive_links WHERE relation='conflicts_with'")["status"] == "unresolved"


# ======================= 見積り・検索 =======================
@needs_ffmpeg
def test_estimate_before_paid_run_calls_no_api(lecture_src, cfg_factory, tmp_path, capsys):
    from structlab.cli import main
    script = [{"start": a, "end": a + 2.5, "text": b} for a, b in LECTURE]
    p = tmp_path / "c.yaml"
    import yaml
    p.write_text(yaml.safe_dump({"source": {"type": "local", "path": str(lecture_src)},
                                 "output": {"work_dir": str(tmp_path / "work")},
                                 "stability": {"min_age_seconds": 0, "min_stable_seconds": 0},
                                 "video": {"segment_seconds": 20}, "ocr": {"backend": "none"},
                                 "transcription": {"backend": "mock", "mock_script": script},
                                 "ai": {"provider": "anthropic", "allow_external": False},
                                 "semantic": {"ai": True}}, allow_unicode=True), encoding="utf-8")
    assert main(["-c", str(p), "run"]) == 0  # 許可なし → 外部送信しない
    from structlab.db import DB
    db = DB(tmp_path / "work/state/state.db")
    assert db.q1("SELECT COUNT(*) c FROM api_usage")["c"] == 0
    capsys.readouterr()
    assert main(["-c", str(p), "estimate", "--json"]) == 0
    e = json.loads(capsys.readouterr().out)
    t = e["total"]
    assert t["calls_min"] > 0 and t["calls_max"] >= t["calls_min"] and t["usd_max"] >= t["usd_min"] > 0
    assert db.q1("SELECT COUNT(*) c FROM api_usage")["c"] == 0
    assert main(["-c", str(p), "search", "中央値"]) == 0
    assert "応力度は中央値" in capsys.readouterr().out
    did = db.q1("SELECT id FROM directives WHERE content LIKE '%学籍番号%'")["id"]
    assert main(["-c", str(p), "show-directive", str(did)]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["time_start"] == 33 and rec["source"].endswith(".mp4")


@needs_ffmpeg
def test_schemas_are_valid_and_mock_outputs_conform(lecture_src, cfg_factory):
    """出力スキーマ自体の妥当性と、各タスクの応答がスキーマに沿うこと（実APIでの受理は未検証）。"""
    jsonschema = pytest.importorskip("jsonschema")
    from structlab.ai.mock_semantic import TASKS
    from structlab.semantic import prompts as P

    schemas = {"scene_analysis": P.SCENE_SCHEMA, "global_synthesis": P.GLOBAL_SCHEMA, "global_synthesis_part": P.GLOBAL_SCHEMA,
               "scene_reinterpret": P.REINTERPRET_SCHEMA, "directive_extraction": P.DIRECTIVE_SCHEMA,
               "directive_verify": P.VERIFY_SCHEMA, "corpus_integration": P.CORPUS_SCHEMA}

    def strict(s):  # 構造化出力の前提: すべてのプロパティが required・追加プロパティ禁止
        if isinstance(s, dict):
            if s.get("type") == "object":
                assert set(s["required"]) == set(s["properties"]) and s["additionalProperties"] is False
            for v in s.values():
                strict(v)
        elif isinstance(s, list):
            for v in s:
                strict(v)

    for s in set(map(id, schemas.values())):
        sch = next(x for x in schemas.values() if id(x) == s)
        jsonschema.Draft202012Validator.check_schema(sch)
        strict(sch)
    cfg = _cfg(cfg_factory, lecture_src, semantic={"ai": True})
    _, _, _, prov = run_pipeline(cfg)
    seen = set()
    for c in prov.calls:
        if c.schema is not None:
            jsonschema.validate(TASKS[c.task](c.text), schemas[c.task])
            seen.add(c.task)
    assert {"scene_analysis", "global_synthesis", "scene_reinterpret", "directive_extraction", "directive_verify",
            "corpus_integration"} <= seen
