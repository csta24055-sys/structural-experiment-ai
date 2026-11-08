"""Cross-checks between solver.py and the independent matrix method."""
import math
import unittest

import independent_check as ic
import solver


class IndependentCrossCheck(unittest.TestCase):
    def setUp(self):
        self.s = solver.calculate()
        self.m = ic.analyse(ic.MEMBERS)

    def test_both_methods_agree_exactly(self):
        for key_s, key_m in (("gx", "gx"), ("gy", "gy"), ("rx", "rx"),
                             ("ry", "ry"), ("ex", "ex"), ("ey", "ey"),
                             ("kr", "kr"), ("total_kx", "sum_kx"),
                             ("total_ky", "sum_ky")):
            self.assertEqual(self.s[key_s], self.m[key_m], key_s)
        self.assertAlmostEqual(self.s["eccentricity_x"], self.m["Rex"], 12)
        self.assertAlmostEqual(self.s["eccentricity_y"], self.m["Rey"], 12)

    def test_parallel_axis_decomposition(self):
        # Kθθ about origin = 1416 ; minus 24²/6 = 96 ; minus 108²/12 = 972
        self.assertEqual(self.m["ktt_origin"], 1416)
        self.assertEqual(1416 - 96 - 972, 348)

    def test_displacement_solution_confirms_e_over_kr(self):
        th_y = ic.rotation_under_unit_force(ic.MEMBERS, "Y")
        self.assertAlmostEqual(abs(th_y) * 348, 3.0, 9)
        self.assertLess(th_y, 0)  # G is left of R -> clockwise twist
        self.assertAlmostEqual(ic.rotation_under_unit_force(ic.MEMBERS, "X"), 0.0, 12)

    def test_wall_y_coordinate_is_irrelevant(self):
        cases = dict(ic.sensitivity_cases())
        for k in ("確認(d1): 壁のY座標=0m", "確認(d2): 壁のY座標=8m"):
            self.assertEqual(cases[k]["kr"], 348)
            self.assertAlmostEqual(cases[k]["Rey"], 3 / math.sqrt(29), 12)

    def test_mistakes_change_the_answer(self):
        cases = dict(ic.sensitivity_cases())
        self.assertAlmostEqual(cases["誤(a): 壁8K + 端柱KY 2本を二重計上"]["Rey"], 0.6729, 4)
        self.assertEqual(cases["誤(b): X/Y取り違え(KXとKYを入替)"]["Rey"], 0)
        self.assertAlmostEqual(cases["誤(e): 講義例題の右下柱3Kを流用"]["Rey"], 0.2474, 4)


if __name__ == "__main__":
    unittest.main()
