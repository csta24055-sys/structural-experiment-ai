"""Excel の作成（RC 梁の破壊実験）。計算はすべてセルの数式で書き、Excel・LibreOffice で再計算できる形にする。

  設計条件     表1 の設計値と表2 の実測値（入力）→ Ⅳ 項の式（数式）。書式の印刷値との差も数式で示す
  測定データ   CSV の全行 ＋ 補正たわみ δ = No.1 −(No.2+No.3)/2、Q = P/2、R = δ/a、τ = P·10³/(2bj)（数式）
  表2〜表4     書式 p7・p9 の表（TQ = 各荷重 P/2、比は数式）
  図          散布図（凡例・軸タイトルあり）。図の題は講義の指示どおり図の下のセルに書く（グラフの上の題は付けない）
値を推測で補わない: 書式に指定の無い選択（最大耐力と比べる理論値など）は「要確認」の行として残す。
"""
from __future__ import annotations

from pathlib import Path

from .rc_beam import DESIGN, PRINTED, Inputs, theory, valid_deflection_rows

P_SHEET, D_SHEET = "設計条件", "測定データ"
Q = f"'{P_SHEET}'"

#: 設計条件のシートの入力行: (記号, 項目, 設計値のキー, 実測で置き換える材料のキー, 単位)
INPUT_ROWS = [
    ("D", "梁せい", "D", None, "mm"), ("d", "有効せい", "d", None, "mm"), ("j", "応力中心間距離", "j", None, "mm"),
    ("b", "梁幅", "b", None, "mm"), ("l", "スパン", "l", None, "mm"), ("a", "せん断スパン", "a", None, "mm"),
    ("Σat", "引張鉄筋断面積", "sum_at", None, "mm²"), ("pt", "引張鉄筋比", "pt", None, "%"),
    ("σy", "主筋の降伏点", "sigma_y", "main_sigma_y", "N/mm²"), ("Σaw", "あばら筋断面積", "sum_aw", None, "mm²"),
    ("pw", "あばら筋比", "pw", None, "%"), ("σwy", "あばら筋の降伏点", "sigma_wy", "stirrup_sigma_y", "N/mm²"),
    ("Fc", "コンクリート強度（実測は σB）", "Fc", "sigma_B", "N/mm²"), ("γ", "コンクリートの単位体積重量", "gamma", None, "kN/m³"),
    ("n", "ヤング係数比（書式の値。実測で変えるかは要確認）", "n", None, "—"), ("ku", "寸法効果の係数", "ku", None, "—"),
    ("kc", "係数", "kc", None, "—"),
]
#: 計算行: (記号, 項目, 数式（{c} は列、{r[記号]} は行番号）, 単位, 書式の印刷値のキー, 印刷値の倍率)
CALC_ROWS = [
    ("I", "断面二次モーメント", "={c}{r[b]}*{c}{r[D]}^3/12", "mm⁴", "I", 1),
    ("Z", "断面係数", "={c}{r[b]}*{c}{r[D]}^2/6", "mm³", "Z", 1),
    ("Ec", "コンクリートのヤング係数", "=3.35E4*({c}{r[γ]}/24)^2*({c}{r[Fc]}/60)^(1/3)", "N/mm²", "Ec", 1000),
    ("M/Qd", "せん断スパン比", "={c}{r[a]}/{c}{r[d]}", "—", "M_Qd", 1),
    ("kp", "引張鉄筋比の係数 0.82pt^0.23", "=0.82*{c}{r[pt]}^0.23", "—", "kp", 1),
    ("QBC", "曲げひび割れ発生時せん断力", "=0.56*SQRT({c}{r[Fc]})*{c}{r[Z]}/{c}{r[a]}", "N", "QBC", 1),
    ("QSC", "せん断ひび割れ発生時せん断力", "=0.085*{c}{r[kc]}*(49+{c}{r[Fc]})/({c}{r[M/Qd]}+1.7)*{c}{r[b]}*{c}{r[j]}", "N", "QSC", 1),
    ("Qy", "曲げ降伏時耐力", "=0.9*{c}{r[Σat]}*{c}{r[σy]}*{c}{r[d]}/{c}{r[a]}", "N", "Qy", 1),
    ("Qmin", "せん断耐力 min 式",
     "=(0.092*{c}{r[ku]}*{c}{r[kp]}*(17.6+{c}{r[Fc]})/({c}{r[M/Qd]}+0.12)+0.845*SQRT({c}{r[pw]}/100*{c}{r[σwy]}))*{c}{r[b]}*{c}{r[j]}",
     "N", "Qmin", 1),
    ("Qmean", "せん断耐力 mean 式",
     "=(0.115*{c}{r[ku]}*{c}{r[kp]}*(17.6+{c}{r[Fc]})/({c}{r[M/Qd]}+0.12)+0.845*SQRT({c}{r[pw]}/100*{c}{r[σwy]}))*{c}{r[b]}*{c}{r[j]}",
     "N", "Qmean", 1),
    ("KE", "弾性剛性 48EcI/l³", "=48*{c}{r[Ec]}*{c}{r[I]}/{c}{r[l]}^3", "N/mm", "KE", 1),
    ("αy", "剛性低下率", "=(0.043+1.64*{c}{r[n]}*{c}{r[pt]}/100+0.043*{c}{r[a]}/{c}{r[D]})*({c}{r[d]}/{c}{r[D]})^2", "—", "alpha_y", 1),
    ("Ky", "降伏時剛性 αy·KE", "={c}{r[αy]}*{c}{r[KE]}", "N/mm", "Ky", 1),
    ("μ", "靭性の指標 10(Qmin/Qy−1)", "=10*({c}{r[Qmin]}/{c}{r[Qy]}-1)", "—", "mu", 1),
]


def build_workbook(inp: Inputs, out: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.chart import Reference, ScatterChart, Series
    from openpyxl.styles import Alignment, Font

    if not inp.rows or {"main_sigma_y", "stirrup_sigma_y", "sigma_B"} - set(inp.materials):
        raise ValueError("測定データまたは材料強度の実測値が不足しています（推測で補わない）")
    wb = Workbook()
    ws = wb.active
    ws.title = P_SHEET
    ws.append(["記号", "項目", "設計値 FQ の計算（単位はE列）", "実材料強度による理論値 CQ の計算（単位はE列）", "単位",
               "書式の印刷値（単位はE列）", "設計値と印刷値の差（%）"])
    r: dict[str, int] = {}
    for sym, name, key, mkey, unit in INPUT_ROWS:
        ws.append([sym, name, DESIGN[key], inp.materials[mkey] if mkey else DESIGN[key], unit])
        r[sym] = ws.max_row
    for sym, name, f, unit, pkey, scale in CALC_ROWS:
        i = ws.max_row + 1
        r[sym] = i
        ws.append([sym, name, f.format(c="C", r=r), f.format(c="D", r=r), unit, PRINTED[pkey] * scale,
                   f"=(C{i}/F{i}-1)*100"])
    for col, w in zip("ABCDEFG", (8, 34, 22, 26, 9, 18, 16)):
        ws.column_dimensions[col].width = w

    # 測定データ
    wd = wb.create_sheet(D_SHEET)
    wd.append(["No.", "測定日時", "荷重 P（kN）", "たわみ No.1（mm）", "たわみ No.2（mm）", "たわみ No.3（mm）",
               "主筋 No.1 ひずみ（μ）", "あばら筋 No.2 ひずみ（μ）", "あばら筋 No.3 ひずみ（μ）", "あばら筋 No.4 ひずみ（μ）",
               "補正たわみ δ（mm）", "せん断力 Q（kN）", "部材角 R（rad）", "せん断応力度 τ（N/mm²）"])
    for k, row in enumerate(inp.rows, start=2):
        wd.append([k - 1, row["t"], row["P"], row["d1"], row["d2"], row["d3"], row["e_main1"], row["e_st2"], row["e_st3"],
                   row["e_st4"], f"=D{k}-(E{k}+F{k})/2", f"=C{k}/2", f"=K{k}/{Q}!$C${r['a']}",
                   f"=C{k}*1000/(2*{Q}!$C${r['b']}*{Q}!$C${r['j']})"])
    last = len(inp.rows) + 1

    # 表2〜表4
    t2 = wb.create_sheet("表2")
    t2.append(["材料", "項目", "使用材料の実強度（N/mm²）", "設計時強度（N/mm²）"])
    m = inp.materials
    for row in (["コンクリート", "圧縮強度 σB", f"={Q}!D{r['Fc']}", f"={Q}!C{r['Fc']}"],
                ["主筋", "降伏点 σy", f"={Q}!D{r['σy']}", f"={Q}!C{r['σy']}"],
                ["主筋", "引張強さ σm", m.get("main_sigma_m"), "—"],
                ["あばら筋", "降伏点 σwy", f"={Q}!D{r['σwy']}", f"={Q}!C{r['σwy']}"],
                ["あばら筋", "引張強さ σwm", m.get("stirrup_sigma_m"), "—"]):
        t2.append(row)
    t3 = wb.create_sheet("表3")
    t3.append(["項目", "実強度 TQ（kN）", "理論値 CQ（kN）", "TQ/CQ（—）", "備考"])
    ev = inp.events
    for label, ekey, sym, note in (("曲げひび割れ発生時せん断力 QBC", "BC", "QBC", ""),
                                   ("せん断ひび割れ発生時せん断力 QSC", "SC", "QSC", ""),
                                   ("曲げ降伏時耐力 Qy", "y", "Qy", ""),
                                   ("最大耐力（理論値は Qmin）", "max", "Qmin",
                                    "理論上の最大耐力は Qmin<Qy よりせん断で決まる（講義「最大耐力はどの破壊モードで決まっているのか」）"),
                                   ("（参考）最大耐力と Qmean", "max", "Qmean", "参考: せん断耐力の平均値式"),
                                   ("（参考）最大耐力と Qy", "max", "Qy", "参考: 曲げ降伏時耐力（実験は曲げ降伏が先行）")):
        i = t3.max_row + 1
        tq = ev[ekey]["P_kN"] / 2 if ekey in ev else None
        t3.append([label, f"={ev[ekey]['P_kN']}/2" if tq is not None else None, f"={Q}!D{r[sym]}/1000",
                   f"=B{i}/C{i}" if tq is not None else None, note or "TQ = 荷重 P/2（書式 p4: Q = P/2）"])
    t4 = wb.create_sheet("表4")
    t4.append(["項目", "設計強度による計算値 FQ（kN）", "実材料強度による理論値 CQ（kN）", "FQ/CQ（—）"])
    for label, sym in (("曲げひび割れ発生時せん断力 QBC", "QBC"), ("せん断ひび割れ発生時せん断力 QSC", "QSC"),
                       ("曲げ降伏時耐力 Qy", "Qy"), ("せん断耐力 min 式 Qmin", "Qmin"), ("せん断耐力 mean 式 Qmean", "Qmean")):
        i = t4.max_row + 1
        t4.append([label, f"={Q}!C{r[sym]}/1000", f"={Q}!D{r[sym]}/1000", f"=B{i}/C{i}"])
    for sh in (t2, t3, t4):
        for col, w in zip("ABCDE", (34, 22, 24, 14, 36)):
            sh.column_dimensions[col].width = w

    # 図2 の三折れ線（理論値 CQ・計算値 FQ）。値は数式で設計条件から
    tl = wb.create_sheet("理論線")
    tl.append(["線", "補正たわみ δ（mm）", "荷重 P（kN）"])
    for col, name in (("D", "理論値 CQ"), ("C", "計算値 FQ")):
        pts = [("0", "0"),
               (f"2*{Q}!{col}{r['QSC']}/{Q}!{col}{r['KE']}", f"2*{Q}!{col}{r['QSC']}/1000")]
        dsc, psc = pts[1]
        dy, py, pmin = (f"2*{Q}!{col}{r['Qy']}/{Q}!{col}{r['Ky']}", f"2*{Q}!{col}{r['Qy']}/1000",
                        f"2*{Q}!{col}{r['Qmin']}/1000")
        # せん断耐力 2Qmin が曲げ降伏 2Qy より小さければ 2Qmin で頭打ち（書式 p9）
        pts.append((f"IF({pmin}<{py},{dsc}+({pmin}-{psc})/({py}-{psc})*({dy}-{dsc}),{dy})", f"MIN({pmin},{py})"))
        for x, y in pts:
            tl.append([name, f"={x}", f"={y}"])
    # 理論値 CQ のせん断破壊点 → 曲げ降伏点（書式 p8 の図の点線。参考）
    tl.append(["理論値 CQ（せん断破壊点→曲げ降伏点、参考）", "=B4", "=C4"])
    tl.append(["理論値 CQ（せん断破壊点→曲げ降伏点、参考）", f"=2*{Q}!D{r['Qy']}/{Q}!D{r['Ky']}", f"=2*{Q}!D{r['Qy']}/1000"])
    ev_sheet = wb.create_sheet("各荷重")
    ev_sheet.append(["事象", "荷重 P（kN）", "変位（データ.txt）（mm）"])
    for jp, key in (("曲げひび割れ", "BC"), ("せん断ひび割れ", "SC"), ("曲げ降伏", "y"), ("最大荷重", "max")):
        if key in ev:
            ev_sheet.append([jp, ev[key]["P_kN"], ev[key]["delta_mm"]])

    # 図（散布図・凡例・軸タイトル。題はグラフの下のセル）
    def scatter(x_title, y_title):
        ch = ScatterChart()
        ch.title = None
        ch.x_axis.title, ch.y_axis.title = x_title, y_title
        ch.legend.position = "r"
        ch.height, ch.width = 9, 16
        ch.x_axis.delete = ch.y_axis.delete = False
        return ch

    colors = iter(["1F77B4", "000000", "FF7F0E", "7F7F7F"] * 3)
    strain_colors = iter(["1F77B4", "FF7F0E", "2CA02C", "D62728"] * 3)

    def add(ch, sheet, xcol, ycol, r0, r1, name, marker=True, color=None, width=1.5):
        # 講義（エクセルの作り方1）どおり「散布図（直線）」で描く。点だけの系列は LibreOffice で見えないことがあるため線にする
        s = Series(Reference(sheet, min_col=ycol, min_row=r0, max_row=r1),
                   Reference(sheet, min_col=xcol, min_row=r0, max_row=r1), title=name)
        s.marker.symbol = "none"
        s.smooth = False
        s.graphicalProperties.line.solidFill = color or next(colors)
        s.graphicalProperties.line.width = int(width * 12700)
        ch.series.append(s)

    figs = wb.create_sheet("図")
    c1 = scatter("補正たわみ δ（mm）", "荷重 P（kN）")
    nv = valid_deflection_rows(inp.rows)
    add(c1, wd, 11, 3, 2, nv + 1, "実験値（補正たわみ）")
    add(c1, tl, 2, 3, 2, 4, "理論値 CQ（三折れ線）", marker=False, width=2.5)
    add(c1, tl, 2, 3, 5, 7, "計算値 FQ（三折れ線）", marker=False)
    c1.series[-1].graphicalProperties.line.dashStyle = "dash"
    add(c1, tl, 2, 3, 8, 9, "理論値 CQ（せん断破壊点→曲げ降伏点、参考）", marker=False)
    c1.series[-1].graphicalProperties.line.dashStyle = "sysDot"
    c1.x_axis.scaling.min, c1.y_axis.scaling.min = 0, 0
    figs.add_chart(c1, "A1")
    figs["A20"] = "図-A　荷重 P と補正たわみ δ の関係（実験値と理論値）" + (
        "" if nv == len(inp.rows) else f"。たわみ No.2・No.3 の計測が途切れる測定データ {nv + 1} 行目より前を表示")
    c2 = scatter("ひずみ（μ）", "荷重 P（kN）")
    add(c2, wd, 7, 3, 2, last, "主筋 No.1", color=next(strain_colors))
    c2.y_axis.scaling.min = 0
    figs.add_chart(c2, "A23")
    figs["A42"] = "図-B　荷重 P と主筋のひずみの関係"
    c3 = scatter("ひずみ（μ）", "荷重 P（kN）")
    for col, name in ((8, "あばら筋 No.2"), (9, "あばら筋 No.3"), (10, "あばら筋 No.4")):
        add(c3, wd, col, 3, 2, last, name, color=next(strain_colors))
    c3.y_axis.scaling.min = 0
    figs.add_chart(c3, "A45")
    figs["A64"] = "図-C　荷重 P とあばら筋のひずみの関係"
    for cell in ("A20", "A42", "A64"):
        figs[cell].font = Font(bold=True)
        figs[cell].alignment = Alignment(horizontal="left")
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def recalc_with_libreoffice(src: Path, outdir: Path, timeout: int = 300) -> Path:
    """LibreOffice（無料）で開いて再計算し、計算結果を保存した xlsx を outdir に書く（照合に使う）。"""
    import shutil
    import subprocess
    import tempfile
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice が無い")
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as prof:
        subprocess.run([soffice, f"-env:UserInstallation=file://{prof}", "--headless", "--calc", "--convert-to",
                        "xlsx:Calc MS Excel 2007 XML", "--outdir", str(outdir), str(src)],
                       check=True, capture_output=True, timeout=timeout)
    return outdir / src.name


def expected_values(inp: Inputs) -> dict:
    """Python で独立に計算した値（Excel の数式の結果と照合する）。単位: kN"""
    from .rc_beam import measured_params
    f, c = theory(DESIGN), theory(measured_params(inp.materials))
    return {sym: (f[sym] / 1000, c[sym] / 1000) for sym in ("QBC", "QSC", "Qy", "Qmin", "Qmean")}
