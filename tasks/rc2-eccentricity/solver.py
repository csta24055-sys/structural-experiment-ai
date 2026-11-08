#!/usr/bin/env python3
"""RC II eccentricity worksheet. Stiffnesses are in multiples of K.

The Y-stiff wall is a SINGLE equivalent KY=8K element at x=12m.
All six columns contribute KX=K under X-direction loading.
"""
from dataclasses import dataclass
from fractions import Fraction as F
from math import sqrt


@dataclass(frozen=True)
class Element:
    x: F
    y: F
    value: F


def f(value):
    return F(value)


# Six point loads: (x, y, vertical load in kN)
LOADS = tuple(
    Element(f(x), f(y), f(n))
    for y in (0, 8)
    for x, n in ((0, 100), (6, 200), (12, 100))
)

# Translational stiffness normalized by K
KX = tuple(Element(f(x), f(y), f(1)) for y in (0, 8) for x in (0, 6, 12))
# Only FOUR standalone columns contribute KY=K; wall and its end columns
# form one equivalent spring of KY=8K at x=12.
KY = (
    Element(f(0), f(0), f(1)),
    Element(f(6), f(0), f(1)),
    Element(f(0), f(8), f(1)),
    Element(f(6), f(8), f(1)),
    Element(f(12), f(4), f(8)),
)


def calculate(loads=LOADS, kx=KX, ky=KY):
    total_n = sum((e.value for e in loads), f(0))
    total_kx = sum((e.value for e in kx), f(0))
    total_ky = sum((e.value for e in ky), f(0))
    assert total_n > 0 and total_kx > 0 and total_ky > 0

    gx = sum((e.value * e.x for e in loads), f(0)) / total_n
    gy = sum((e.value * e.y for e in loads), f(0)) / total_n
    rx = sum((e.value * e.x for e in ky), f(0)) / total_ky
    ry = sum((e.value * e.y for e in kx), f(0)) / total_kx

    ex, ey = abs(rx - gx), abs(ry - gy)
    torsion_from_kx = sum((e.value * (e.y - ry)**2 for e in kx), f(0))
    torsion_from_ky = sum((e.value * (e.x - rx)**2 for e in ky), f(0))
    kr = torsion_from_kx + torsion_from_ky
    rex = sqrt(float(kr / total_kx))
    rey = sqrt(float(kr / total_ky))
    eccentricity_x = float(ey) / rex
    eccentricity_y = float(ex) / rey
    return {
        "total_n": total_n, "gx": gx, "gy": gy,
        "total_kx": total_kx, "total_ky": total_ky,
        "rx": rx, "ry": ry, "ex": ex, "ey": ey,
        "torsion_from_kx": torsion_from_kx,
        "torsion_from_ky": torsion_from_ky, "kr": kr,
        "rex": rex, "rey": rey,
        "eccentricity_x": eccentricity_x,
        "eccentricity_y": eccentricity_y,
    }


def main():
    a = calculate()
    print("Vertical load sum = {} kN".format(a["total_n"]))
    print("G = ({}, {}) m".format(a["gx"], a["gy"]))
    print("ΣKX = {}K, ΣKY = {}K".format(a["total_kx"], a["total_ky"]))
    print("R = ({}, {}) m".format(a["rx"], a["ry"]))
    print("eX = {}m, eY = {}m".format(a["ex"], a["ey"]))
    print("ΣKX*YR² = {} K m²; ΣKY*XR² = {} K m²".format(
        a["torsion_from_kx"], a["torsion_from_ky"]))
    print("Kr = {} K m²".format(a["kr"]))
    print("reX = {:.6f} m, reY = {:.6f} m".format(a["rex"], a["rey"]))
    print("ReX = {:.6f}, ReY = {:.6f}".format(
        a["eccentricity_x"], a["eccentricity_y"]))
    print("Exceeds 0.15? X: {}, Y: {}".format(
        a["eccentricity_x"] > .15, a["eccentricity_y"] > .15))


if __name__ == "__main__":
    main()
