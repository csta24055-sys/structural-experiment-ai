"""文章スタイル（最終工程のみ）と、フィードバックの記録・工程の追跡。

本人の文章スタイルPDFは個人情報を含むためリポジトリに置かない。ここでは同じ構造のダミーPDFで確認する。
実物のPDFでの読み取りは、環境変数 STRUCTLAB_STYLE_PDF を指定したときだけ確認する。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from conftest import needs_ffmpeg, run_pipeline
from structlab.ai.mock_provider import MockProvider
from structlab.cli import main
from structlab.db import DB
from structlab.style.apply import apply_style, apply_to_markdown, verify_preservation
from structlab.style.profile import build_profile, select_rules

# 実際のPDFと同じ「部・節（番号範囲）・番号付き項目」の構造だけを真似た架空の内容
DUMMY_LINES = [
    "文章の特徴メモ（テスト用の架空データ）",
    "第1部 入力の特徴 3項目",
    "① 入力のしかた 1～3",
    "1. 誤字をそのまま残すことがある", "2. 短い返事が多い", "3. 質問を一言で送る",
    "第2部 正式文の特徴 4項目",
    "1. 説明の進め方 1～2",
    "1. 手順を実施した順に並べる傾向がある。",
    "2. 面接で話す内容と揃える傾向がある。",
    "9. 就活書類向けの節 3～4",
    "3. 応募先ごとに書き分ける。", "4. 締めの一文を短くする。",
    "第4部 調整しすぎを防ぐ決まり 3項目",
    "1. 文末の決まり 1～3",
    "1. 同じ文末を機械的に並べない。", "2. 段落ごとに教訓を付け足さない。",
    "3. 元の文章にない評価の言葉を最後の文で",
    "付け足さない。",
    "96",
]


def make_style_pdf(path: Path) -> Path:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    c = canvas.Canvas(str(path))
    c.setFont("HeiseiKakuGo-W5", 10)
    y = 800
    for ln in DUMMY_LINES:
        c.drawString(40, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return path


@pytest.fixture
def style_cfg(tmp_path, cfg_factory):
    src = tmp_path / "src"
    src.mkdir()
    pdf = make_style_pdf(tmp_path / "writing_style.pdf")
    return cfg_factory(src, style={"enabled": True, "pdf_path": str(pdf), "max_rules": 50}), pdf


# ======================= PDFの読み込み =======================
def test_parse_profile_and_exclusions(style_cfg):
    cfg, pdf = style_cfg
    prof = build_profile(pdf, cfg.work_dir / "style" / "profile.json")
    items = {i["id"]: i for i in prof["items"]}
    assert prof["stats"]["parsed"] == prof["stats"]["declared_total"] == 10
    assert not items["P1-①-0001"]["applicable"] and "入力・会話" in items["P1-①-0001"]["reason"]  # 第1部は使わない
    assert not items["P2-9-0003"]["applicable"] and "就職活動" in items["P2-9-0003"]["reason"]
    assert not items["P2-1-0002"]["applicable"]  # 「面接」→ 就活向け
    assert items["P2-1-0001"]["applicable"]
    assert items["P4-1-0003"]["text"] == "元の文章にない評価の言葉を最後の文で付け足さない。"  # 折り返しを結合
    rules = select_rules(prof, 2)
    # 最終チェック（第4部）を先頭に、節ごとに順番に選ぶ。上限を超えて全部は使わない
    assert [r["id"] for r in rules] == ["P4-1-0001", "P2-1-0001"]
    assert all(r["applicable"] for r in select_rules(prof, 100)) and len(select_rules(prof, 100)) == prof["stats"]["applicable"] == 4
    assert (cfg.work_dir / "style" / "profile.json").exists()


@pytest.mark.skipif(not os.environ.get("STRUCTLAB_STYLE_PDF"), reason="実物の文章スタイルPDFは指定時のみ")
def test_real_style_pdf(tmp_path):
    prof = build_profile(Path(os.environ["STRUCTLAB_STYLE_PDF"]), tmp_path / "p.json")
    assert prof["stats"]["parsed"] == prof["stats"]["declared_total"] > 0


# ======================= 内容が変わっていないかの再検証 =======================
ORIG = ("ヤング係数は応力度とひずみ度の比で求めた。したがって、E = σ / ε であり、その値は205 GPaであった。"
        "先生の「有効数字は3桁」という指示に従った。降伏点は明確ではない可能性がある。以上より、鋼材は弾性域で線形であると考えられる。")


def test_verify_preservation():
    assert verify_preservation(ORIG, ORIG.replace("したがって、", "そのため、"))["status"] == "ok"
    assert verify_preservation(ORIG, ORIG.replace("205", "210"))["status"] == "mismatch"
    assert verify_preservation(ORIG, ORIG.replace("E = σ / ε", "E = σ × ε"))["status"] == "mismatch"
    assert verify_preservation(ORIG, ORIG.replace("「有効数字は3桁」", "「有効数字は2桁」"))["status"] == "mismatch"
    r = verify_preservation(ORIG, ORIG.replace("明確ではない", "明確である"))
    assert r["status"] == "needs_review" and any("否定" in x for x in r["needs_review"])
    r = verify_preservation(ORIG, ORIG.replace("可能性がある", "のは確実である"))
    assert any("断定" in x for x in r["needs_review"])
    r = verify_preservation(ORIG, ORIG + "この実験を通して大きく成長し、社会貢献への意欲が高まった。")
    assert r["status"] == "needs_review"
    r = verify_preservation(ORIG, ORIG.replace("鋼材は弾性域で線形であると考えられる", "試験体の破断形状は延性的と考えられる"))
    assert any("結論" in x for x in r["needs_review"])


# ======================= 適用（最終工程） =======================
def test_style_disabled_or_missing_pdf_does_not_stop(tmp_path, cfg_factory):
    src = tmp_path / "src"
    src.mkdir()
    db = DB(tmp_path / "s.db")
    off = cfg_factory(src)
    r = apply_style(db, off, ORIG, provider=MockProvider())
    assert r["status"] == "skipped" and r["final"] == ORIG
    on = cfg_factory(src, style={"enabled": True})
    r = apply_style(db, on, ORIG, provider=MockProvider())
    assert r["status"] == "skipped" and r["final"] == ORIG and "未提供" in r["verification"]["note"]
    assert db.q1("SELECT COUNT(*) c FROM style_runs WHERE original=?", (ORIG,))["c"] == 2  # 適用前は必ず保存
    assert main(["-c", str(_yaml(tmp_path, src, {"style": {"enabled": True}})), "style", "profile"]) == 0


def _yaml(tmp_path, src, extra):
    import yaml
    p = tmp_path / "c.yaml"
    d = {"source": {"type": "local", "path": str(src)}, "output": {"work_dir": str(tmp_path / "work")}}
    d.update(extra)
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    return p


def test_style_apply_and_rejects_content_changes(style_cfg):
    cfg, pdf = style_cfg
    build_profile(pdf, cfg.work_dir / "style" / "profile.json")
    db = DB(cfg.db_path)
    prov = MockProvider()
    ok = apply_style(db, cfg, ORIG, provider=prov, kind="discussion")
    assert ok["status"] == "applied" and "そのため、" in ok["final"] and ok["final"] != ORIG
    req = prov.calls[-1]
    assert req.task == "style_rewrite" and "P4-1-0001" in req.text and "P1-①" not in req.text  # 除外項目は渡さない
    assert "数値・単位・数式" in req.system
    brk = apply_style(db, cfg, "[[STYLE_BREAK]]" + ORIG, provider=prov)
    assert brk["status"] == "kept_original" and brk["final"].startswith("[[STYLE_BREAK]]") and brk["styled"] != brk["final"]
    assert any("数値" in x for x in brk["verification"]["mismatch"])
    inv = apply_style(db, cfg, ORIG + "[[STYLE_INVENT]]", provider=prov)
    assert inv["status"] == "kept_original"  # 書き足し（創作）の疑いは採用しない
    rows = db.q("SELECT * FROM style_runs ORDER BY id")
    assert all(r["original"] for r in rows) and {r["status"] for r in rows} == {"applied", "kept_original"}


def test_style_markdown_keeps_tables_and_writes_original(style_cfg, tmp_path):
    cfg, pdf = style_cfg
    build_profile(pdf, cfg.work_dir / "style" / "profile.json")
    md = "# 考察\n" + ORIG + "\n\n# 結果\n| 荷重 | 変位 |\n|---|---|\n| 10 | 0.12 |\n"
    db = DB(cfg.db_path)
    out, runs = apply_to_markdown(db, cfg, md, provider=MockProvider())
    assert "| 10 | 0.12 |" in out and "# 考察" in out and "# 結果" in out
    assert [r["status"] for r in runs if r["section"]] == ["applied", "locked"]


def test_style_is_isolated_from_analysis():
    """文章スタイルは最終工程専用: 意味理解・指示判断・計算・チェックのコードから参照しない。"""
    root = Path(__file__).resolve().parents[1] / "src" / "structlab"
    offenders = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("style/") or rel in ("cli.py", "trace.py") or rel.startswith("ai/mock"):
            continue
        if re.search(r"from \.+style|import structlab\.style|from structlab\.style", p.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == []


@needs_ffmpeg
def test_pipeline_runs_with_style_enabled_but_no_pdf(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir, style={"enabled": True, "pdf_path": "/nonexistent/style.pdf"})
    db, _, stats, _ = run_pipeline(cfg)
    assert stats["files_done"] >= 1 and db.q1("SELECT COUNT(*) c FROM style_runs")["c"] == 0


# ======================= フィードバックと追跡 =======================
@needs_ffmpeg
def test_feedback_and_trace(tmp_path, cfg_factory, capsys):
    import dummy_data
    from test_semantic import LECTURE
    src = tmp_path / "src"
    dummy_data.make_video(src / "v.mp4", tmp_path / "w")
    script = [{"start": a, "end": a + 2.5, "text": b} for a, b in LECTURE]
    p = _yaml(tmp_path, src, {"stability": {"min_age_seconds": 0, "min_stable_seconds": 0}, "video": {"segment_seconds": 20},
                              "ocr": {"backend": "none"}, "transcription": {"backend": "mock", "mock_script": script},
                              "ai": {"provider": "mock"}, "semantic": {"ai": True}})
    assert main(["-c", str(p), "run"]) == 0
    db = DB(tmp_path / "work/state/state.db")
    did = db.q1("SELECT id FROM directives WHERE content LIKE '%中央値%' AND extracted_by LIKE 'ai:%'")["id"]
    text = "中央値ではなく平均値を使うよう説明したはずです"
    assert main(["-c", str(p), "feedback", "add", text, "--target", f"directive:{did}", "--author", "教授"]) == 0
    fb = db.q1("SELECT * FROM feedback")
    assert fb["text"] == text and fb["suspected_stage"] is None and fb["status"] == "open"  # 内容を推測して分類しない
    capsys.readouterr()
    assert main(["-c", str(p), "trace", f"directive:{did}"]) == 0
    tr = json.loads(capsys.readouterr().out)
    assert set(tr) >= {"target", "extraction", "understanding", "directives", "feedback"}
    assert tr["extraction"]["timeline_near"] and tr["extraction"]["checks"]
    assert len(tr["understanding"]["scene_passes"]) >= 2  # 局所理解→再解釈の版
    assert tr["directives"]["verification"] and tr["feedback"][0]["text"] == text
    assert main(["-c", str(p), "feedback", "set", "--id", str(fb["id"]), "--stage", "understanding", "--status",
                 "investigating"]) == 0
    assert db.q1("SELECT suspected_stage FROM feedback")["suspected_stage"] == "understanding"
