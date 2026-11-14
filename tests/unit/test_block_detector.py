"""Block detection — must catch real walls AND must not fire on legitimate content.

The second half matters as much as the first. A detector that flags normal Croma
pages as blocked would silently destroy every run.
"""

from __future__ import annotations

import unittest

from croma_intel.browser.block_detector import BlockDetector, is_empty_shell

CROMA_MARKERS = ("croma", "add to cart", "product details", "/p/")

# A plausible legitimate page: long, with real content markers.
LEGIT_HTML = (
    "<html><head><title>Buy Lenovo LOQ Gaming Laptop Online - Croma</title></head>"
    "<body><nav>Croma</nav><h1>Lenovo LOQ 15ARP10E</h1>"
    "<a href='/lenovo-loq/p/323360'>view</a>"
    "<button>Add to Cart</button><section>Product Details</section>"
    + ("<p>specification row filler</p>" * 400)
    + "</body></html>"
)


class TestRealBlocksDetected(unittest.TestCase):
    def setUp(self):
        self.d = BlockDetector(content_markers=CROMA_MARKERS)

    def test_http_403_is_decisive(self):
        ev = self.d.assess(html=LEGIT_HTML, title="Croma", http_status=403)
        self.assertTrue(ev.is_blocked)
        self.assertEqual(ev.decisive_signal, "HTTP_403_FORBIDDEN")

    def test_http_429_is_decisive(self):
        self.assertTrue(self.d.assess(html="", title="", http_status=429).is_blocked)

    def test_http_503_is_decisive(self):
        self.assertTrue(self.d.assess(html="", title="", http_status=503).is_blocked)

    def test_access_denied_title(self):
        ev = self.d.assess(html=LEGIT_HTML, title="Access Denied", http_status=200)
        self.assertTrue(ev.is_blocked)
        self.assertEqual(ev.decisive_signal, "TITLE_ACCESS_DENIED")

    def test_cloudflare_challenge(self):
        html = "<html><body><script src='/cdn-cgi/challenge-platform/x.js'></script></body></html>"
        ev = self.d.assess(html=html, title="Just a moment...", http_status=200)
        self.assertTrue(ev.is_blocked)

    def test_recaptcha_widget(self):
        html = "<html><body><div class='g-recaptcha'></div></body></html>"
        self.assertTrue(self.d.assess(html=html, title="Verify", http_status=200).is_blocked)

    def test_amazon_robot_check(self):
        html = "<html><body>Enter the characters you see below</body></html>"
        self.assertTrue(self.d.assess(html=html, title="Robot Check", http_status=200).is_blocked)

    def test_tiny_body_scores_but_is_not_alone_decisive(self):
        """A tiny body is suggestive, not conclusive.

        A 13-byte response could be a wall or a broken page — the two need different
        handling, so size alone must not assert "blocked". It still cannot reach
        downstream as good data: the empty-shell check below catches it and the
        capture is classified EMPTY_SHELL rather than SUCCESS.
        """
        ev = self.d.assess(html="<html></html>", title="", http_status=200)
        self.assertIn("BODY_TINY", ev.signals)
        self.assertGreater(ev.score, 0.0)
        self.assertFalse(ev.is_blocked, "size alone must not force a BLOCKED verdict")
        self.assertTrue(is_empty_shell("<html></html>", content_markers=CROMA_MARKERS))

    def test_tiny_body_plus_one_weak_signal_crosses_threshold(self):
        """Corroboration is what promotes weak signals to a verdict."""
        html = "<html><body>Request unsuccessful. Akamai Reference: 1</body></html>"
        ev = self.d.assess(html=html, title="", http_status=200)
        self.assertTrue(ev.is_blocked)
        self.assertGreaterEqual(ev.score, ev.threshold)


class TestLegitimateContentNotFlagged(unittest.TestCase):
    """Regression guards: none of these may be classified as a block."""

    def setUp(self):
        self.d = BlockDetector(content_markers=CROMA_MARKERS)

    def test_normal_croma_page_is_clean(self):
        ev = self.d.assess(
            html=LEGIT_HTML,
            title="Buy Lenovo LOQ Gaming Laptop Online - Croma",
            http_status=200,
        )
        self.assertFalse(ev.is_blocked, f"false positive: {ev.detail}")
        self.assertIsNone(ev.decisive_signal)

    def test_page_discussing_captcha_in_prose_is_clean(self):
        """The bare word 'captcha' in help copy must not trigger a block verdict."""
        html = LEGIT_HTML.replace(
            "<h1>", "<p>Having trouble with a captcha on checkout? Contact support.</p><h1>"
        )
        ev = self.d.assess(html=html, title="Help - Croma", http_status=200)
        self.assertFalse(ev.is_blocked, f"false positive: {ev.detail}")

    def test_small_page_with_content_marker_is_clean(self):
        html = "<html><body><a href='/x/p/1'>Croma</a>Add to Cart</body></html>"
        ev = self.d.assess(html=html, title="Croma", http_status=200)
        self.assertFalse(ev.is_blocked, f"false positive: {ev.detail}")

    def test_404_is_not_a_block(self):
        """A 404 is a missing page, not a bot wall. They need different handling."""
        ev = self.d.assess(html=LEGIT_HTML, title="Page Not Found", http_status=404)
        self.assertFalse(ev.is_blocked, f"false positive: {ev.detail}")

    def test_evidence_recorded_even_when_clean(self):
        ev = self.d.assess(html=LEGIT_HTML, title="Croma", http_status=200)
        self.assertFalse(ev.is_blocked)
        self.assertEqual(ev.detail, "no block signals detected")
        self.assertEqual(ev.signals, ())


class TestEmptyShell(unittest.TestCase):
    """M0 F3 — Reliance Digital returned HTTP 200 with only a page title."""

    def test_shell_detected(self):
        html = "<html><head><title>Online Electronic Shopping Store in India</title></head><body></body></html>"
        self.assertTrue(is_empty_shell(html, content_markers=("add to cart",)))

    def test_real_page_not_a_shell(self):
        self.assertFalse(is_empty_shell(LEGIT_HTML, content_markers=CROMA_MARKERS))

    def test_empty_string_is_a_shell(self):
        self.assertTrue(is_empty_shell("", content_markers=CROMA_MARKERS))


if __name__ == "__main__":
    unittest.main()
