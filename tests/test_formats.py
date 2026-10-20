"""PDF・画像・Excel・テキスト・Office の抽出内容。"""
from __future__ import annotations

import json
import zipfile

import pytest

import dummy_data
from conftest import run_pipeline
from structlab.processors.excel import workbook_structure


def _extract(db, path, unit_type=None):
    q = "SELECT u.* FROM units u JOIN files f ON f.id=u.file_id WHERE f.path=?"
    args = [path]
    if unit_type:
        q += " AND u.unit_type=?"
        args.append(unit_type)
    return [json.loads(r["extract_json"]) for r in db.q(q + " ORDER BY seq", args)]


def test_pdf_text_tables_formulas_figures(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir, ocr={"backend": "auto"})
    db, _, _, prov = run_pipeline(cfg)
    pages = _extract(db, "第1回/実験指導書.pdf")
    assert len(pages) == 3
    p2 = pages[1]["data"]
    assert any("σ = P / A" in f for f in p2["formula_candidates"])
    assert p2["n_tables"] >= 1 and "荷重P(kN)" in p2["tables"][0]
    assert p2["n_drawings"] > 20
    p3 = pages[2]
    assert any("スキャン" in r for r in p3["needs_ai"])
    # AI にはページ画像が渡される（図・表を取りこぼさないため）
    pdf_calls = [c for c in prov.calls if c.task == "pdf_page" and c.source_label.startswith("第1回/実験指導書.pdf")]
    assert len(pdf_calls) == 3 and all(len(c.images) == 1 for c in pdf_calls)


def test_excel_cells_formulas_charts(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir)
    db, _, _, _ = run_pipeline(cfg)
    st = _extract(db, "第1回/測定データ.xlsx", "excel_workbook")[0]["data"]
    assert [s["name"] for s in st["sheets"]] == ["データ", "説明"]
    ch = st["charts"][0]
    assert ch["sheet"] == "データ" and "散布図" in ch["types"] and ch["title"] == "荷重-変位曲線"
    assert {a["title"] for a in ch["axes"]} == {"変位(mm)", "荷重(kN)"}
    assert ch["series"][0]["trendline"] == "linear" and ch["series"][0]["trendline_show_equation"]
    assert "'データ'!$B$2:$B$31" == ch["series"][0]["xVal"]
    sheet = _extract(db, "第1回/測定データ.xlsx", "excel_sheet")[0]["data"]
    groups = {g["pattern"]: g for g in sheet["formula_groups"]}
    assert groups["=A#*1000/$D#"]["count"] == 30
    assert any(g["example"].startswith("=AVERAGE(") for g in sheet["formula_groups"])


def test_excel_macro_flag(tmp_path):
    x = dummy_data.make_excel(tmp_path / "m.xlsx")
    xm = tmp_path / "m.xlsm"
    with zipfile.ZipFile(x) as zi, zipfile.ZipFile(xm, "w") as zo:
        for i in zi.infolist():
            zo.writestr(i, zi.read(i))
        zo.writestr("xl/vbaProject.bin", b"dummy")
    assert workbook_structure(xm)["has_macro"] is True


def test_image_ocr_and_ai_image(src_dir, cfg_factory):
    cfg = cfg_factory(src_dir, ocr={"backend": "auto"})
    db, _, _, prov = run_pipeline(cfg)
    ex = _extract(db, "第1回/板書.png")[0]
    assert ex["data"]["width"] == 1280
    calls = [c for c in prov.calls if c.task == "image" and c.source_label.startswith("第1回/板書.png")]
    assert len(calls) == 1 and calls[0].images


def test_heic_photo(tmp_path, cfg_factory):
    pytest.importorskip("pillow_heif")
    from PIL import Image
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (400, 300), "white").save(src / "IMG_0001.HEIC")
    cfg = cfg_factory(src)
    db, _, _, prov = run_pipeline(cfg)
    assert db.q1("SELECT status FROM files")["status"] == "done"
    assert prov.calls[0].images


def test_legacy_and_unknown_formats_recorded(tmp_path, cfg_factory):
    src = tmp_path / "src"
    src.mkdir()
    (src / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0legacy")
    (src / "data.bin").write_bytes(b"\0\1")
    cfg = cfg_factory(src)
    db, _, _, _ = run_pipeline(cfg)
    rows = {r["path"]: r for r in db.q("SELECT * FROM files")}
    assert rows["old.xls"]["status"] == "unsupported" and ".xlsx" in rows["old.xls"]["status_detail"]
    assert rows["data.bin"]["status"] == "unsupported"


def test_text_cp932(tmp_path, cfg_factory):
    src = tmp_path / "src"
    src.mkdir()
    (src / "メモ.txt").write_bytes("提出期限は11月20日です。\n".encode("cp932"))
    cfg = cfg_factory(src)
    db, _, _, _ = run_pipeline(cfg)
    assert db.q1("SELECT COUNT(*) c FROM items WHERE category='submission_requirements'")["c"] >= 1


def test_pptx_minimal(tmp_path, cfg_factory):
    src = tmp_path / "src"
    src.mkdir()
    slide = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody>'
             '<a:p><a:r><a:t>実験の目的：降伏点を求める</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    with zipfile.ZipFile(src / "講義.pptx", "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide)
        z.writestr("ppt/slides/slide2.xml", slide.replace("降伏点", "ヤング係数"))
    cfg = cfg_factory(src)
    db, _, _, _ = run_pipeline(cfg)
    assert db.q1("SELECT COUNT(*) c FROM units WHERE unit_type='office_part'")["c"] == 2
    assert db.q1("SELECT COUNT(*) c FROM items WHERE category='purpose_overview'")["c"] == 2
