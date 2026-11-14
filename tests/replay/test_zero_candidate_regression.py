"""Regression: the M1 live-run failure, reproduced and diagnosed offline.

Observed 2026-08-13 on the first successful live run:

    [PASS] croma_search: status=SUCCESS http=200
    [FAIL] result_identification: search returned zero candidate results

Browser access worked. The capture was taken before the page had rendered its
product links, so identification correctly found nothing. These tests pin both
halves of that: the failure must be *diagnosed* rather than merely reported, and
the post-fix (hydrated) page must identify the right product.

Runs entirely offline from committed fixtures.
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from croma_intel.browser.artifacts import CaptureStore
from croma_intel.browser.block_detector import BlockDetector, is_empty_shell
from croma_intel.browser.dom_probe import diagnose_zero_results, probe_html
from croma_intel.domain.capture import (
    CaptureStatus,
    PageKind,
    Platform,
    ResultsWaitReport,
    build_capture,
)
from croma_intel.platforms.croma.result_identifier import Confidence, ResultIdentifier
from croma_intel.platforms.croma.search_parser import extract_search_candidates

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "croma"
SKU = "323360"
MODEL = "83S000FYIN"
CROMA_MARKERS = ("croma", "add to cart", "product details", "/p/")


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _NoNetwork:
    def __enter__(self):
        self._s, self._c = socket.socket, socket.create_connection

        def blocked(*_a, **_kw):
            raise AssertionError("network access attempted")

        socket.socket = blocked  # type: ignore[assignment]
        socket.create_connection = blocked  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        socket.socket = self._s  # type: ignore[assignment]
        socket.create_connection = self._c  # type: ignore[assignment]


class TestPreHydrationFailureSignature(unittest.TestCase):
    """The exact observed failure: HTTP 200, SUCCESS, yet zero candidates."""

    def setUp(self):
        self.html = load("search_323360_prehydration.html")

    def test_page_is_not_classified_as_blocked(self):
        """Critical: this must NOT look like a bot wall. Croma served a real page."""
        evidence = BlockDetector(content_markers=CROMA_MARKERS).assess(
            html=self.html, title="Search Results - Croma", http_status=200
        )
        self.assertFalse(evidence.is_blocked, f"false block verdict: {evidence.detail}")
        self.assertIsNone(evidence.decisive_signal)

    def test_page_is_not_an_empty_shell(self):
        """It is large and has real chrome — so status legitimately reads SUCCESS."""
        self.assertFalse(is_empty_shell(self.html, content_markers=CROMA_MARKERS))

    def test_reproduces_zero_candidates(self):
        self.assertEqual(extract_search_candidates(self.html), [])

    def test_identification_fails_structurally_and_does_not_guess(self):
        report = ResultIdentifier().identify(sku=SKU, model=MODEL, candidates=[])
        self.assertEqual(report.confidence, Confidence.NOT_FOUND.value)
        self.assertIsNone(report.chosen)
        self.assertIn("zero candidate", report.reason)

    def test_probe_finds_the_sku_hidden_in_the_data_payload(self):
        """The decisive clue: results EXIST in the payload but not as links."""
        probe = probe_html(self.html, tokens=[SKU, MODEL])
        self.assertEqual(probe.pdp_anchors, 0)
        self.assertTrue(probe.token_hits[SKU]["present"])
        self.assertTrue(probe.token_hits[MODEL]["present"])
        self.assertTrue(probe.spa_markers_found)
        self.assertTrue(probe.likely_client_rendered)

    def test_root_cause_names_capture_timing_not_a_block_or_empty_results(self):
        probe = probe_html(self.html, tokens=[SKU, MODEL])
        cause = diagnose_zero_results(probe, sku=SKU, model=MODEL)
        self.assertIn("CAPTURED BEFORE HYDRATION", cause)
        self.assertIn("not a Croma block", cause)
        self.assertNotIn("genuinely returning no results", cause)


class TestHydratedPageSucceeds(unittest.TestCase):
    """After waiting for results, the same search identifies the right product."""

    def setUp(self):
        self.html = load("search_323360_hydrated.html")

    def test_candidates_are_extracted(self):
        candidates = extract_search_candidates(self.html)
        self.assertEqual(len(candidates), 4)
        self.assertTrue(all("/p/" in c.url for c in candidates))

    def test_identifies_correct_sku_and_not_the_first_result(self):
        candidates = extract_search_candidates(self.html)
        report = ResultIdentifier().identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.CONFIDENT.value)
        self.assertIsNotNone(report.chosen)
        self.assertNotEqual(report.chosen.position, 1, "must not fall back to first result")
        self.assertEqual(report.chosen.position, 3)
        self.assertTrue(report.chosen.url.endswith(f"/p/{SKU}"))

    def test_model_bearing_decoy_with_wrong_sku_is_rejected(self):
        """Position 2 carries the correct MODEL but the wrong SKU. It must lose."""
        candidates = extract_search_candidates(self.html)
        decoy = next(c for c in candidates if "316063" in c.url)
        self.assertIn(MODEL, decoy.title)  # the decoy really does contain the model
        report = ResultIdentifier().identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertNotIn("316063", report.chosen.url)

    def test_probe_reports_a_healthy_page(self):
        probe = probe_html(self.html, tokens=[SKU, MODEL])
        self.assertGreater(probe.pdp_anchors, 0)
        self.assertFalse(probe.likely_client_rendered)
        cause = diagnose_zero_results(probe, sku=SKU, model=MODEL)
        self.assertIn("PDP anchors ARE present", cause)


class TestCaptureRecordsDiagnosticEvidence(unittest.TestCase):
    """Schema v1.1 must persist what was missing when the live failure occurred."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="croma_regress_"))
        self.store = CaptureStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failed_wait_is_recorded_in_the_artifact(self):
        html = load("search_323360_prehydration.html")
        capture = build_capture(
            platform=Platform.CROMA.value,
            page_kind=PageKind.SEARCH.value,
            requested_url="https://www.croma.com/search/?text=323360",
            final_url="https://www.croma.com/search/?text=323360",
            status=CaptureStatus.SUCCESS.value,
            html=html,
            page_title="Search Results - Croma",
            input_sku=SKU,
            http_status=200,
            visible_text="Search Results",
            screenshot_path="artifacts/screenshots/search_323360.png",
            results_wait=ResultsWaitReport(
                performed=True, appeared=False, initial_count=0, final_count=0,
                reached_networkidle=False, waited_ms=20000,
            ),
        )
        self.store.write(capture)

        with _NoNetwork():
            restored = self.store.read(capture.capture_id, verify=True)

        # The artifact alone now explains the failure without re-running anything.
        self.assertTrue(restored.results_wait.performed)
        self.assertFalse(restored.results_wait.appeared)
        self.assertEqual(restored.results_wait.final_count, 0)
        self.assertEqual(restored.screenshot_path, "artifacts/screenshots/search_323360.png")
        self.assertEqual(restored.visible_text, "Search Results")

        probe = probe_html(restored.html, tokens=[SKU, MODEL])
        self.assertIn("CAPTURED BEFORE HYDRATION", diagnose_zero_results(probe, sku=SKU, model=MODEL))


if __name__ == "__main__":
    unittest.main()
