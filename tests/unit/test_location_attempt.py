"""Regression: the un-started-driver guard in ``set_location``.

The guard existed to turn "there is no page" into a report rather than an exception —
failure to find the control is evidence about the DOM, not an error. But it was
constructing ``LocationAttempt(performed=False, detail=...)`` against a dataclass
that had no ``detail`` field, so the guard itself raised ``TypeError``. A branch
written specifically to avoid an exception was the only branch guaranteed to throw
one.

Unreachable in the proven M1 flow, which always starts the driver first. These tests
exercise it directly so it cannot rot again.

Imports the driver module without Playwright installed, which the lazy-import rule
makes possible.
"""

from __future__ import annotations

import unittest

from croma_intel.browser.playwright_driver import LocationAttempt, PlaywrightDriver
from croma_intel.config.settings import Settings


class TestLocationAttemptShape(unittest.TestCase):
    def test_detail_is_a_real_field(self):
        attempt = LocationAttempt(performed=False, detail="driver not started")
        self.assertEqual(attempt.detail, "driver not started")
        self.assertFalse(attempt.performed)

    def test_detail_defaults_to_empty(self):
        self.assertEqual(LocationAttempt().detail, "")

    def test_detail_is_distinct_from_the_step_trace(self):
        """``steps`` is what was attempted; ``detail`` is why nothing was."""
        attempt = LocationAttempt(performed=True, steps=("clicked:x",), detail="")
        self.assertEqual(attempt.steps, ("clicked:x",))
        self.assertEqual(attempt.detail, "")

    def test_detail_survives_serialisation(self):
        payload = LocationAttempt(detail="driver not started").to_dict()
        self.assertEqual(payload["detail"], "driver not started")
        self.assertIsInstance(payload["steps"], list)


class TestUnstartedDriverGuard(unittest.TestCase):
    """The branch that used to raise."""

    def setUp(self):
        # Never started: no Playwright, no browser, no page.
        self.driver = PlaywrightDriver(Settings())

    def test_set_location_returns_a_report_instead_of_raising(self):
        attempt = self.driver.set_location("560087")
        self.assertIsInstance(attempt, LocationAttempt)

    def test_the_report_says_nothing_was_performed(self):
        attempt = self.driver.set_location("560087")
        self.assertFalse(attempt.performed)
        self.assertFalse(attempt.field_found)
        self.assertFalse(attempt.products_appeared)

    def test_the_report_explains_itself(self):
        self.assertIn("not started", self.driver.set_location("560087").detail)

    def test_no_products_are_claimed_to_have_appeared(self):
        """A guard failure must never look like a satisfied location gate."""
        attempt = self.driver.set_location("560087")
        self.assertEqual(attempt.pdp_anchors_before, 0)
        self.assertEqual(attempt.pdp_anchors_after, 0)
        self.assertFalse(attempt.products_appeared)


if __name__ == "__main__":
    unittest.main()
