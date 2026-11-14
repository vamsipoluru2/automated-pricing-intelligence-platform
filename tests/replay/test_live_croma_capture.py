"""Tests over the REAL Croma DOM captured live on 2026-08-13.

Provenance: the only successful load of three attempts.
  * request 1 -> HTTP 200, 485,676 bytes, full site chrome, ZERO product links
  * requests 2 and 3 (minutes later) -> HTTP 403 "Access Denied",
    errors.edgesuite.net (Akamai) => progressive rate-based bot defense

This fixture is the ground truth for the M1 search failure. It is committed so the
investigation can continue offline indefinitely without touching Croma again — the
capture/replay guarantee doing exactly the job it exists for.
"""

from __future__ import annotations

import gzip
import json
import unittest
from pathlib import Path

from croma_intel.browser.block_detector import BlockDetector
from croma_intel.browser.dom_probe import diagnose_zero_results, find_token, probe_html
from croma_intel.platforms.croma.result_identifier import Confidence, ResultIdentifier
from croma_intel.platforms.croma.search_parser import extract_search_candidates

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "croma"
SKU = "323360"
MODEL = "83S000FYIN"
CROMA_MARKERS = ("croma", "add to cart", "product details", "/p/")


def load_live_html() -> str:
    with gzip.open(FIXTURES / "live_search_323360_200_no_products.html.gz", "rt", encoding="utf-8") as fh:
        return fh.read()


def load_meta() -> dict:
    return json.loads((FIXTURES / "live_search_323360_meta.json").read_text(encoding="utf-8"))


class TestLiveCaptureProvenance(unittest.TestCase):
    def test_metadata_records_a_successful_non_blocked_load(self):
        meta = load_meta()
        self.assertEqual(meta["http_status"], 200)
        self.assertEqual(meta["capture_status"], "SUCCESS")
        self.assertFalse(meta["block"]["is_blocked"])

    def test_wait_for_results_ran_and_timed_out(self):
        """The fix was active: it waited 20s and reached networkidle, still no links."""
        wait = load_meta()["results_wait"]
        self.assertTrue(wait["performed"])
        self.assertFalse(wait["appeared"])
        self.assertTrue(wait["reached_networkidle"])
        self.assertGreater(wait["waited_ms"], 19_000)
        self.assertEqual(wait["final_count"], 0)


class TestLiveCaptureIsNotABlock(unittest.TestCase):
    """Critical: this page must NOT be classified as a bot wall. It was HTTP 200."""

    def setUp(self):
        self.html = load_live_html()

    def test_block_detector_clears_it(self):
        evidence = BlockDetector(content_markers=CROMA_MARKERS).assess(
            html=self.html, title=load_meta()["page_title"], http_status=200
        )
        self.assertFalse(evidence.is_blocked, f"false block verdict: {evidence.detail}")


class TestLiveCaptureHasNoProducts(unittest.TestCase):
    def setUp(self):
        self.html = load_live_html()

    def test_zero_pdp_anchors(self):
        self.assertEqual(extract_search_candidates(self.html), [])

    def test_no_pdp_url_pattern_anywhere(self):
        """Not merely unrendered links: no /p/<digits> appears in 485KB of markup.

        Asserted on the count, not with assertNotIn, so a failure reports a number
        rather than dumping the entire fixture into the test output.
        """
        import re

        matches = re.findall(r"/p/\d+", self.html)
        self.assertEqual(
            len(matches), 0, f"expected zero /p/<digits>, found {len(matches)}: {matches[:5]}"
        )

    def test_identification_fails_without_guessing(self):
        report = ResultIdentifier().identify(sku=SKU, model=MODEL, candidates=[])
        self.assertEqual(report.confidence, Confidence.NOT_FOUND.value)
        self.assertIsNone(report.chosen)

    def test_model_token_is_entirely_absent(self):
        self.assertFalse(find_token(self.html, MODEL)["present"])


class TestTokenHitsAreClassified(unittest.TestCase):
    """The bug this fixture exposed: echoed-query hits read as product evidence."""

    def setUp(self):
        self.html = load_live_html()

    def test_sku_appears_but_only_as_noise(self):
        hit = find_token(self.html, SKU)
        self.assertTrue(hit["present"], "the SKU string is in the page")
        self.assertGreater(hit["count"], 1)
        self.assertFalse(
            hit["meaningful"],
            f"every hit should be head/script/URL noise, got regions={hit['regions']}",
        )
        self.assertEqual(hit["meaningful_count"], 0)

    def test_regions_are_reported(self):
        self.assertTrue(find_token(self.html, SKU)["regions"])


class TestRootCauseOnRealCapture(unittest.TestCase):
    """The corrected classifier must name the location gate, not invent a parser bug."""

    def setUp(self):
        self.html = load_live_html()
        self.probe = probe_html(
            self.html, tokens=[SKU, MODEL], visible_text=load_meta()["visible_text"]
        )

    def test_detects_react_app(self):
        self.assertTrue(self.probe.spa_markers_found)

    def test_detects_location_gate(self):
        self.assertTrue(self.probe.location_gate_markers)
        self.assertTrue(self.probe.likely_location_gated)

    def test_root_cause_names_location_gate(self):
        cause = diagnose_zero_results(self.probe, sku=SKU, model=MODEL)
        self.assertIn("LOCATION/PINCODE GATE", cause)

    def test_root_cause_does_not_claim_a_parser_bug(self):
        """The pre-fix classifier said 'rendered without /p/ anchor hrefs'. It was wrong."""
        cause = diagnose_zero_results(self.probe, sku=SKU, model=MODEL)
        self.assertNotIn("without /p/ anchor hrefs", cause)

    def test_root_cause_flags_the_soft_block_alternative(self):
        """Honesty requirement: a degraded anti-bot page looks identical."""
        cause = diagnose_zero_results(self.probe, sku=SKU, model=MODEL)
        self.assertIn("anti-bot", cause.lower())


class TestAkamaiDenyPageIsDetected(unittest.TestCase):
    """The 403 page served on requests 2 and 3 must be classified as a block."""

    DENY_HTML = (
        "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>"
        "<H1>Access Denied</H1>"
        "You don't have permission to access \"http://www.croma.com/search/?\" on this server."
        "<P>Reference #18.7ca2dfad.1786601179.1011c308</P>"
        "<P>https://errors.edgesuite.net/18.7ca2dfad.1786601179.1011c308</P>"
        "</BODY></HTML>"
    )

    def setUp(self):
        self.d = BlockDetector(content_markers=CROMA_MARKERS)

    def test_detected_via_http_status(self):
        ev = self.d.assess(html=self.DENY_HTML, title="Access Denied", http_status=403)
        self.assertTrue(ev.is_blocked)
        self.assertEqual(ev.decisive_signal, "HTTP_403_FORBIDDEN")

    def test_detected_from_body_even_without_status(self):
        """A proxy may strip the status; the Akamai page must still be caught."""
        ev = self.d.assess(html=self.DENY_HTML, title="", http_status=None)
        self.assertTrue(ev.is_blocked)
        self.assertIn("AKAMAI_EDGESUITE_DENY", ev.signals)

    def test_deny_page_never_reaches_identification(self):
        ev = self.d.assess(html=self.DENY_HTML, title="Access Denied", http_status=403)
        self.assertTrue(ev.is_blocked, "a block must fail as a capture failure, not empty data")


if __name__ == "__main__":
    unittest.main()
