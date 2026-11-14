"""Capture artifact: schema, determinism, serialization, integrity."""

from __future__ import annotations

import unittest

from croma_intel.domain.capture import (
    SCHEMA_VERSION,
    BlockEvidence,
    BrowserMetadata,
    CaptureStatus,
    PageKind,
    Platform,
    RawCapture,
    ScrollReport,
    build_capture,
    compute_capture_id,
    sha256_text,
)

HTML = "<html><head><title>Test</title></head><body><p>hello</p></body></html>"


def make(**overrides):
    kwargs = dict(
        platform=Platform.CROMA.value,
        page_kind=PageKind.PDP.value,
        requested_url="https://www.croma.com/x/p/323360",
        final_url="https://www.croma.com/x/p/323360",
        status=CaptureStatus.SUCCESS.value,
        html=HTML,
        page_title="Test",
        input_sku="323360",
        http_status=200,
        captured_at="2026-08-13T00:00:00.000+00:00",
    )
    kwargs.update(overrides)
    return build_capture(**kwargs)


class TestCaptureConstruction(unittest.TestCase):
    def test_required_fields_present(self):
        c = make()
        for attr in (
            "capture_id", "captured_at", "platform", "input_sku", "requested_url",
            "final_url", "page_title", "html", "status",
        ):
            self.assertTrue(hasattr(c, attr), f"missing required field {attr}")
        self.assertEqual(c.schema_version, SCHEMA_VERSION)
        self.assertEqual(c.input_sku, "323360")
        self.assertEqual(c.platform, "croma")

    def test_html_hash_and_length_derived(self):
        c = make()
        self.assertEqual(c.html_sha256, sha256_text(HTML))
        self.assertEqual(c.html_length, len(HTML))

    def test_capture_is_immutable(self):
        c = make()
        with self.assertRaises(Exception):
            c.status = CaptureStatus.BLOCKED.value  # type: ignore[misc]

    def test_is_success_property(self):
        self.assertTrue(make().is_success)
        self.assertFalse(make(status=CaptureStatus.BLOCKED.value).is_success)

    def test_redirect_detection(self):
        """M0 F10 — requested and served URLs can disagree; that must be visible."""
        c = make(final_url="https://www.croma.com/other/p/999999")
        self.assertTrue(c.redirected)
        self.assertFalse(make().redirected)


class TestCaptureIdDeterminism(unittest.TestCase):
    def test_same_inputs_produce_same_id(self):
        self.assertEqual(make().capture_id, make().capture_id)

    def test_different_html_produces_different_id(self):
        self.assertNotEqual(make().capture_id, make(html=HTML + "<!-- x -->").capture_id)

    def test_different_timestamp_produces_different_id(self):
        a = make(captured_at="2026-08-13T00:00:00.000+00:00")
        b = make(captured_at="2026-08-13T00:00:01.000+00:00")
        self.assertNotEqual(a.capture_id, b.capture_id)

    def test_capture_id_excluded_from_its_own_hash(self):
        manifest = make().to_manifest()
        self.assertEqual(compute_capture_id(manifest), manifest["capture_id"])

    def test_key_order_does_not_affect_id(self):
        manifest = make().to_manifest()
        shuffled = dict(reversed(list(manifest.items())))
        self.assertEqual(compute_capture_id(manifest), compute_capture_id(shuffled))


class TestSerialization(unittest.TestCase):
    def test_manifest_excludes_html_by_default(self):
        self.assertNotIn("html", make().to_manifest())

    def test_manifest_can_include_html(self):
        self.assertEqual(make().to_manifest(include_html=True)["html"], HTML)

    def test_manifest_is_json_serializable(self):
        import json

        json.dumps(make().to_manifest())  # must not raise

    def test_round_trip_preserves_everything(self):
        original = make(
            browser=BrowserMetadata(engine="chromium", headless=False, user_agent="UA"),
            scroll=ScrollReport(performed=True, scroll_count=4, reached_stable=True),
            block=BlockEvidence(is_blocked=False, score=0.0, signals=("A", "B")),
            notes=["note-1"],
        )
        restored = RawCapture.from_manifest(original.to_manifest(), original.html)
        self.assertEqual(restored.capture_id, original.capture_id)
        self.assertEqual(restored.html, original.html)
        self.assertEqual(restored.browser.user_agent, "UA")
        self.assertFalse(restored.browser.headless)
        self.assertEqual(restored.scroll.scroll_count, 4)
        self.assertTrue(restored.scroll.reached_stable)
        self.assertEqual(restored.block.signals, ("A", "B"))
        self.assertEqual(restored.notes, ("note-1",))


class TestIntegrity(unittest.TestCase):
    def test_clean_capture_verifies(self):
        ok, reason = make().verify_integrity()
        self.assertTrue(ok, reason)

    def test_tampered_html_detected(self):
        c = make()
        tampered = RawCapture.from_manifest(c.to_manifest(), "<html>DIFFERENT</html>")
        ok, reason = tampered.verify_integrity()
        self.assertFalse(ok)
        self.assertIn("html_sha256 mismatch", reason)

    def test_tampered_manifest_field_detected(self):
        manifest = make().to_manifest()
        manifest["final_url"] = "https://evil.example/p/1"
        tampered = RawCapture.from_manifest(manifest, HTML)
        ok, reason = tampered.verify_integrity()
        self.assertFalse(ok)
        self.assertIn("capture_id mismatch", reason)


if __name__ == "__main__":
    unittest.main()
