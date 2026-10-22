"""RC 梁の破壊実験（2026 構造実験報告書の書式）の入力の読み込み・検査と理論値の計算。

式と設計値は報告書の書式 PDF（2026構造実験報告書.pdf）の Ⅳ 項（p3〜p7）による。値を推測で補わない:
入力に無い値・資料と合わない値は issues に「不足」「不明」「矛盾」として残し、成功扱いにしない。
このモジュールは提出物を作らない（作るのは excel.py / report.py。利用者の許可の後だけ）。
"""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

#: 書式 PDF 表1（設計値）。単位: mm, N/mm², mm², mm⁴, mm³
DESIGN = {
    "D": 160.0, "d": 125.0, "j": 109.0, "b": 120.0, "l": 700.0, "a": 350.0,
    "ag": 796.0, "sum_at": 398.0, "pt": 2.65, "sigma_y": 380.0,
    "x": 100.0, "sum_aw": 143.0, "pw": 1.19, "sigma_wy": 325.0,
    "Fc": 21.0, "gamma": 23.0, "n": 10.0, "ku": 1.0, "kc": 1.0,
}
#: 書式 PDF に印刷された設計値の計算結果（式の実装の確認に使う。単位: N, N/mm, kN/mm²）
PRINTED = {"Ec": 21.7, "I": 4.10e7, "Z": 5.12e5, "QBC": 3.75e3, "QSC": 17.3e3, "Qy": 48.6e3, "Qmin": 38.1e3,
           "Qmean": 42.2e3, "KE": 1.25e5, "alpha_y": 0.349, "Ky": 4.36e4, "mu": -2.16, "kp": 1.03, "M_Qd": 2.8}
#: CSV の列（readme.txt の対応）
COLUMNS = {"Load kN": "P", "Disp-1 mm": "d1", "Disp-2 mm": "d2", "Disp-3 mm": "d3",
           "Strain-1 μ": "e_main1", "Strain-2 μ": "e_st2", "Strain-3 μ": "e_st3", "Strain-4 μ": "e_st4"}
EVENTS = {"曲げひび割れ": "BC", "せん断ひび割れ": "SC", "曲げ降伏": "y", "最大荷重": "max"}


def theory(p: dict) -> dict:
    """Ⅳ 項の式。p は DESIGN と同じキー（実測の場合は Fc・sigma_y・sigma_wy を実測値に置き換えたもの）。単位は N・mm。"""
    D, d, j, b, l, a = p["D"], p["d"], p["j"], p["b"], p["l"], p["a"]
    Fc = p["Fc"]
    I = b * D ** 3 / 12
    Z = b * D ** 2 / 6
    Ec = 3.35e4 * (p["gamma"] / 24) ** 2 * (Fc / 60) ** (1 / 3)          # N/mm²
    M_Qd = a / d
    kp = 0.82 * p["pt"] ** 0.23
    pw = p["pw"] / 100
    QBC = 0.56 * math.sqrt(Fc) * Z / a
    QSC = 0.085 * p["kc"] * (49 + Fc) / (M_Qd + 1.7) * b * j
    Qy = 0.9 * p["sum_at"] * p["sigma_y"] * d / a
    shear_s = 0.845 * math.sqrt(pw * p["sigma_wy"])
    Qmin = (0.092 * p["ku"] * kp * (17.6 + Fc) / (M_Qd + 0.12) + shear_s) * b * j
    Qmean = (0.115 * p["ku"] * kp * (17.6 + Fc) / (M_Qd + 0.12) + shear_s) * b * j
    KE = 48 * Ec * I / l ** 3
    alpha_y = (0.043 + 1.64 * p["n"] * p["pt"] / 100 + 0.043 * a / D) * (d / D) ** 2
    Ky = alpha_y * KE
    mu = 10 * (Qmin / Qy - 1)
    # 図2 の三折れ線（荷重 P = 2Q、変位 δ）: 原点 → せん断ひび割れ（弾性剛性）→ 曲げ降伏（降伏時剛性）、
    # せん断強度 2Qmin が曲げ降伏より小さければ 2Qmin で頭打ち（書式 p9 の図）
    P_sc, P_y, P_min = 2 * QSC, 2 * Qy, 2 * Qmin
    d_sc, d_y = P_sc / KE, P_y / Ky
    tri = [(0.0, 0.0), (d_sc, P_sc)]
    if P_min < P_y:
        tri.append((d_sc + (P_min - P_sc) / (P_y - P_sc) * (d_y - d_sc), P_min))
    else:
        tri.append((d_y, P_y))
    return {"I": I, "Z": Z, "Ec": Ec / 1000, "M_Qd": M_Qd, "kp": kp, "QBC": QBC, "QSC": QSC, "Qy": Qy, "Qmin": Qmin,
            "Qmean": Qmean, "KE": KE, "alpha_y": alpha_y, "Ky": Ky, "mu": mu, "delta_coef": 1 / KE,
            "trilinear_mm_N": tri, "failure": "せん断破壊のおそれ（μ<1）" if mu < 1 else "曲げ破壊（μ≥1）"}


def measured_params(mat: dict) -> dict:
    """表2: 実測の材料強度で置き換える（コンクリート σB → Fc、主筋 σy → σy、あばら筋 σy → σwy）。
    ヤング係数比 n は書式に 10 とあり、実測で変えるかの記載が無い → 10 のまま（issues に「要確認」を残す）。"""
    p = dict(DESIGN)
    p.update(Fc=mat["sigma_B"], sigma_y=mat["main_sigma_y"], sigma_wy=mat["stirrup_sigma_y"])
    return p


@dataclass
class Inputs:
    rows: list[dict] = field(default_factory=list)
    materials: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    mapping: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)   # {"kind": 不足|不明|矛盾|要確認, "what": ...}


def _text(p: Path) -> str:
    b = p.read_bytes()
    for enc in ("utf-8", "cp932"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{p.name}: 文字コードを判別できない")


def _find(folder: Path, *pats: str) -> Path | None:
    for pat in pats:
        hit = sorted(folder.glob(pat))
        if hit:
            return hit[0]
    return None


def load_inputs(folder: Path) -> Inputs:
    folder = Path(folder)
    inp = Inputs()
    issue = lambda kind, what: inp.issues.append({"kind": kind, "what": what})  # noqa: E731
    csvp = _find(folder, "*実験結果*.csv", "*.csv")
    if not csvp:
        issue("不足", "測定データの CSV が無い")
    else:
        lines = _text(csvp).splitlines()
        head_i = next((i for i, x in enumerate(lines) if "Load" in x and "Disp-1" in x), None)
        if head_i is None:
            issue("不足", f"{csvp.name}: 見出しの行（Load, Disp-1…）が無い")
        else:
            rd = csv.reader(io.StringIO("\n".join(lines[head_i:])))
            head = [h.strip() for h in next(rd)]
            idx = {COLUMNS[h]: i for i, h in enumerate(head) if h in COLUMNS}
            for k in set(COLUMNS.values()) - set(idx):
                issue("不足", f"{csvp.name}: 列 {k} が無い")
            bad = 0
            for r in rd:
                if not r or not any(x.strip() for x in r):
                    continue
                try:
                    inp.rows.append({k: float(r[i]) for k, i in idx.items()} | {"t": r[1].strip() if len(r) > 1 else ""})
                except (ValueError, IndexError):
                    bad += 1
            if bad:
                issue("矛盾", f"{csvp.name}: 数値でない行 {bad} 件（読み飛ばした）")
    matp = _find(folder, "*材料*.txt")
    if not matp:
        issue("不足", "材料強度（実測値）のファイルが無い")
    else:
        t = _text(matp)
        m = re.search(r"主筋.*?σy\s*=\s*([\d.]+).*?σm\s*=\s*([\d.]+)", t, re.S)
        s = re.search(r"あばら筋.*?σy\s*=\s*([\d.]+).*?σm\s*=\s*([\d.]+)", t, re.S)
        c = re.search(r"σB\s*=\s*([\d.]+)", t)
        if m:
            inp.materials.update(main_sigma_y=float(m.group(1)), main_sigma_m=float(m.group(2)))
        if s:
            inp.materials.update(stirrup_sigma_y=float(s.group(1)), stirrup_sigma_m=float(s.group(2)))
        if c:
            inp.materials["sigma_B"] = float(c.group(1))
        for k in ("main_sigma_y", "stirrup_sigma_y", "sigma_B"):
            if k not in inp.materials:
                issue("不足", f"{matp.name}: {k} を読めない")
    datp = _find(folder, "データ*.txt")
    if not datp:
        issue("不足", "各荷重のデータ（データ.txt）が無い")
    else:
        t = _text(datp)
        for jp, key in EVENTS.items():
            m = re.search(jp + r"[^\n]*\n(?:\s*荷重\s*-->\s*)?\s*([\d.]+)\s*\(kN\)(?:\s*\n\s*変位\s*-->\s*([\d.]+))?", t)
            if m:
                inp.events[key] = {"P_kN": float(m.group(1)), "delta_mm": float(m.group(2)) if m.group(2) else None}
            else:
                issue("不足", f"{datp.name}: {jp} の荷重を読めない")
    rp = _find(folder, "readme*.txt")
    if rp:
        t = _text(rp)
        for h, k in COLUMNS.items():
            col = h.split()[0]
            m = re.search(re.escape(col) + r"\([^)]*\)\s*-->\s*(\S+)", t)
            if m:
                inp.mapping[k] = m.group(1)
        if "材料強度.txt" in t and matp and matp.name != "材料強度.txt":
            issue("要確認", f"readme は「材料強度.txt」を指すが、実際のファイル名は「{matp.name}」（内容は材料強度の実測値で一致）")
    else:
        issue("不足", "readme（列の対応）が無い")
    return inp


def corrected(r: dict) -> float:
    """補正たわみ = 変形 No.1 − (No.2 + No.3)/2（講義の指示・書式の考察(1)・データ.txt の値で確認）"""
    return r["d1"] - (r["d2"] + r["d3"]) / 2


def check(inp: Inputs) -> dict:
    """入力の検査（不足・不明・矛盾を issues に加える）。数値の要約を返す。"""
    issue = lambda kind, what: inp.issues.append({"kind": kind, "what": what})  # noqa: E731
    out: dict = {"rows": len(inp.rows)}
    if not inp.rows:
        return out
    P = [r["P"] for r in inp.rows]
    imax = max(range(len(P)), key=P.__getitem__)
    out.update(P_max=P[imax], P_max_row=imax + 1, P_max_time=inp.rows[imax]["t"],
               ranges={k: (min(r[k] for r in inp.rows), max(r[k] for r in inp.rows)) for k in COLUMNS.values()})
    ev = inp.events
    if "max" in ev and abs(ev["max"]["P_kN"] - P[imax]) > 0.005:
        issue("矛盾", f"最大荷重: データ.txt {ev['max']['P_kN']} kN と CSV の最大 {P[imax]} kN が違う")
    out["events"] = {}
    for key, e in ev.items():
        if e.get("delta_mm") is None:
            continue
        rows = [i for i, x in enumerate(P) if abs(x - e["P_kN"]) < 0.005]
        cand = [(i + 1, round(corrected(inp.rows[i]), 4)) for i in rows]
        out["events"][key] = {"P_kN": e["P_kN"], "delta_txt": e["delta_mm"], "csv_rows_corrected": cand}
        if not cand:
            issue("矛盾", f"{key}: データ.txt の荷重 {e['P_kN']} kN が CSV に無い")
        elif not (hit := [i for i, c in cand if _round_half_up(c, _decimals(e["delta_mm"])) == e["delta_mm"]]):
            issue("不明", f"{key}: データ.txt の変位 {e['delta_mm']} mm が、同じ荷重の行の補正たわみ "
                          f"{[c for _, c in cand]} と合わない（どの値を使うか要確認）")
        else:
            out["events"][key]["matching_rows"] = hit   # データ.txt の変位は、この行の補正たわみを四捨五入した値
    return out


def _decimals(x: float) -> int:
    s = repr(float(x))
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def _round_half_up(x: float, n: int) -> float:
    from decimal import ROUND_HALF_UP, Decimal
    return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP))
