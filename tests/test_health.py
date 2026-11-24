"""Tests for the energy maths. These numbers drive real advice, so they get
checked against hand-computed values rather than just "returns something"."""

import unittest

from chizhang import health


FEMALE = {
    "sex": "female", "age": 30, "height_cm": 165, "weight_kg": 55,
    "activity": "light", "goal": "lose", "deficit": 400,
}
MALE = {**FEMALE, "sex": "male"}


class ProfileTest(unittest.TestCase):
    def test_normalise_fills_defaults(self):
        p = health.normalise_profile({})
        for key in ("sex", "age", "height_cm", "weight_kg", "activity", "goal", "deficit"):
            self.assertIn(key, p)

    def test_normalise_rejects_nonsense(self):
        p = health.normalise_profile({"age": 5, "height_cm": 900, "weight_kg": -3, "activity": "nope"})
        self.assertEqual(p["age"], 14)
        self.assertEqual(p["height_cm"], 230.0)
        self.assertEqual(p["weight_kg"], 35.0)
        self.assertEqual(p["activity"], "light")

    def test_normalise_handles_junk_types(self):
        p = health.normalise_profile({"age": "abc", "weight_kg": None, "deficit": "x"})
        self.assertEqual(p["age"], 30)
        self.assertEqual(p["weight_kg"], 60.0)
        self.assertEqual(p["deficit"], 400)


class BmrTest(unittest.TestCase):
    def test_female_mifflin(self):
        # 10*55 + 6.25*165 - 5*30 - 161 = 1270.25
        self.assertAlmostEqual(health.bmr(health.normalise_profile(FEMALE)), 1270.25, places=2)

    def test_male_is_166_higher(self):
        f = health.bmr(health.normalise_profile(FEMALE))
        m = health.bmr(health.normalise_profile(MALE))
        self.assertAlmostEqual(m - f, 166.0, places=2)

    def test_tdee_scales_with_activity(self):
        low = health.tdee(health.normalise_profile({**FEMALE, "activity": "sedentary"}))
        high = health.tdee(health.normalise_profile({**FEMALE, "activity": "active"}))
        self.assertGreater(high, low)


class TargetsTest(unittest.TestCase):
    def test_targets_shape(self):
        t = health.targets(FEMALE)
        for key in ("bmr", "tdee", "kcal", "protein", "carb", "fat", "deficit", "bmi"):
            self.assertIn(key, t)

    def test_protein_is_16_g_per_kg_for_fat_loss(self):
        t = health.targets(FEMALE)
        self.assertAlmostEqual(t["protein"], round(1.6 * 55), delta=1)

    def test_macros_add_up_to_calorie_target(self):
        for goal in ("lose", "keep", "gain"):
            with self.subTest(goal=goal):
                t = health.targets({**FEMALE, "goal": goal})
                derived = t["protein"] * 4 + t["carb"] * 4 + t["fat"] * 9
                # Rounding to whole grams costs a few kcal; 3% is plenty.
                self.assertLessEqual(abs(derived - t["kcal"]), max(30, t["kcal"] * 0.03))

    def test_loss_target_is_below_maintenance(self):
        t = health.targets(FEMALE)
        self.assertLess(t["kcal"], t["tdee"])

    def test_safety_floor_kicks_in(self):
        # A 400 kcal deficit on this profile would land under BMR*1.1.
        t = health.targets(FEMALE)
        self.assertTrue(t["floored"])
        self.assertGreaterEqual(t["kcal"], t["floor"])
        self.assertGreaterEqual(t["kcal"], 1200)

    def test_floor_reported_but_not_applied_when_target_is_high(self):
        t = health.targets({**FEMALE, "weight_kg": 90, "height_cm": 168, "activity": "moderate"})
        self.assertFalse(t["floored"])

    def test_gain_adds_surplus(self):
        keep = health.targets({**FEMALE, "goal": "keep"})
        gain = health.targets({**FEMALE, "goal": "gain"})
        self.assertGreater(gain["kcal"], keep["kcal"])

    def test_keep_matches_tdee(self):
        keep = health.targets({**FEMALE, "goal": "keep"})
        self.assertEqual(keep["kcal"], keep["tdee"])

    def test_bmi(self):
        t = health.targets({**FEMALE, "weight_kg": 60, "height_cm": 165})
        self.assertAlmostEqual(t["bmi"], round(60 / 1.65 ** 2, 1), places=1)

    def test_projected_loss_is_plausible(self):
        t = health.targets(FEMALE)
        self.assertGreaterEqual(t["projected_weekly_loss_kg"], 0)
        self.assertLess(t["projected_weekly_loss_kg"], 1.5)

    def test_large_deficit_never_goes_below_floor(self):
        t = health.targets({**FEMALE, "deficit": 1000})
        self.assertGreaterEqual(t["kcal"], t["floor"])


def entry(meal, name, kcal, p, c, f, day="2026-11-24", category="主食"):
    return {
        "id": name, "date": day, "meal": meal, "name": name, "category": category,
        "kcal": kcal, "protein": p, "carb": c, "fat": f, "grams": 100, "ts": 1,
    }


class DaySummaryTest(unittest.TestCase):
    def setUp(self):
        self.targets = health.targets(FEMALE)
        self.entries = [
            entry("breakfast", "燕麦", 150, 6, 27, 3),
            entry("lunch", "米饭", 174, 4, 39, 0),
            entry("lunch", "鸡胸", 118, 25, 1, 2, category="肉蛋"),
            entry("dinner", "西兰花", 54, 6, 6, 1, category="蔬菜"),
        ]

    def test_groups_by_meal(self):
        day = health.day_summary("2026-11-24", self.entries, self.targets)
        self.assertEqual([m["key"] for m in day["meals"]],
                         ["breakfast", "lunch", "dinner", "snack"])
        lunch = next(m for m in day["meals"] if m["key"] == "lunch")
        self.assertEqual(len(lunch["items"]), 2)
        self.assertAlmostEqual(lunch["totals"]["kcal"], 292, places=1)

    def test_totals_sum_all_meals(self):
        day = health.day_summary("2026-11-24", self.entries, self.targets)
        self.assertAlmostEqual(day["totals"]["kcal"], 496, places=1)
        self.assertAlmostEqual(day["totals"]["protein"], 41, places=1)
        self.assertEqual(day["entry_count"], 4)

    def test_progress_reports_remaining(self):
        day = health.day_summary("2026-11-24", self.entries, self.targets)
        self.assertAlmostEqual(
            day["progress"]["kcal"]["remaining"],
            self.targets["kcal"] - 496, delta=1,
        )

    def test_other_days_are_ignored(self):
        extra = entry("lunch", "别的天", 500, 0, 0, 0, day="2026-11-23")
        day = health.day_summary("2026-11-24", self.entries + [extra], self.targets)
        self.assertAlmostEqual(day["totals"]["kcal"], 496, places=1)

    def test_empty_day(self):
        day = health.day_summary("2026-11-24", [], self.targets)
        self.assertEqual(day["totals"]["kcal"], 0)
        self.assertEqual(day["entry_count"], 0)
        self.assertTrue(day["insights"])


class InsightsTest(unittest.TestCase):
    def setUp(self):
        self.targets = health.targets(FEMALE)

    def test_never_empty(self):
        for entries in ([], [entry("lunch", "米饭", 174, 4, 39, 0)]):
            with self.subTest(count=len(entries)):
                day = health.day_summary("2026-11-24", entries, self.targets)
                self.assertGreater(len(day["insights"]), 0)

    def test_at_most_three(self):
        many = [entry("lunch", f"食物{i}", 400, 0, 60, 20) for i in range(6)]
        day = health.day_summary("2026-11-24", many, self.targets)
        self.assertLessEqual(len(day["insights"]), 3)

    def test_flags_overeating(self):
        heavy = [entry("lunch", "大餐", 2000, 50, 200, 80)]
        day = health.day_summary("2026-11-24", heavy, self.targets)
        texts = " ".join(i["text"] for i in day["insights"])
        self.assertIn("超出目标", texts)
        self.assertTrue(any(i["level"] == "warn" for i in day["insights"]))

    def test_flags_low_protein(self):
        carbs_only = [entry("lunch", "白粥", 200, 2, 45, 0)]
        day = health.day_summary("2026-11-24", carbs_only, self.targets)
        texts = " ".join(i["text"] for i in day["insights"])
        self.assertIn("蛋白质差", texts)

    def test_praises_protein_on_target(self):
        meat = [entry("lunch", "鸡胸", 500, 95, 1, 5, category="肉蛋")]
        day = health.day_summary("2026-11-24", meat, self.targets)
        texts = " ".join(i["text"] for i in day["insights"])
        self.assertIn("蛋白质达标", texts)

    def test_mentions_missing_vegetables(self):
        only_rice = [entry("lunch", "米饭", 174, 4, 39, 0)]
        day = health.day_summary("2026-11-24", only_rice, self.targets)
        texts = " ".join(i["text"] for i in day["insights"])
        self.assertIn("蔬菜", texts)

    def test_no_vegetable_nag_when_vegetables_logged(self):
        meal = [entry("lunch", "西兰花", 54, 6, 6, 1, category="蔬菜"),
                entry("lunch", "鸡胸", 300, 60, 1, 4, category="肉蛋")]
        day = health.day_summary("2026-11-24", meal, self.targets)
        texts = " ".join(i["text"] for i in day["insights"])
        self.assertNotIn("还没吃蔬菜", texts)


class WeekReportTest(unittest.TestCase):
    def test_seven_day_window(self):
        report = health.week_report([], health.targets(FEMALE), "2026-11-24")
        self.assertEqual(len(report["days"]), 7)
        self.assertEqual(report["days"][-1]["date"], "2026-11-24")
        self.assertEqual(report["days"][0]["date"], "2026-11-18")

    def test_counts_logged_days_and_average(self):
        entries = [
            entry("lunch", "a", 1000, 10, 100, 30, day="2026-11-23"),
            entry("lunch", "b", 1400, 10, 100, 30, day="2026-11-24"),
        ]
        report = health.week_report(entries, health.targets(FEMALE), "2026-11-24")
        self.assertEqual(report["logged_days"], 2)
        self.assertEqual(report["avg_kcal"], 1200)

    def test_entries_outside_window_are_ignored(self):
        old = entry("lunch", "old", 500, 0, 0, 0, day="2026-01-01")
        report = health.week_report([old], health.targets(FEMALE), "2026-11-24")
        self.assertEqual(report["logged_days"], 0)

    def test_custom_window(self):
        report = health.week_report([], health.targets(FEMALE), "2026-11-24", days=30)
        self.assertEqual(len(report["days"]), 30)


class WeightTest(unittest.TestCase):
    def test_empty(self):
        out = health.weight_summary([])
        self.assertIsNone(out["latest"])

    def test_latest_and_change(self):
        rows = [
            {"date": "2026-11-18", "kg": 60.0},
            {"date": "2026-11-24", "kg": 58.5},
        ]
        out = health.weight_summary(rows)
        self.assertEqual(out["latest"]["kg"], 58.5)
        self.assertAlmostEqual(out["change_7d"], -1.5, places=1)
        self.assertAlmostEqual(out["change_all"], -1.5, places=1)

    def test_ignores_junk_rows(self):
        rows = [{"date": "2026-11-18", "kg": 60.0}, {"date": "", "kg": 50}, {"date": "2026-11-19", "kg": 0}]
        out = health.weight_summary(rows)
        self.assertEqual(len(out["series"]), 1)

    def test_single_point_has_no_change(self):
        out = health.weight_summary([{"date": "2026-11-24", "kg": 58.0}])
        self.assertEqual(out["latest"]["kg"], 58.0)
        self.assertIsNone(out["change_all"])

    def test_series_sorted(self):
        rows = [{"date": "2026-11-24", "kg": 58}, {"date": "2026-11-20", "kg": 59}]
        out = health.weight_summary(rows)
        self.assertEqual([r["date"] for r in out["series"]], ["2026-11-20", "2026-11-24"])


if __name__ == "__main__":
    unittest.main()
