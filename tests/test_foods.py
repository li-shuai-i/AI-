"""Tests for the food table: shape, sanity of the numbers, and search."""

import unittest

from chizhang import foods


class FoodTableTest(unittest.TestCase):
    def test_has_enough_foods(self):
        self.assertGreaterEqual(len(foods.FOODS), 150)

    def test_every_food_has_required_fields(self):
        for food in foods.FOODS:
            with self.subTest(food=food["name"]):
                self.assertTrue(food["name"])
                self.assertIn(food["category"], foods.CATEGORIES)
                self.assertTrue(food["units"], "每个食物至少要有一个常见份量")
                self.assertTrue(food["id"].startswith("f"))
                for unit in food["units"]:
                    self.assertGreater(unit["grams"], 0)
                    self.assertLess(unit["grams"], 1000)

    def test_ids_are_unique(self):
        ids = [f["id"] for f in foods.FOODS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_names_are_unique(self):
        names = [f["name"] for f in foods.FOODS]
        self.assertEqual(len(names), len(set(names)))

    def test_macros_are_in_plausible_range(self):
        for food in foods.FOODS:
            p = food["per100"]
            with self.subTest(food=food["name"]):
                self.assertGreaterEqual(p["kcal"], 0)
                self.assertLessEqual(p["kcal"], 900)
                for key in ("protein", "carb", "fat"):
                    self.assertGreaterEqual(p[key], 0)
                    self.assertLessEqual(p[key], 100)

    def test_energy_matches_macros_for_whole_foods(self):
        """4/4/9 kcal per gram should roughly reproduce the stated energy.

        Fruit and vegetables carry fibre, and dishes hide unknown oil, so the
        tolerance is generous — this only catches put-the-decimal-in-the-wrong
        -place mistakes, which is exactly the bug that matters here.
        """
        skip = {"饮料零食", "油脂调味"}
        for food in foods.FOODS:
            if food["category"] in skip:
                continue
            p = food["per100"]
            derived = 4 * p["protein"] + 4 * p["carb"] + 9 * p["fat"]
            tolerance = max(35.0, p["kcal"] * 0.3)
            with self.subTest(food=food["name"], stated=p["kcal"], derived=round(derived)):
                self.assertLessEqual(
                    abs(derived - p["kcal"]), tolerance,
                    f"{food['name']} 标称 {p['kcal']} kcal，按三大营养素推算 {derived:.0f} kcal",
                )

    def test_scale(self):
        rice = next(f for f in foods.FOODS if f["name"] == "米饭")
        out = foods.scale(rice, 150)
        self.assertAlmostEqual(out["kcal"], rice["per100"]["kcal"] * 1.5, places=1)
        self.assertAlmostEqual(foods.scale(rice, 0)["kcal"], 0.0)

    def test_stats(self):
        stats = foods.stats()
        self.assertEqual(stats["total"], len(foods.FOODS))
        self.assertEqual(sum(stats["categories"].values()), len(foods.FOODS))


class SearchTest(unittest.TestCase):
    def test_exact_match_ranks_first(self):
        results = foods.search("米饭")
        self.assertEqual(results[0]["name"], "米饭")

    def test_prefix_match_ranks_first(self):
        # Names starting with the query beat names that merely contain it.
        results = foods.search("鸡")
        self.assertTrue(results)
        self.assertTrue(results[0]["name"].startswith("鸡"))

    def test_alias_hits_are_included_after_name_hits(self):
        # 番茄炒蛋 lists 西红柿炒鸡蛋 as an alias, so it should answer "鸡"
        # but never outrank the real chicken entries.
        names = [f["name"] for f in foods.search("鸡")]
        self.assertIn("番茄炒蛋", names)
        self.assertGreater(names.index("番茄炒蛋"), 0)

    def test_alias_search(self):
        results = foods.search("西红柿")
        names = [f["name"] for f in results]
        self.assertIn("番茄", names)

    def test_category_filter(self):
        results = foods.search("", "水果")
        self.assertTrue(results)
        self.assertTrue(all(f["category"] == "水果" for f in results))

    def test_category_and_query(self):
        results = foods.search("豆", "蔬菜")
        self.assertTrue(all(f["category"] == "蔬菜" for f in results))

    def test_limit(self):
        self.assertLessEqual(len(foods.search("", "", limit=5)), 5)

    def test_no_match(self):
        self.assertEqual(foods.search("zzzz不存在"), [])

    def test_case_insensitive(self):
        self.assertEqual(
            [f["name"] for f in foods.search("KFC")],
            [f["name"] for f in foods.search("kfc")],
        )


if __name__ == "__main__":
    unittest.main()
