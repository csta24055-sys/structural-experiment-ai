#!/usr/bin/env python3
"""Independent verification of the RC II eccentricity worksheet.

This file deliberately does NOT import solver.py.  It re-enters the
input data in a different form (one list of physical members) and uses a
different method:

Method M (matrix): assemble the 3x3 rigid-diaphragm stiffness matrix about
the origin, DOF = (u: X-translation, v: Y-translation, theta: rotation,
counter-clockwise positive).  For a spring kx located at y and a spring ky
located at x:

    Kuu += kx          Kut += -kx*y      Ktt += kx*y**2
    Kvv += ky          Kvt += +ky*x      Ktt += ky*x**2

The centre of rigidity is the point about which translation and rotation
uncouple:  RX = Kvt/Kvv,  RY = -Kut/Kuu.  The torsional stiffness about R
follows from the parallel-axis theorem:

    Kr = Ktt - Kut**2/Kuu - Kvt**2/Kvv

Method D (displacement): apply a unit horizontal force at the centre of
gravity G and solve K d = f numerically.  The floor rotation must equal
P*e/Kr, which checks e and Kr together without using the textbook formula.

Stiffness is carried as a multiple of the reference stiffness K (symbolic),
lengths in metres, loads in kN.
"""
from fractions import Fraction as F
from math import sqrt

import numpy as np

# ---------------------------------------------------------------------------
# Input re-entered from PROBLEM.md as physical members.
# (name, x[m], y[m], N[kN], KX/K, KY/K)
# Right wall at x=12: in Y the wall+both end columns is ONE spring 8K, so the
# two end columns get KY=0 here and the wall carries KY=8.  In X the wall is
# not effective ("X方向入力に対しては柱のみ抵抗") so the wall has KX=0 and the
# end columns keep KX=1.
# ---------------------------------------------------------------------------
MEMBERS = [
    ("C1 柱(0,0)",   0, 0, 100, 1, 1),
    ("C2 柱(6,0)",   6, 0, 200, 1, 1),
    ("C3 柱(12,0)", 12, 0, 100, 1, 0),   # KY included in wall 8K
    ("C4 柱(0,8)",   0, 8, 100, 1, 1),
    ("C5 柱(6,8)",   6, 8, 200, 1, 1),
    ("C6 柱(12,8)", 12, 8, 100, 1, 0),   # KY included in wall 8K
    ("W  耐震壁",    12, 4,   0, 0, 8),   # y is irrelevant for KY terms
]


def rigid_floor_matrix(members):
    kuu = kvv = kut = kvt = ktt = F(0)
    for _, x, y, _, kx, ky in members:
        x, y, kx, ky = F(x), F(y), F(kx), F(ky)
        kuu += kx
        kut += -kx * y
        kvv += ky
        kvt += ky * x
        ktt += kx * y * y + ky * x * x
    return kuu, kvv, kut, kvt, ktt


def analyse(members):
    n_sum = sum(F(m[3]) for m in members)
    gx = sum(F(m[3]) * m[1] for m in members) / n_sum
    gy = sum(F(m[3]) * m[2] for m in members) / n_sum
    kuu, kvv, kut, kvt, ktt = rigid_floor_matrix(members)
    rx, ry = kvt / kvv, -kut / kuu
    kr = ktt - kut * kut / kuu - kvt * kvt / kvv
    ex, ey = abs(rx - gx), abs(ry - gy)
    rex, rey = sqrt(kr / kuu), sqrt(kr / kvv)
    return dict(n=n_sum, gx=gx, gy=gy, sum_kx=kuu, sum_ky=kvv,
                ktt_origin=ktt, rx=rx, ry=ry, ex=ex, ey=ey, kr=kr,
                rex=rex, rey=rey, Rex=float(ey) / rex, Rey=float(ex) / rey)


def rotation_under_unit_force(members, direction):
    """Solve K d = f with a unit force at G (numerically, K := 1)."""
    a = analyse(members)
    kuu, kvv, kut, kvt, ktt = map(float, rigid_floor_matrix(members))
    k = np.array([[kuu, 0.0, kut], [0.0, kvv, kvt], [kut, kvt, ktt]])
    gx, gy = float(a["gx"]), float(a["gy"])
    if direction == "X":   # Px=1 at G: moment about origin = -Px*gy
        f = np.array([1.0, 0.0, -gy])
    else:                  # Py=1 at G: moment about origin = +Py*gx
        f = np.array([0.0, 1.0, gx])
    return float(np.linalg.solve(k, f)[2])


def variant(name, members):
    a = analyse(members)
    return (name, a)


def sensitivity_cases():
    base = MEMBERS
    # (a) wrong: count end columns' KY again on top of the 8K wall
    double = [m if m[0][:2] not in ("C3", "C6") else (m[0], m[1], m[2], m[3], 1, 1)
              for m in base]
    # (b) wrong: X/Y swapped (wall treated as resisting X, columns-only in Y)
    swapped = [(n, x, y, N, ky, kx) for (n, x, y, N, kx, ky) in base]
    # (c) wrong: let the wall also resist X (KX=8K) - contradicts the note
    wall_x = [m if m[0][0] != "W" else (m[0], m[1], m[2], m[3], 8, 8) for m in base]
    # (d) check: wall y-coordinate arbitrary (0 or 8 instead of 4)
    wall_y0 = [m if m[0][0] != "W" else (m[0], m[1], 0, m[3], m[4], m[5]) for m in base]
    wall_y8 = [m if m[0][0] != "W" else (m[0], m[1], 8, m[3], m[4], m[5]) for m in base]
    # (e) wrong: lecture example values (right-bottom column 3K, no wall)
    lecture_like = [m for m in base if m[0][0] != "W"]
    lecture_like = [m if m[0][:2] not in ("C3", "C6") else
                    (m[0], m[1], m[2], m[3], 1, 3 if m[0][:2] == "C3" else 1)
                    for m in lecture_like]
    return [
        variant("正: 壁8Kを1回・X方向は柱6本", base),
        variant("誤(a): 壁8K + 端柱KY 2本を二重計上", double),
        variant("誤(b): X/Y取り違え(KXとKYを入替)", swapped),
        variant("誤(c): 壁がX方向にも8K抵抗", wall_x),
        variant("確認(d1): 壁のY座標=0m", wall_y0),
        variant("確認(d2): 壁のY座標=8m", wall_y8),
        variant("誤(e): 講義例題の右下柱3Kを流用", lecture_like),
    ]


def main():
    a = analyse(MEMBERS)
    print("== Method M: rigid-floor stiffness matrix (about origin) ==")
    kuu, kvv, kut, kvt, ktt = rigid_floor_matrix(MEMBERS)
    print(f"Kuu=ΣKX={kuu}K  Kvv=ΣKY={kvv}K  Kuθ={kut}K·m  Kvθ={kvt}K·m  Kθθ={ktt}K·m²")
    print(f"G=({a['gx']}, {a['gy']}) m   R=({a['rx']}, {a['ry']}) m")
    print(f"eX={a['ex']} m  eY={a['ey']} m")
    print(f"Kr = {ktt} - ({kut})²/{kuu} - ({kvt})²/{kvv} = {a['kr']} K·m²")
    print(f"reX=√({a['kr']}/{kuu})={a['rex']:.6f} m  reY=√({a['kr']}/{kvv})={a['rey']:.6f} m")
    print(f"ReX={a['Rex']:.6f}  ReY={a['Rey']:.6f}")
    print()
    print("== Method D: displacement solve, unit force at G ==")
    for d, e in (("X", a["ey"]), ("Y", a["ex"])):
        th = rotation_under_unit_force(MEMBERS, d)
        expect = float(e) / float(a["kr"])
        print(f"P{d}=1 at G: θ(solve)={th:+.9f}/K  |θ|·Kr={abs(th)*float(a['kr']):.9f} m"
              f"  (expected e={float(e):.9f} m, P·e/Kr={expect:.9f})")
    print()
    print("== Sensitivity (interpretation / mistake checks) ==")
    print(f"{'case':<34}{'ΣKX':>5}{'ΣKY':>5}{'RX':>8}{'RY':>6}{'Kr':>8}{'ReX':>8}{'ReY':>8}")
    for name, v in sensitivity_cases():
        print(f"{name:<28}{str(v['sum_kx']):>5}{str(v['sum_ky']):>5}"
              f"{float(v['rx']):>8.3f}{float(v['ry']):>6.2f}{float(v['kr']):>8.2f}"
              f"{v['Rex']:>8.4f}{v['Rey']:>8.4f}")


if __name__ == "__main__":
    main()
