import math
import unittest
from fractions import Fraction as F

from solver import Element, LOADS, KX, KY, calculate


class EccentricityTests(unittest.TestCase):
    def setUp(self):
        self.a = calculate()

    def test_source_inputs(self):
        self.assertEqual(len(LOADS), 6)
        self.assertEqual(sum(t.value for t in LOADS), 800)
        self.assertEqual(len(KX), 6)
        self.assertEqual(len(KY), 5)  # Four columns + ONE wall spring
        self.assertEqual(sum(t.value for t in KY if t.x == 12), 8)

    def test_center_of_mass_and_rigidity(self):
        for key, expected in {
            "gx": 6, "gy": 4, "rx": 9, "ry": 4,
            "total_kx": 6, "total_ky": 12,
        }.items():
            self.assertEqual(self.a[key], expected)

    def test_torsional_rigidity_independent_grouping(self):
        # Six X springs are 4m away from Ry.
        self.assertEqual(self.a["torsion_from_kx"], 6 * 1 * 4**2)
        # Y: two at x0, two at x6, wall KY8 at x12, all about Rx9.
        direct_ky = 2 * (0 - 9)**2 + 2 * (6 - 9)**2 + 8 * (12 - 9)**2
        self.assertEqual(direct_ky, 252)
        self.assertEqual(self.a["torsion_from_ky"], direct_ky)
        self.assertEqual(self.a["kr"], 348)

    def test_eccentricities_and_radii(self):
        self.assertEqual(self.a["ex"], 3)
        self.assertEqual(self.a["ey"], 0)
        self.assertAlmostEqual(self.a["rex"], math.sqrt(58), 12)
        self.assertAlmostEqual(self.a["rey"], math.sqrt(29), 12)
        self.assertEqual(self.a["eccentricity_x"], 0)
        self.assertAlmostEqual(self.a["eccentricity_y"], 3/math.sqrt(29), 12)
        self.assertLessEqual(self.a["eccentricity_x"], .15)
        self.assertGreater(self.a["eccentricity_y"], .15)

    def test_global_coordinate_translation_invariance(self):
        def shift(elems, dx, dy):
            return tuple(Element(e.x + F(dx), e.y + F(dy), e.value) for e in elems)
        b = calculate(shift(LOADS, 13, -7), shift(KX, 13, -7), shift(KY, 13, -7))
        self.assertEqual((b["rx"]-b["gx"], b["ry"]-b["gy"]),
                         (self.a["rx"]-self.a["gx"], self.a["ry"]-self.a["gy"]))
        self.assertEqual(b["kr"], self.a["kr"])
        self.assertAlmostEqual(b["eccentricity_y"], self.a["eccentricity_y"], 12)

    def test_wall_double_counting_would_change_answer(self):
        # A regression guard against wrongly adding end-column KY twice.
        wrong_ky = KY + (Element(F(12), F(0), F(1)),
                          Element(F(12), F(8), F(1)))
        wrong = calculate(LOADS, KX, wrong_ky)
        self.assertNotEqual(wrong["total_ky"], self.a["total_ky"])
        self.assertNotEqual(wrong["rx"], self.a["rx"])
        self.assertNotAlmostEqual(wrong["eccentricity_y"], self.a["eccentricity_y"])


if __name__ == "__main__":
    unittest.main()
