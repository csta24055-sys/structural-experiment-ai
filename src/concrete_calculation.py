"""建築実験Ⅱ（コンクリート実験）課題(1)(2) の再計算スクリプト.

課題(1): 調合管理強度 Fm と 調合強度 F
課題(2): f = 26(C/W) - 14 による調合（絶対容積法）と単位容積質量

端数処理の方針
- 内部計算はすべて丸めずに float / Decimal で保持する（最終丸め方式）。
- 回答欄の値だけを、問題の指定どおり丸める。
    * 調合強度 F       : 整数、小数点以下切り上げ（ceil）
    * 水セメント比・細骨材率 : 小数第1位（四捨五入 ROUND_HALF_UP）
    * その他           : 整数（四捨五入 ROUND_HALF_UP）
- 比較用に「途中丸め方式」（セメント絶対容積を先に整数化してから
  細骨材を求める）も計算し、最終回答が一致することをテストで確認する。

実行: python -m src.concrete_calculation  （または python src/concrete_calculation.py）
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------------------
# 問題条件（問題用紙より）
# ---------------------------------------------------------------------------
FC_DESIGN = 27.0          # 設計基準強度 Fc [N/mm2]
SERVICE_CLASS = "長期"     # 構造体の計画供用期間の級
CEMENT = "普通ポルトランドセメント"
CONSTRUCTION_DATE = (10, 1)  # 10月1日
SIGMA = 2.5               # 圧縮強度の標準偏差 [N/mm2]

# (2) 調合条件
UNIT_WATER = 170.0               # 単位水量 [kg/m3]
AIR_PERCENT = 4.5                # 空気量 [%]
COARSE_BULK_VOLUME = 0.60        # 単位粗骨材かさ容積 [m3/m3]
COARSE_SOLID_RATIO = 60.0        # 粗骨材の実積率 [%]
RHO_WATER = 1.00                 # 水の密度 [g/cm3] = [kg/L]
RHO_CEMENT = 3.15                # [g/cm3] = [kg/L]
RHO_SAND = 2.65
RHO_GRAVEL = 2.70

# 強度式 f = A (C/W) - B
FA, FB = 26.0, 14.0

# JASS 5 耐久設計基準強度 Fd [N/mm2]
DURABILITY_FD = {"短期": 18.0, "標準": 24.0, "長期": 30.0, "超長期": 36.0}

# 構造体強度補正値 28S91 の表（普通ポルトランドセメント、問題用紙の表）
# 期間は (開始月, 開始日, 終了月, 終了日, 値)
S_TABLE_NORMAL_PORTLAND = [
    ((7, 9), (9, 11), 6.0),     # 25 < θ
    ((9, 12), (12, 7), 3.0),    # 8 ≦ θ ≦ 25
    ((2, 20), (7, 8), 3.0),     # 8 ≦ θ ≦ 25
    ((12, 8), (2, 19), 6.0),    # 0 ≦ θ < 8 （年をまたぐ）
]


def _in_period(md: tuple[int, int], start: tuple[int, int], end: tuple[int, int]) -> bool:
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end  # 年をまたぐ期間


def correction_28S91(month: int, day: int) -> float:
    """打込み日から 28S91 を表引きする（普通ポルトランドセメント）。"""
    hits = [v for s, e, v in S_TABLE_NORMAL_PORTLAND if _in_period((month, day), s, e)]
    if len(hits) != 1:
        raise ValueError(f"期間が一意に定まらない: {month}/{day} -> {hits}")
    return hits[0]


def round_half_up(x: float, ndigits: int = 0) -> float:
    q = Decimal(1).scaleb(-ndigits)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def ceil_int(x: float, eps: float = 1e-9) -> int:
    """浮動小数誤差（例 37.00000000001）で切り上がりすぎないようにした ceil。"""
    return math.ceil(x - eps)


# ---------------------------------------------------------------------------
# 課題(1)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Part1:
    Fc: float
    Fd: float
    Fq: float
    S: float
    Fm: float
    F_a: float      # Fm + 1.73σ
    F_b: float      # 0.85Fm + 3σ
    F_raw: float    # max(F_a, F_b)
    F: int          # 回答（整数切り上げ）


def solve_part1(fc: float = FC_DESIGN, service_class: str | None = SERVICE_CLASS,
                date: tuple[int, int] = CONSTRUCTION_DATE, sigma: float = SIGMA) -> Part1:
    """service_class=None のときは Fq=Fc（耐久設計基準強度を考慮しない別解）。"""
    fd = DURABILITY_FD[service_class] if service_class else 0.0
    fq = max(fc, fd)
    s = correction_28S91(*date)
    assert s >= 0
    fm = fq + s
    fa = fm + 1.73 * sigma
    fb = 0.85 * fm + 3.0 * sigma
    f_raw = max(fa, fb)
    return Part1(fc, fd, fq, s, fm, fa, fb, f_raw, ceil_int(f_raw))


# ---------------------------------------------------------------------------
# 課題(2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Part2:
    f: float
    c_over_w: float
    w_over_c: float          # [-]
    C: float                 # [kg/m3]
    Vw: float                # [L/m3]
    Va: float
    Vc: float
    Vg: float
    Vs: float
    S: float                 # [kg/m3]
    G: float
    s_over_a: float          # [%]
    unit_mass: float         # [kg/m3]


def solve_part2(f: float, water: float = UNIT_WATER) -> Part2:
    cw = (f + FB) / FA
    wc = 1.0 / cw
    c = water / wc                      # = water * cw
    vw = water / RHO_WATER
    va = AIR_PERCENT / 100.0 * 1000.0
    vc = c / RHO_CEMENT
    vg = COARSE_BULK_VOLUME * COARSE_SOLID_RATIO * 1000.0 / 100.0
    vs = 1000.0 - (vw + va + vc + vg)
    s = vs * RHO_SAND
    g = vg * RHO_GRAVEL
    sa = vs / (vs + vg) * 100.0
    mass = water + c + s + g
    return Part2(f, cw, wc, c, vw, va, vc, vg, vs, s, g, sa, mass)


def answer_row(p: Part2) -> dict[str, float]:
    """回答表（最終丸め方式）。"""
    return {
        "水セメント比[%]": round_half_up(p.w_over_c * 100, 1),
        "細骨材率[%]": round_half_up(p.s_over_a, 1),
        "単位水量[kg/m3]": round_half_up(UNIT_WATER),
        "セメント[kg/m3]": round_half_up(p.C),
        "細骨材[kg/m3]": round_half_up(p.S),
        "粗骨材[kg/m3]": round_half_up(p.G),
        "絶対容積セメント[L/m3]": round_half_up(p.Vc),
        "絶対容積細骨材[L/m3]": round_half_up(p.Vs),
        "絶対容積粗骨材[L/m3]": round_half_up(p.Vg),
        "単位容積質量[kg/m3]": round_half_up(p.unit_mass),
    }


def answer_row_intermediate_rounding(f: float, water: float = UNIT_WATER) -> dict[str, float]:
    """途中丸め方式（W/C を小数第1位[%]、各単位量・容積を整数にしてから次へ進む）。"""
    wc_pct = round_half_up((1.0 / ((f + FB) / FA)) * 100, 1)
    c = round_half_up(water / (wc_pct / 100))
    vc = round_half_up(c / RHO_CEMENT)
    vg = round_half_up(COARSE_BULK_VOLUME * COARSE_SOLID_RATIO * 1000 / 100)
    va = round_half_up(AIR_PERCENT * 10)
    vs = 1000 - water - va - vc - vg
    s = round_half_up(vs * RHO_SAND)
    g = round_half_up(vg * RHO_GRAVEL)
    return {
        "水セメント比[%]": wc_pct,
        "細骨材率[%]": round_half_up(vs / (vs + vg) * 100, 1),
        "単位水量[kg/m3]": water,
        "セメント[kg/m3]": c,
        "細骨材[kg/m3]": s,
        "粗骨材[kg/m3]": g,
        "絶対容積セメント[L/m3]": vc,
        "絶対容積細骨材[L/m3]": vs,
        "絶対容積粗骨材[L/m3]": vg,
        "単位容積質量[kg/m3]": water + c + s + g,
    }


def report(service_class: str | None = SERVICE_CLASS) -> str:
    p1 = solve_part1(service_class=service_class)
    p2 = solve_part2(p1.F)
    lines = [
        f"=== 課題(1)  [Fq の扱い: {'max(Fc, Fd)' if service_class else 'Fq = Fc'}] ===",
        f"Fc = {p1.Fc}, Fd = {p1.Fd}, Fq = {p1.Fq}, 28S91 = {p1.S}",
        f"Fm = {p1.Fq} + {p1.S} = {p1.Fm}",
        f"F >= Fm + 1.73σ   = {p1.Fm} + 1.73×{SIGMA} = {p1.F_a:.4f}",
        f"F >= 0.85Fm + 3σ  = 0.85×{p1.Fm} + 3×{SIGMA} = {p1.F_b:.4f}",
        f"F = ceil({p1.F_raw:.4f}) = {p1.F}",
        "=== 課題(2) ===",
        f"C/W = ({p1.F}+14)/26 = {p2.c_over_w:.6f},  W/C = {p2.w_over_c*100:.4f} %",
        f"C = {UNIT_WATER}/{p2.w_over_c:.6f} = {p2.C:.4f} kg/m3",
        f"Vw = {p2.Vw:.4f}, Va = {p2.Va:.4f}, Vc = {p2.Vc:.4f}, Vg = {p2.Vg:.4f}, Vs = {p2.Vs:.4f} L/m3",
        f"S = {p2.S:.4f}, G = {p2.G:.4f} kg/m3,  s/a = {p2.s_over_a:.4f} %",
        f"単位容積質量 = {p2.unit_mass:.4f} kg/m3",
        "--- 回答表（最終丸め） ---",
    ]
    lines += [f"{k}: {v:g}" for k, v in answer_row(p2).items()]
    lines.append("--- 回答表（途中丸め） ---")
    lines += [f"{k}: {v:g}" for k, v in answer_row_intermediate_rounding(p1.F).items()]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(SERVICE_CLASS))
    print()
    print("##### 参考（別解: 耐久設計基準強度を考慮せず Fq = Fc = 27 とした場合） #####")
    print(report(None))
