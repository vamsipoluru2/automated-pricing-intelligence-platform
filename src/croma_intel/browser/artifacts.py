"""Content-addressed capture store.

Guarantees the M0 replay property: once a capture is written, every downstream stage
must be able to read it with the network physically unavailable.

Layout on disk::

    artifacts/captures/
      manifests/<capture_id>.json        human-readable, diffable
      blobs/<html_sha256[:2]>/<html_sha256>.html.gz   deduplicated page bodies

Blobs are keyed by content hash, so re-capturing an unchanged page costs no extra
bytes. Manifests are keyed by capture id, so two captures of the same page at
different times remain distinct events.

Stdlib only. No network, no Playwright.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterator

from ..domain.capture import RawCapture, compute_capture_id
from ..domain.errors import CaptureIntegrityError, CaptureNotFoundError


class CaptureStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.manifest_dir = self.root / "manifests"
        self.blob_dir = self.root / "blobs"

    # ------------------------------------------------------------------ writing

    def _blob_path(self, html_sha256: str) -> Path:
        return self.blob_dir / html_sha256[:2] / f"{html_sha256}.html.gz"

    def _manifest_path(self, capture_id: str) -> Path:
        return self.manifest_dir / f"{capture_id}.json"

    def write(self, capture: RawCapture) -> Path:
        """Persist a capture. Returns the manifest path.

        The blob is written first: a manifest pointing at a missing blob would be a
        broken artifact, whereas an orphan blob is merely harmless garbage.
        """
        blob_path = self._blob_path(capture.html_sha256)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if not blob_path.exists():  # content-addressed: identical HTML written once
            with gzip.open(blob_path, "wt", encoding="utf-8") as fh:
                fh.write(capture.html)

        manifest_path = self._manifest_path(capture.capture_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = capture.to_manifest(include_html=False)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True, ensure_ascii=False)
        return manifest_path

    # ------------------------------------------------------------------ reading

    def read(self, capture_id: str, *, verify: bool = True) -> RawCapture:
        """Load a capture. Never touches the network.

        ``verify=True`` re-derives the content hash and capture id; a tampered or
        truncated artifact raises rather than being silently trusted.
        """
        manifest_path = self._manifest_path(capture_id)
        if not manifest_path.exists():
            raise CaptureNotFoundError(f"no manifest for capture_id={capture_id!r} at {manifest_path}")

        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        blob_path = self._blob_path(manifest["html_sha256"])
        if not blob_path.exists():
            raise CaptureIntegrityError(
                f"manifest {capture_id} references missing blob {manifest['html_sha256']}"
            )
        with gzip.open(blob_path, "rt", encoding="utf-8") as fh:
            html = fh.read()

        capture = RawCapture.from_manifest(manifest, html)

        if verify:
            ok, reason = capture.verify_integrity()
            if not ok:
                raise CaptureIntegrityError(f"integrity check failed for {capture_id}: {reason}")
        return capture

    def exists(self, capture_id: str) -> bool:
        return self._manifest_path(capture_id).exists()

    def list_capture_ids(self) -> list[str]:
        if not self.manifest_dir.exists():
            return []
        return sorted(p.stem for p in self.manifest_dir.glob("*.json"))

    def iter_captures(self, *, verify: bool = True) -> Iterator[RawCapture]:
        for capture_id in self.list_capture_ids():
            yield self.read(capture_id, verify=verify)

    def verify_all(self) -> list[tuple[str, bool, str]]:
        """Integrity-check every stored capture. Returns (capture_id, ok, reason)."""
        results: list[tuple[str, bool, str]] = []
        for capture_id in self.list_capture_ids():
            try:
                capture = self.read(capture_id, verify=False)
                ok, reason = capture.verify_integrity()
            except Exception as exc:  # noqa: BLE001 - report, never abort the sweep
                ok, reason = False, f"{type(exc).__name__}: {exc}"
            results.append((capture_id, ok, reason))
        return results
