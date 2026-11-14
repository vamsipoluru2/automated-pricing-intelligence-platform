"""Offline replay guarantee.

Architectural rule 1: once a capture exists, downstream processing must not require
network access. These tests enforce that by *physically disabling sockets* for the
duration of the replay — not by convention, but by making a network call impossible.
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from croma_intel.browser.artifacts import CaptureStore
from croma_intel.domain.capture import (
    BlockEvidence,
    BrowserMetadata,
    CaptureStatus,
    PageKind,
    Platform,
    ScrollReport,
    build_capture,
)
from croma_intel.domain.errors import CaptureIntegrityError, CaptureNotFoundError
from croma_intel.platforms.croma.search_parser import extract_search_candidates

SEARCH_HTML = """
<html><head><title>Search results - Croma</title></head><body>
  <a href="/other-laptop/p/999999">Some Other Laptop</a>
  <a href="/lenovo-loq-15arp10e/p/323360">Lenovo LOQ 15ARP10E Gaming Laptop 83S000FYIN</a>
</body></html>
"""


class _NoNetwork:
    """Context manager that makes any socket creation raise."""

    def __init__(self):
        self._real_socket = socket.socket
        self._real_create = socket.create_connection

    def __enter__(self):
        def blocked(*_a, **_kw):
            raise AssertionError("network access attempted during offline replay")

        socket.socket = blocked  # type: ignore[assignment]
        socket.create_connection = blocked  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        socket.socket = self._real_socket  # type: ignore[assignment]
        socket.create_connection = self._real_create  # type: ignore[assignment]


class ReplayTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="croma_replay_"))
        self.store = CaptureStore(self.tmp)
        self.capture = build_capture(
            platform=Platform.CROMA.value,
            page_kind=PageKind.SEARCH.value,
            requested_url="https://www.croma.com/search/?text=323360",
            final_url="https://www.croma.com/search/?text=323360",
            status=CaptureStatus.SUCCESS.value,
            html=SEARCH_HTML,
            page_title="Search results - Croma",
            input_sku="323360",
            http_status=200,
            browser=BrowserMetadata(engine="chromium", headless=True, user_agent="UA"),
            scroll=ScrollReport(performed=True, scroll_count=3, reached_stable=True),
            block=BlockEvidence(is_blocked=False, detail="no block signals detected"),
        )
        self.store.write(self.capture)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestOfflineReplay(ReplayTestBase):
    def test_capture_written_to_disk(self):
        self.assertTrue(self.store.exists(self.capture.capture_id))
        self.assertIn(self.capture.capture_id, self.store.list_capture_ids())

    def test_replay_without_network(self):
        """The core guarantee: load a full capture with sockets disabled."""
        with _NoNetwork():
            restored = self.store.read(self.capture.capture_id, verify=True)
            self.assertEqual(restored.capture_id, self.capture.capture_id)
            self.assertTrue(restored.html)
            self.assertEqual(restored.html, SEARCH_HTML)
            self.assertEqual(restored.final_url, self.capture.final_url)
            self.assertEqual(restored.input_sku, "323360")
            self.assertEqual(restored.page_title, "Search results - Croma")
            self.assertTrue(restored.browser.user_agent)
            self.assertTrue(restored.scroll.performed)

    def test_downstream_parsing_works_offline(self):
        """Re-derive search candidates from a stored capture with no network."""
        with _NoNetwork():
            restored = self.store.read(self.capture.capture_id)
            candidates = extract_search_candidates(restored.html)
            self.assertEqual(len(candidates), 2)
            self.assertTrue(any("323360" in c.url for c in candidates))

    def test_replay_is_deterministic(self):
        a = self.store.read(self.capture.capture_id)
        b = self.store.read(self.capture.capture_id)
        self.assertEqual(a.capture_id, b.capture_id)
        self.assertEqual(a.html, b.html)
        self.assertEqual(a.to_manifest(), b.to_manifest())

    def test_integrity_verified_on_load(self):
        ok, reason = self.store.read(self.capture.capture_id).verify_integrity()
        self.assertTrue(ok, reason)


class TestStoreFailureModes(ReplayTestBase):
    def test_missing_capture_raises(self):
        with self.assertRaises(CaptureNotFoundError):
            self.store.read("does-not-exist")

    def test_missing_blob_raises_integrity_error(self):
        blob = self.store._blob_path(self.capture.html_sha256)  # noqa: SLF001
        blob.unlink()
        with self.assertRaises(CaptureIntegrityError):
            self.store.read(self.capture.capture_id)

    def test_tampered_manifest_raises(self):
        import json

        path = self.store._manifest_path(self.capture.capture_id)  # noqa: SLF001
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["final_url"] = "https://evil.example/"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(CaptureIntegrityError):
            self.store.read(self.capture.capture_id, verify=True)

    def test_verify_all_reports_per_capture(self):
        results = self.store.verify_all()
        self.assertEqual(len(results), 1)
        capture_id, ok, _ = results[0]
        self.assertEqual(capture_id, self.capture.capture_id)
        self.assertTrue(ok)


class TestBlobDeduplication(ReplayTestBase):
    def test_identical_html_stored_once(self):
        second = build_capture(
            platform=Platform.CROMA.value,
            page_kind=PageKind.SEARCH.value,
            requested_url="https://www.croma.com/search/?text=323360",
            final_url="https://www.croma.com/search/?text=323360",
            status=CaptureStatus.SUCCESS.value,
            html=SEARCH_HTML,  # same content
            input_sku="323360",
            captured_at="2099-01-01T00:00:00.000+00:00",  # different capture event
        )
        self.store.write(second)
        self.assertNotEqual(second.capture_id, self.capture.capture_id)
        blobs = list((self.tmp / "blobs").rglob("*.html.gz"))
        self.assertEqual(len(blobs), 1, "identical HTML should be stored once")
        self.assertEqual(len(self.store.list_capture_ids()), 2)


if __name__ == "__main__":
    unittest.main()
