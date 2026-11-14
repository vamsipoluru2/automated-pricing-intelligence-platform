"""Search-page parsing and configuration.

The parser is pure over stored HTML, which is what lets search results be
re-derived offline from a capture.
"""

from __future__ import annotations

import unittest

from croma_intel.config.settings import Settings
from croma_intel.platforms.croma.search_parser import (
    extract_page_title,
    extract_search_candidates,
)

SEARCH_HTML = """
<html><head><title>Search - Croma</title></head><body>
  <div class="results">
    <a href="/laptop-a/p/111111"><span>Laptop A</span></a>
    <a href="/lenovo-loq-15arp10e/p/323360">Lenovo LOQ 15ARP10E 83S000FYIN</a>
    <a href="/computers-tablets/laptops/c/20">All Laptops</a>
    <a href="/laptop-a/p/111111">Laptop A duplicate link</a>
    <a href="https://www.croma.com/laptop-c/p/333333">Laptop C</a>
  </div>
</body></html>
"""


class TestSearchParser(unittest.TestCase):
    def test_extracts_only_pdp_links(self):
        candidates = extract_search_candidates(SEARCH_HTML)
        urls = [c.url for c in candidates]
        self.assertTrue(all("/p/" in u for u in urls))
        self.assertFalse(any("/c/20" in u for u in urls))

    def test_deduplicates_by_pdp_id(self):
        candidates = extract_search_candidates(SEARCH_HTML)
        ids = [c.url.rsplit("/p/", 1)[1] for c in candidates]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(candidates), 3)

    def test_relative_urls_made_absolute(self):
        candidates = extract_search_candidates(SEARCH_HTML)
        self.assertTrue(all(c.url.startswith("https://www.croma.com/") for c in candidates))

    def test_positions_reflect_document_order(self):
        candidates = extract_search_candidates(SEARCH_HTML)
        self.assertEqual([c.position for c in candidates], [1, 2, 3])
        self.assertIn("111111", candidates[0].url)  # first in document
        self.assertIn("323360", candidates[1].url)  # the one we actually want

    def test_titles_captured(self):
        candidates = extract_search_candidates(SEARCH_HTML)
        target = next(c for c in candidates if "323360" in c.url)
        self.assertIn("83S000FYIN", target.title)

    def test_prefers_longer_title_for_duplicate_id(self):
        candidates = extract_search_candidates(SEARCH_HTML)
        first = next(c for c in candidates if "111111" in c.url)
        self.assertEqual(first.title, "Laptop A duplicate link")

    def test_limit_respected(self):
        self.assertEqual(len(extract_search_candidates(SEARCH_HTML, limit=2)), 2)

    def test_empty_html_yields_nothing(self):
        self.assertEqual(extract_search_candidates(""), [])

    def test_malformed_html_does_not_raise(self):
        broken = "<html><body><a href='/x/p/1'>unclosed<div></body>"
        extract_search_candidates(broken)  # must not raise

    def test_page_title_extraction(self):
        self.assertEqual(extract_page_title(SEARCH_HTML), "Search - Croma")
        self.assertEqual(extract_page_title("<html></html>"), "")


class TestSettings(unittest.TestCase):
    def test_defaults_load(self):
        s = Settings()
        self.assertEqual(s.browser.engine, "chromium")
        self.assertTrue(s.browser.headless)
        self.assertEqual(s.croma.base_url, "https://www.croma.com")
        self.assertEqual(s.croma.test_pincode, "560087")  # M1 correction

    def test_search_url_built_from_sku(self):
        self.assertEqual(
            Settings().search_url("323360"),
            "https://www.croma.com/search/?text=323360",
        )

    def test_search_url_quotes_query(self):
        self.assertIn("%20", Settings().search_url("lenovo loq"))

    def test_round_trip_through_dict(self):
        s = Settings()
        self.assertEqual(Settings.from_dict(s.to_dict()), s)

    def test_partial_override(self):
        s = Settings.from_dict({"browser": {"headless": False, "navigation_timeout_ms": 1234}})
        self.assertFalse(s.browser.headless)
        self.assertEqual(s.browser.navigation_timeout_ms, 1234)
        self.assertEqual(s.browser.engine, "chromium")  # untouched default

    def test_unknown_keys_ignored(self):
        s = Settings.from_dict({"browser": {"not_a_real_setting": 1}})
        self.assertEqual(s.browser.engine, "chromium")

    def test_missing_file_falls_back_to_defaults(self):
        self.assertEqual(Settings.load("does/not/exist.json"), Settings())

    def test_no_eup_anywhere_in_config(self):
        """Architectural rule 7: EUP does not exist in this codebase."""
        blob = repr(Settings().to_dict()).lower()
        self.assertNotIn("eup", blob)


if __name__ == "__main__":
    unittest.main()
