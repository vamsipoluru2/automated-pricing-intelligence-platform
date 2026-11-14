"""LIVE Croma integration test — opt-in only. NEVER part of the offline suite or CI.

Requires network access, `pip install playwright`, and `playwright install chromium`.

Run explicitly:

    CROMA_LIVE=1 python -m tests.integration.test_croma_live

or via pytest with the marker:

    CROMA_LIVE=1 pytest tests/integration -m live

Guarded three ways so it can never run by accident:
  1. the CROMA_LIVE environment variable must be set;
  2. Playwright must be importable;
  3. it lives outside the directories the offline runner discovers.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from croma_intel.browser.playwright_driver import playwright_available  # noqa: E402
from croma_intel.config.settings import Settings  # noqa: E402

LIVE_ENABLED = os.environ.get("CROMA_LIVE") == "1"
PLAYWRIGHT_OK, PLAYWRIGHT_DETAIL = playwright_available()

# Reference product from M0 findings.
SKU = os.environ.get("CROMA_TEST_SKU", "323360")
MODEL = os.environ.get("CROMA_TEST_MODEL", "83S000FYIN")

try:  # pytest marker when available; harmless otherwise
    import pytest

    pytestmark = pytest.mark.live
except Exception:  # noqa: BLE001
    pytestmark = None


@unittest.skipUnless(LIVE_ENABLED, "set CROMA_LIVE=1 to run the live Croma gate")
@unittest.skipUnless(PLAYWRIGHT_OK, f"playwright unavailable: {PLAYWRIGHT_DETAIL}")
class TestCromaLiveGate(unittest.TestCase):
    """The real M1 gate. A failure here is a genuine M1 NO-GO signal."""

    def test_m1_gate_end_to_end(self):
        from croma_intel.gate.m1_gate import GateOutcome, run_m1_gate

        settings = Settings()
        report = run_m1_gate(sku=SKU, model=MODEL, settings=settings, headless=True)

        out = Path(settings.storage.reports_dir) / f"m1_gate_live_{SKU}.json"
        report.write(out)

        print(f"\n--- M1 LIVE GATE: {report.outcome} ---")
        for step in report.steps:
            print(f"  [{'PASS' if step.ok else 'FAIL'}] {step.name}: {step.detail}")
        print(f"  report: {out}")
        print(f"  {report.summary}\n")

        self.assertNotEqual(
            report.outcome,
            GateOutcome.INCONCLUSIVE,
            "gate could not execute; this is an environment problem, not a Croma verdict",
        )
        self.assertEqual(
            report.outcome,
            GateOutcome.GO,
            f"M1 gate did not pass: {report.summary}",
        )
        self.assertIsNotNone(report.search_capture_id)
        self.assertIsNotNone(report.pdp_capture_id)

    def test_captures_are_replayable_offline_after_live_run(self):
        """Whatever the live run produced must satisfy the replay guarantee."""
        from croma_intel.browser.artifacts import CaptureStore

        store = CaptureStore(Settings().storage.captures_dir)
        ids = store.list_capture_ids()
        if not ids:
            self.skipTest("no captures on disk; run test_m1_gate_end_to_end first")
        failures = [(cid, reason) for cid, ok, reason in store.verify_all() if not ok]
        self.assertEqual(failures, [], f"captures failed integrity verification: {failures}")


def main() -> int:
    if not LIVE_ENABLED:
        print("CROMA_LIVE is not set to 1 — refusing to run the live gate.")
        print("Run:  CROMA_LIVE=1 python -m tests.integration.test_croma_live")
        return 2
    if not PLAYWRIGHT_OK:
        print(f"Playwright unavailable: {PLAYWRIGHT_DETAIL}")
        print("Run:  pip install playwright && playwright install chromium")
        return 2
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCromaLiveGate)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
