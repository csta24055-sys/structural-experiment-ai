"""課題(1)(2) の検証テスト。

実装とは独立に fractions.Fraction で厳密計算した値と突き合わせる。
"""
from fractions import Fraction as Fr
import math

import pytest

from src.concrete_calculation import (
    answer_row, answer_row_intermediate_rounding, ceil_int, correction_28S91,
    round_half_up, solve_part1, solve_part2,
)


# ---------------- 構造体強度補正値の表引き ----------------
@pytest.mark.parametrize("md,expected", [
    ((10, 1), 3.0),                 # 本問
    ((9, 11), 6.0), ((9, 12), 3.0), # 境界
    ((12, 7), 3.0), ((12, 8), 6.0),
    ((2, 19), 6.0), ((2, 20), 3.0),
    ((7, 8), 3.0), ((7, 9), 6.0),
    ((1, 1), 6.0), ((8, 15), 6.0), ((5, 1), 3.0),
])
def test_correction_table(md, expected):
    assert correction_28S91(*md) == expected


def test_table_covers_every_day_exactly_once():
    days = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    for m, n in enumerate(days, start=1):
        for d in range(1, n + 1):
            correction_28S91(m, d)  # 一意でなければ ValueError


# ---------------- 課題(1) ----------------
def test_part1_primary():
    p = solve_part1()
    assert p.Fd == 30 and p.Fq == 30            # Fq = max(27, 30)
    assert p.S == 3
    assert p.Fm == pytest.approx(33.0)
    assert p.F_a == pytest.approx(37.325)
    assert p.F_b == pytest.approx(35.55)
    assert p.F_raw == pytest.approx(37.325)     # Fm+1.73σ が支配
    assert p.F == 38


def test_part1_exact_fraction():
    fq = max(Fr(27), Fr(30)); fm = fq + 3; s = Fr(5, 2)
    fa = fm + Fr(173, 100) * s; fb = Fr(85, 100) * fm + 3 * s
    assert (fa, fb) == (Fr(1493, 40), Fr(711, 20))
    assert math.ceil(max(fa, fb)) == solve_part1().F == 38


def test_part1_alternative_fq_equals_fc():
    p = solve_part1(service_class=None)
    assert (p.Fq, p.Fm, p.F) == (27, 30, 35)


def test_ceil_is_not_fooled_by_float_noise():
    assert ceil_int(37.000000000001) == 37
    assert ceil_int(37.01) == 38
    assert ceil_int(37.325) == 38


# ---------------- 課題(2) ----------------
def test_part2_exact_fraction_independent():
    F = Fr(38)
    cw = (F + 14) / 26
    assert cw == 2                                 # C/W = 2.0 ちょうど
    wc = 1 / cw
    C = Fr(170) / wc
    Vc = C / Fr(315, 100)
    Vg = Fr(60, 100) * 60 * 1000 / 100
    Vs = 1000 - 170 - 45 - Vc - Vg
    S, G = Vs * Fr(265, 100), Vg * Fr(270, 100)
    sa = Vs / (Vs + Vg) * 100
    mass = 170 + C + S + G
    p = solve_part2(38)
    assert p.w_over_c == pytest.approx(float(wc))
    assert p.C == pytest.approx(float(C)) and C == 340
    assert p.Vc == pytest.approx(float(Vc)) and Vc == Fr(6800, 63)
    assert p.Vg == pytest.approx(float(Vg)) and Vg == 360
    assert p.Vs == pytest.approx(float(Vs))
    assert p.S == pytest.approx(float(S))
    assert p.G == pytest.approx(float(G)) and G == 972
    assert p.s_over_a == pytest.approx(float(sa))
    assert p.unit_mass == pytest.approx(float(mass))


def test_volume_closure():
    p = solve_part2(38)
    assert p.Vw + p.Va + p.Vc + p.Vs + p.Vg == pytest.approx(1000.0)


def test_strength_back_substitution():
    p = solve_part2(38)
    assert 26 * p.c_over_w - 14 == pytest.approx(38.0)
    # 取り違え防止: W/C を横軸に入れると全く違う強度になる
    assert 26 * p.w_over_c - 14 != pytest.approx(38.0)


EXPECTED = {
    "水セメント比[%]": 50.0, "細骨材率[%]": 46.8, "単位水量[kg/m3]": 170,
    "セメント[kg/m3]": 340, "細骨材[kg/m3]": 840, "粗骨材[kg/m3]": 972,
    "絶対容積セメント[L/m3]": 108, "絶対容積細骨材[L/m3]": 317,
    "絶対容積粗骨材[L/m3]": 360, "単位容積質量[kg/m3]": 2322,
}


def test_answer_table_final_rounding():
    assert answer_row(solve_part2(solve_part1().F)) == EXPECTED


def test_answer_table_intermediate_rounding_agrees():
    assert answer_row_intermediate_rounding(38) == EXPECTED


def test_rounded_mass_is_consistent_with_rounded_table():
    r = EXPECTED
    assert r["単位水量[kg/m3]"] + r["セメント[kg/m3]"] + r["細骨材[kg/m3]"] + r["粗骨材[kg/m3]"] \
        == r["単位容積質量[kg/m3]"]


def test_round_half_up():
    assert round_half_up(46.85, 1) == 46.9   # 銀行丸めでないこと
    assert round_half_up(2.5) == 3
    assert round_half_up(840.2183) == 840
