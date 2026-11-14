"""DOM probe — broad discovery and root-cause classification.

The probe reports structure; it never selects or extracts products. These tests
pin that boundary as well as the diagnostic behaviour.
"""

from __future__ import annotations

import unittest

from croma_intel.browser.dom_probe import (
    diagnose_zero_results,
    find_token,
    probe_html,
    visible_text_from_html,
)


class TestVisibleText(unittest.TestCase):
    def test_strips_script_and_style(self):
        html = "<html><body><script>var x=1;</script><style>.a{}</style><p>Hello</p></body></html>"
        self.assertEqual(visible_text_from_html(html), "Hello")

    def test_collapses_whitespace(self):
        self.assertEqual(visible_text_from_html("<p>a\n\n   b</p>"), "a b")

    def test_empty_input(self):
        self.assertEqual(visible_text_from_html(""), "")


class TestFindToken(unittest.TestCase):
    def test_finds_token_with_context(self):
        hit = find_token("<div>code 323360 here</div>", "323360")
        self.assertTrue(hit["present"])
        self.assertEqual(hit["count"], 1)
        self.assertIn("323360", hit["contexts"][0])

    def test_case_insensitive(self):
        self.assertTrue(find_token("<p>83s000fyin</p>", "83S000FYIN")["present"])

    def test_absent_token(self):
        hit = find_token("<p>nothing</p>", "323360")
        self.assertFalse(hit["present"])
        self.assertEqual(hit["contexts"], [])

    def test_counts_multiple_occurrences(self):
        self.assertEqual(find_token("a 1 b 1 c 1", "1")["count"], 3)

    def test_empty_token_is_absent(self):
        self.assertFalse(find_token("<p>x</p>", "")["present"])


class TestProbeStructure(unittest.TestCase):
    HTML = """
    <html><body>
      <a href="/laptop-a/p/111111" class="product-title">Laptop A</a>
      <a href="/laptop-b/p/222222">Laptop B</a>
      <a href="/computers-tablets/laptops/c/20">All Laptops</a>
      <a href="#top">Top</a>
      <a>no href</a>
      <div class="product-card"><div class="modal-backdrop"></div></div>
    </body></html>
    """

    def test_counts_anchors(self):
        probe = probe_html(self.HTML)
        self.assertEqual(probe.total_anchors, 5)
        self.assertEqual(probe.anchors_with_href, 4)

    def test_counts_pdp_anchors_only(self):
        probe = probe_html(self.HTML)
        self.assertEqual(probe.pdp_anchors, 2)
        self.assertEqual(probe.pdp_ids, ["111111", "222222"])

    def test_reports_hint_classes_without_selecting(self):
        probe = probe_html(self.HTML)
        self.assertIn("product", probe.product_hint_classes)
        self.assertIn("card", probe.product_hint_classes)
        self.assertIn("modal", probe.overlay_hint_classes)
        self.assertIn("backdrop", probe.overlay_hint_classes)

    def test_href_histogram_populated(self):
        self.assertTrue(probe_html(self.HTML).href_prefix_histogram)

    def test_sample_anchors_only_include_href(self):
        self.assertTrue(all(a["href"] for a in probe_html(self.HTML).sample_anchors))

    def test_malformed_html_does_not_raise(self):
        probe_html("<html><body><a href='/x/p/1'>unclosed<div></body>")

    def test_summary_lines_render(self):
        lines = probe_html(self.HTML, tokens=["111111"]).summary_lines()
        self.assertTrue(any("pdp_anchors" in line for line in lines))


class TestRootCauseClassification(unittest.TestCase):
    """Each branch must name a distinct cause; they demand different responses."""

    def test_anchors_present_points_at_identification(self):
        probe = probe_html("<a href='/x/p/1'>x</a>", tokens=["323360"])
        cause = diagnose_zero_results(probe, sku="323360", model=None)
        self.assertIn("PDP anchors ARE present", cause)

    def test_spa_shell_points_at_capture_timing(self):
        html = '<html><body><div id="root"></div><script id="__NEXT_DATA__">{"code":"323360"}</script></body></html>'
        probe = probe_html(html, tokens=["323360"])
        cause = diagnose_zero_results(probe, sku="323360", model=None)
        self.assertIn("CAPTURED BEFORE HYDRATION", cause)

    def test_token_present_without_anchors_points_at_missing_hrefs(self):
        html = "<html><body><div class='tile'>323360 Lenovo</div></body></html>"
        probe = probe_html(html, tokens=["323360"])
        cause = diagnose_zero_results(probe, sku="323360", model=None)
        self.assertIn("without /p/ anchor hrefs", cause)

    def test_overlay_present_is_reported(self):
        html = "<html><body><div class='consent-modal'>Accept cookies</div></body></html>"
        probe = probe_html(html, tokens=["323360"])
        cause = diagnose_zero_results(probe, sku="323360", model=None)
        self.assertIn("interstitial", cause)

    def test_genuinely_empty_is_distinguished(self):
        html = "<html><body><p>No results found for your search.</p></body></html>"
        probe = probe_html(html, tokens=["323360"])
        cause = diagnose_zero_results(probe, sku="323360", model=None)
        self.assertIn("genuinely returning no results", cause)
        self.assertNotIn("HYDRATION", cause)


class TestProbeDoesNotExtract(unittest.TestCase):
    """Boundary guard: the probe reports evidence, it never picks a product."""

    def test_report_has_no_chosen_or_selected_field(self):
        fields = probe_html("<a href='/x/p/1'>x</a>").to_dict().keys()
        for forbidden in ("chosen", "selected", "match", "product", "candidate"):
            self.assertNotIn(forbidden, fields)


if __name__ == "__main__":
    unittest.main()
