"""レポートの組み立て（RC 梁の破壊実験）: 図（matplotlib）・表（計算値）・本文（content.json）→ docx → PDF（LibreOffice）。

本文（考察など）はこのモジュールでは書かない。本番では、G5 の解析で得た講義の指示と計算結果をもとに Claude Code が
content.json を書き、このモジュールが書式どおりに組み立てて検査する。必須（◎）の項目が欠けている・「未記入」の印が残る・
表紙の欄が空のときは issues に残し、完成扱いにしない。
講義の指示: 図の題は図の下（「グラフとか図のタイトルは下って決まってる」）、散布図、凡例。表の題は書式 p9 の例どおり表の上。
"""
from __future__ import annotations

import json
from pathlib import Path

from .rc_beam import DESIGN, Inputs, corrected, measured_params, theory

#: 書式 p7（Ⅴ 考察）の必須（◎）の項目。content.json の discussion[番号][キー] に本文を書く
MANDATORY = {
    "1": {"correction": "たわみ測定値の補正方法（なぜ補正が必要か・採用する補正方法）",
          "relation": "補正したたわみ測定値と理論値の関係性（理論値はどうあるべきか）",
          "failure_mode": "梁の破壊モード（理論値のグラフとの比較から言える範囲で）"},
    "2": {"relation": "各種実強度と理論値の関係性（両者の比の大小に着目。理論値はどうあるべきか）",
          "failure_mode": "梁の破壊モード（最大耐力と矛盾しないように）"},
    "3": {"relation": "各種計算値と理論値の関係性（計算式に対する材料強度の影響度合いに着目）"},
    "4": {"strain_graph": "ひずみ度の測定結果のグラフと梁の破壊状況（グラフの傾きに着目）",
          "failure_mode": "梁の破壊モード（ひずみ度のグラフと矛盾しないように）"},
}
COVER_FIELDS = ("実験日時", "班", "学生番号", "氏名", "講義日", "実験日", "提出日")
PLACEHOLDER = "【未記入】"


def _font():
    import matplotlib
    from matplotlib import font_manager
    for name in ("Noto Sans CJK JP", "IPAexGothic", "IPAGothic", "WenQuanYi Zen Hei", "Unifont-JP"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = name
            return name
    return None


def figures(inp: Inputs, outdir: Path) -> dict[str, dict]:
    """図を PNG で書く。題は図の下に置くため画像には入れず、キャプションとして返す。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _font()
    outdir.mkdir(parents=True, exist_ok=True)
    c = theory(measured_params(inp.materials))
    f = theory(DESIGN)
    a, b, j = DESIGN["a"], DESIGN["b"], DESIGN["j"]
    P = [r["P"] for r in inp.rows]
    dl = [corrected(r) for r in inp.rows]
    out = {}

    fig, ax = plt.subplots(figsize=(7, 5), dpi=200)
    ax.scatter(dl, P, s=4, label="実験値（補正たわみ）")
    for t, style, name in ((c, "-", "理論値 CQ（三折れ線）"), (f, "--", "計算値 FQ（三折れ線）")):
        xs, ys = zip(*t["trilinear_mm_N"])
        ax.plot(xs, [y / 1000 for y in ys], style, label=name)
    for key, mk, name in (("BC", "^", "曲げひび割れ"), ("SC", "s", "せん断ひび割れ"), ("y", "D", "曲げ降伏")):
        e = inp.events.get(key)
        if e and e.get("delta_mm") is not None:
            ax.scatter([e["delta_mm"]], [e["P_kN"]], marker=mk, s=40, label=f"{name}（データ.txt）", zorder=5)
    ax.set_xlabel("補正たわみ δ（mm）")
    ax.set_ylabel("荷重 P（kN）")
    sx = ax.secondary_xaxis("top", functions=(lambda x: x / a, lambda r: r * a))
    sx.set_xlabel("部材角 R（rad）")
    sy = ax.secondary_yaxis("right", functions=(lambda p: p * 1000 / (2 * b * j), lambda t: t * 2 * b * j / 1000))
    sy.set_ylabel("せん断応力度 τ（N/mm²）")
    ax.legend(fontsize=7, loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / "fig_load_deflection.png")
    plt.close(fig)
    out["load_deflection"] = {"path": outdir / "fig_load_deflection.png",
                              "caption": "荷重 P と補正たわみ δ の関係（実験値と理論値・計算値の三折れ線）"}

    for key, cols, cap in (("strain_main", (("e_main1", "主筋 No.1"),), "荷重 P と主筋のひずみ度の関係"),
                           ("strain_stirrup", (("e_st2", "あばら筋 No.2"), ("e_st3", "あばら筋 No.3"), ("e_st4", "あばら筋 No.4")),
                            "荷重 P とあばら筋のひずみ度の関係")):
        fig, ax = plt.subplots(figsize=(7, 4.5), dpi=200)
        for col, name in cols:
            ax.scatter([r[col] for r in inp.rows], P, s=4, label=name)
        ax.set_xlabel("ひずみ度（μ）")
        ax.set_ylabel("荷重 P（kN）")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(outdir / f"fig_{key}.png")
        plt.close(fig)
        out[key] = {"path": outdir / f"fig_{key}.png", "caption": cap}
    return out


def tables(inp: Inputs) -> dict[str, dict]:
    c, f = theory(measured_params(inp.materials)), theory(DESIGN)
    m, ev = inp.materials, inp.events
    t2 = {"title": "コンクリートおよび鋼の試験結果", "head": ["材料", "項目", "使用材料の実強度 [N/mm²]", "設計時強度 [N/mm²]"],
          "rows": [["コンクリート", "圧縮強度 σB", f"{m['sigma_B']:.1f}", "21"], ["主筋", "降伏点 σy", f"{m['main_sigma_y']:.1f}", "380"],
                   ["主筋", "引張強さ σm", f"{m.get('main_sigma_m', float('nan')):.1f}", "—"],
                   ["あばら筋", "降伏点 σwy", f"{m['stirrup_sigma_y']:.1f}", "325"],
                   ["あばら筋", "引張強さ σwm", f"{m.get('stirrup_sigma_m', float('nan')):.1f}", "—"]]}
    rows3 = []
    for label, key, sym in (("曲げひび割れ発生時せん断力 QBC", "BC", "QBC"), ("せん断ひび割れ発生時せん断力 QSC", "SC", "QSC"),
                            ("曲げ降伏時耐力 Qy", "y", "Qy")):
        tq = ev[key]["P_kN"] / 2 if key in ev else None
        rows3.append([label, f"{tq:.2f}" if tq is not None else PLACEHOLDER, f"{c[sym] / 1000:.2f}",
                      f"{tq / (c[sym] / 1000):.2f}" if tq is not None else PLACEHOLDER])
    t3 = {"title": "実強度（実験結果）と理論値の比較", "head": ["", "実強度 TQ [kN]", "理論値 CQ [kN]", "TQ/CQ"], "rows": rows3,
          "note": "最大耐力の行は、比べる理論値（書式に指定なし）を決めて content.json の table3_max で指定する"}
    t4 = {"title": "計算値と理論値の比較", "head": ["", "設計強度による計算値 FQ [kN]", "実材料強度による理論値 CQ [kN]", "FQ/CQ"],
          "rows": [[label, f"{f[s] / 1000:.2f}", f"{c[s] / 1000:.2f}", f"{f[s] / c[s]:.2f}"]
                   for label, s in (("曲げひび割れ発生時せん断力 QBC", "QBC"), ("せん断ひび割れ発生時せん断力 QSC", "QSC"),
                                    ("曲げ降伏時耐力 Qy", "Qy"), ("せん断耐力 min 式 Qmin", "Qmin"),
                                    ("せん断耐力 mean 式 Qmean", "Qmean"))]}
    return {"表2": t2, "表3": t3, "表4": t4}


def validate(content: dict) -> list[dict]:
    issues = []
    cover = content.get("cover", {})
    for k in COVER_FIELDS:
        if not str(cover.get(k, "")).strip():
            issues.append({"kind": "不足", "what": f"表紙の「{k}」が空"})
    disc = content.get("discussion", {})
    for no, items in MANDATORY.items():
        for key, what in items.items():
            text = str(disc.get(no, {}).get(key, "")).strip()
            if not text:
                issues.append({"kind": "不足", "what": f"考察 {no}) の必須（◎）「{what}」が無い"})
    if PLACEHOLDER in json.dumps(content, ensure_ascii=False):
        issues.append({"kind": "不足", "what": f"本文に {PLACEHOLDER} が残っている"})
    if not content.get("table3_max"):
        issues.append({"kind": "要確認", "what": "表3 の最大耐力と比べる理論値（Qmin・Qmean・Qy のどれか）が決まっていない"})
    return issues


def build(inp: Inputs, content: dict, outdir: Path, *, pdf: bool = True) -> dict:
    """docx（と PDF）を書く。issues が残る場合も書くが、結果に issues を返し完成扱いにしない。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    outdir.mkdir(parents=True, exist_ok=True)
    issues = validate(content)
    figs = figures(inp, outdir / "figures")
    tabs = tables(inp)
    if content.get("table3_max") in ("Qmin", "Qmean", "Qy") and "max" in inp.events:
        c = theory(measured_params(inp.materials))
        sym = content["table3_max"]
        tq = inp.events["max"]["P_kN"] / 2
        tabs["表3"]["rows"].append([f"最大耐力（{sym} と比較）", f"{tq:.2f}", f"{c[sym] / 1000:.2f}", f"{tq / (c[sym] / 1000):.2f}"])
    for k, t in tabs.items():
        if any(PLACEHOLDER in str(v) for row in t["rows"] for v in row):
            issues.append({"kind": "不足", "what": f"{k} に入力の不足による {PLACEHOLDER} がある"})
    doc = Document()
    st = doc.styles["Normal"]
    st.font.size = Pt(10.5)
    st.font.name = "MS Mincho"
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    cover = content.get("cover", {})
    doc.add_heading(content.get("title", "2026 構造実験報告書"), level=0)
    for k in COVER_FIELDS:
        doc.add_paragraph(f"{k}：{cover.get(k, '')}")
    doc.add_page_break()
    fig_no, tab_no = [0], [0]

    def add_figure(key):
        fig_no[0] += 1
        doc.add_picture(str(figs[key]["path"]), width=Mm(150))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap = doc.add_paragraph(f"図-{fig_no[0]}　{figs[key]['caption']}")   # 図の題は図の下
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def add_table(key):
        t = tabs[key]
        tab_no[0] += 1
        cap = doc.add_paragraph(f"{key.replace('表', '表-')}　{t['title']}")  # 書式の表番号。表の題は表の上（書式の例）
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        tbl = doc.add_table(rows=1 + len(t["rows"]), cols=len(t["head"]))
        tbl.style = "Table Grid"
        for i, h in enumerate(t["head"]):
            tbl.rows[0].cells[i].text = h
        for r, row in enumerate(t["rows"], start=1):
            for i, v in enumerate(row):
                tbl.rows[r].cells[i].text = str(v)

    for s in content.get("sections", []):
        doc.add_heading(s["heading"], level=1)
        for block in s.get("blocks", []):
            if "text" in block:
                doc.add_paragraph(block["text"])
            elif "figure" in block:
                add_figure(block["figure"])
            elif "table" in block:
                add_table(block["table"])
    doc.add_heading("Ⅴ　考察", level=1)
    disc = content.get("discussion", {})
    for no, items in MANDATORY.items():
        doc.add_heading(f"{no})", level=2)
        for key in list(items) + [k for k in disc.get(no, {}) if k not in items]:
            v = disc.get(no, {}).get(key)
            if isinstance(v, dict):
                for b in v.get("blocks", []):
                    if "text" in b:
                        doc.add_paragraph(b["text"])
                    elif "figure" in b:
                        add_figure(b["figure"])
                    elif "table" in b:
                        add_table(b["table"])
            elif v:
                doc.add_paragraph(str(v))
    docx_path = outdir / "report.docx"
    doc.save(docx_path)
    res = {"docx": docx_path, "issues": issues, "figures": fig_no[0], "tables": tab_no[0]}
    if pdf:
        import shutil
        import subprocess
        import tempfile
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        with tempfile.TemporaryDirectory() as prof:
            subprocess.run([soffice, f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "pdf",
                            "--outdir", str(outdir), str(docx_path)], check=True, capture_output=True, timeout=300)
        res["pdf"] = outdir / "report.pdf"
        tpl = content.get("template_pages")   # 例: {"pdf": "work/inputs/…報告書.pdf", "pages": [1]}（表紙を書式の PDF から使う場合）
        if tpl:
            import pymupdf as fitz
            src = fitz.open(tpl["pdf"])
            body = fitz.open(res["pdf"])
            out = fitz.open()
            for n in tpl.get("pages", [1]):
                out.insert_pdf(src, from_page=n - 1, to_page=n - 1)
            out.insert_pdf(body, from_page=1 if tpl.get("replace_cover", True) else 0)
            out.save(outdir / "report_with_template.pdf")
            res["pdf"] = outdir / "report_with_template.pdf"
            issues.append({"kind": "要確認", "what": "書式の PDF の表紙の欄（学生番号・氏名・日付・出席印）への記入方法は未確定（手書き不可の指示あり）"})
    return res
