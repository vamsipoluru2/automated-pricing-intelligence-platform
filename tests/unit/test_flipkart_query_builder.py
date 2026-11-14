"""Deterministic Flipkart query construction."""

from __future__ import annotations

import unittest

from croma_intel.domain.product import UNKNOWN
from croma_intel.platforms.flipkart.query_builder import build_query_plan

ASUS = (
    "ASUS TUF A15 AMD Ryzen 7 170 Gaming Laptop (16GB, 512GB SSD, Windows 11 Home, "
    "4GB Graphics, 15.6 inch 144 Hz Full HD IPS Display, NVIDIA GeForce RTX 3050, "
    "MS Office Home 2024, Graphite Black, 2.30 KG)"
)
LENOVO = (
    "Lenovo LOQ 15ARP10E AMD Ryzen 5 Gaming Laptop (16GB, 512GB SSD, Windows 11 Home, "
    "6GB Graphics, 15.6 Inch 144 Hz FHD IPS Display, NVIDIA GeForce RTX 3050, "
    "MS Office Home 2024, Luna Grey, 1.8 KG)"
)


class TestShortening(unittest.TestCase):
    def test_full_title_becomes_a_useful_short_query(self):
        query = build_query_plan(ASUS).primary_query
        self.assertEqual(
            query, "ASUS TUF A15 AMD Ryzen 7 Gaming Laptop 16GB 512GB SSD RTX 3050"
        )

    def test_query_is_much_shorter_than_the_title(self):
        self.assertLess(len(build_query_plan(ASUS).primary_query), len(ASUS) / 2)

    def test_query_is_not_over_simplified_to_the_series(self):
        """'ASUS TUF A15' alone would return every variant in the family."""
        query = build_query_plan(ASUS).primary_query
        self.assertNotEqual(query, "ASUS TUF A15")
        self.assertGreaterEqual(len(query.split()), 8)

    def test_never_empty_for_a_real_title(self):
        for title in (ASUS, LENOVO, "HP Victus Gaming Laptop"):
            with self.subTest(title=title[:30]):
                self.assertTrue(build_query_plan(title).primary_query.strip())

    def test_empty_title_yields_an_empty_plan_not_a_crash(self):
        plan = build_query_plan("")
        self.assertEqual(plan.primary_query, "")
        self.assertIn("empty", plan.strategy)

    def test_deterministic(self):
        self.assertEqual(
            build_query_plan(ASUS).primary_query, build_query_plan(ASUS).primary_query
        )


class TestPreservedTerms(unittest.TestCase):
    def setUp(self):
        self.plan = build_query_plan(ASUS)

    def test_preserves_brand(self):
        self.assertEqual(self.plan.extracted_terms["brand"], "ASUS")

    def test_preserves_product_family(self):
        self.assertEqual(self.plan.extracted_terms["series"], "TUF A15")

    def test_preserves_cpu_family_with_vendor(self):
        self.assertEqual(self.plan.extracted_terms["cpu_family"], "AMD Ryzen 7")

    def test_preserves_ram(self):
        self.assertEqual(self.plan.extracted_terms["ram"], "16GB")
        self.assertIn("16GB", self.plan.primary_query)

    def test_preserves_storage(self):
        self.assertEqual(self.plan.extracted_terms["storage"], "512GB SSD")

    def test_preserves_gpu(self):
        self.assertEqual(self.plan.extracted_terms["gpu"], "RTX 3050")

    def test_ram_is_not_confused_with_storage_or_vram(self):
        """16GB RAM, 512GB storage and 4GB VRAM all appear as 'NGB' in one title."""
        self.assertEqual(self.plan.extracted_terms["ram"], "16GB")
        self.assertNotEqual(self.plan.extracted_terms["ram"], "4GB")
        self.assertNotEqual(self.plan.extracted_terms["ram"], "512GB")


class TestRemovedNoise(unittest.TestCase):
    def setUp(self):
        self.plan = build_query_plan(ASUS)
        self.omitted = {t.lower() for t, _ in self.plan.omitted_terms}

    def test_removes_colour(self):
        self.assertNotIn("graphite", self.plan.primary_query.lower())
        self.assertIn("graphite black", self.omitted)

    def test_removes_ms_office(self):
        self.assertNotIn("office", self.plan.primary_query.lower())
        self.assertTrue(any("office" in t for t in self.omitted))

    def test_removes_weight(self):
        self.assertNotIn("kg", self.plan.primary_query.lower())

    def test_removes_windows_marketing(self):
        self.assertNotIn("windows", self.plan.primary_query.lower())

    def test_removes_panel_adjectives(self):
        for token in ("full hd", "ips"):
            self.assertNotIn(token, self.plan.primary_query.lower())

    def test_omissions_record_a_reason(self):
        for _term, reason in self.plan.omitted_terms:
            self.assertTrue(reason)


class TestModelIsNeverQueried(unittest.TestCase):
    """M0 F2: an MTM query returned 2,135 results with baby cream at rank 1."""

    def test_model_recorded_but_absent_from_every_query(self):
        plan = build_query_plan(LENOVO, sku="323360", model="83S000FYIN")
        self.assertEqual(plan.reference_model, "83S000FYIN")
        for query in plan.all_queries:
            with self.subTest(query=query):
                self.assertNotIn("83S000FYIN", query)

    def test_sku_is_never_queried(self):
        plan = build_query_plan(LENOVO, sku="323360")
        for query in plan.all_queries:
            self.assertNotIn("323360", query)


class TestFallbacks(unittest.TestCase):
    def test_fallbacks_are_distinct_and_ordered_broader(self):
        plan = build_query_plan(ASUS)
        self.assertTrue(plan.fallback_queries)
        self.assertNotIn(plan.primary_query, plan.fallback_queries)
        self.assertIn("ASUS TUF A15 Gaming Laptop", plan.fallback_queries)

    def test_plan_serialises_for_the_artifact(self):
        payload = build_query_plan(ASUS, sku="322265", model=UNKNOWN).to_dict()
        self.assertEqual(payload["reference_model"], "UNKNOWN")
        self.assertIn("primary_query", payload)
        self.assertIn("omitted_terms", payload)


if __name__ == "__main__":
    unittest.main()
