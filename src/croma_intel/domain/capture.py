"""Immutable capture artifact — the boundary between network and offline processing.

Architectural rule 1: capture and extraction are separated by an immutable artifact.
Everything in this module is stdlib-only and pure. It must never import Playwright,
perform I/O, or reach the network.

A ``RawCapture`` is the *only* thing downstream stages are permitted to read. Once a
capture exists on disk, every downstream stage must be runnable with the network
physically unavailable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

# 1.1.0 adds visible_text, screenshot_path and results_wait. These were required
# evidence in M0 §17.1 but were missing from 1.0.0, which is why the first live run
# could not be diagnosed from its own artifact.
SCHEMA_VERSION = "1.1.0"


class CaptureStatus(str, Enum):
    """Terminal outcome of a capture attempt.

    ``BLOCKED`` is deliberately distinct from ``NAVIGATION_FAILED``: M0 established
    that a bot wall must fail as a *capture* failure and must never flow downstream
    as "a product page with no content".
    """

    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    TIMEOUT = "TIMEOUT"
    EMPTY_SHELL = "EMPTY_SHELL"  # HTTP 200 but no meaningful content (M0 F3, RD)


class PageKind(str, Enum):
    SEARCH = "SEARCH"
    PDP = "PDP"
    OTHER = "OTHER"


class Platform(str, Enum):
    CROMA = "croma"
    FLIPKART = "flipkart"
    AMAZON_IN = "amazon_in"
    RELIANCE_DIGITAL = "reliance_digital"
    VIJAY_SALES = "vijay_sales"


def utc_now_iso() -> str:
    """Timestamp in a stable, sortable, timezone-explicit form."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic JSON for hashing.

    ``sort_keys`` matters: an unsorted dump would make ``capture_id`` depend on
    insertion order, which would silently break integrity verification on replay.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class BrowserMetadata:
    """What produced this capture. Recorded so a capture can be reproduced or blamed."""

    engine: str = "unknown"
    browser_version: str = "unknown"
    headless: bool = True
    user_agent: str = ""
    viewport_width: int = 0
    viewport_height: int = 0
    locale: str = ""
    timezone_id: str = ""
    proxy_used: bool = False
    driver_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "BrowserMetadata":
        known = {f for f in BrowserMetadata.__dataclass_fields__}
        return BrowserMetadata(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class ScrollReport:
    """Evidence of what dynamic-content work was actually performed.

    Without this you cannot distinguish "the spec table is absent" from "the spec
    table had not loaded yet" — which is the difference between a real finding and
    a silent extraction bug.
    """

    performed: bool = False
    scroll_count: int = 0
    initial_height_px: int = 0
    final_height_px: int = 0
    height_changed: bool = False
    reached_stable: bool = False
    expanders_clicked: int = 0
    expander_labels: tuple[str, ...] = ()
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["expander_labels"] = list(self.expander_labels)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "ScrollReport":
        known = {f for f in ScrollReport.__dataclass_fields__}
        data = {k: v for k, v in d.items() if k in known}
        if "expander_labels" in data:
            data["expander_labels"] = tuple(data["expander_labels"] or ())
        return ScrollReport(**data)


@dataclass(frozen=True)
class ResultsWaitReport:
    """Evidence about waiting for client-rendered results to appear.

    Distinguishes "the page had no products" from "we snapshotted before the page
    had rendered its products" — the two demand opposite responses, and conflating
    them is what made the first live run's zero-candidate failure ambiguous.
    """

    performed: bool = False
    appeared: bool = False
    initial_count: int = 0
    final_count: int = 0
    reached_networkidle: bool = False
    waited_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "ResultsWaitReport":
        known = {f for f in ResultsWaitReport.__dataclass_fields__}
        return ResultsWaitReport(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class BlockEvidence:
    """Why the block detector reached its verdict. Always recorded, even when clean.

    Recording the *negative* verdict matters as much as the positive one: it is the
    evidence that a page was assessed and found legitimate, rather than never checked.
    """

    is_blocked: bool = False
    score: float = 0.0
    threshold: float = 1.0
    decisive_signal: str | None = None
    signals: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signals"] = list(self.signals)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "BlockEvidence":
        known = {f for f in BlockEvidence.__dataclass_fields__}
        data = {k: v for k, v in d.items() if k in known}
        if "signals" in data:
            data["signals"] = tuple(data["signals"] or ())
        return BlockEvidence(**data)


@dataclass(frozen=True)
class RawCapture:
    """An immutable record of one page fetch.

    ``capture_id`` is derived from the content via :func:`compute_capture_id` and is
    verifiable on load — a mutated artifact fails verification rather than being
    silently trusted.
    """

    # --- identity
    capture_id: str
    schema_version: str
    captured_at: str

    # --- request context
    platform: str
    page_kind: str
    input_sku: str | None
    requested_url: str
    final_url: str

    # --- outcome
    status: str
    http_status: int | None
    page_title: str
    html: str
    html_sha256: str
    html_length: int

    # --- evidence
    browser: BrowserMetadata
    scroll: ScrollReport
    block: BlockEvidence
    visible_text: str = ""
    screenshot_path: str | None = None
    results_wait: ResultsWaitReport = field(default_factory=ResultsWaitReport)
    notes: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers

    @property
    def is_success(self) -> bool:
        return self.status == CaptureStatus.SUCCESS.value

    @property
    def redirected(self) -> bool:
        """M0 F10: requested ID and served content can disagree. Always check."""
        return self.requested_url != self.final_url

    def to_manifest(self, *, include_html: bool = False) -> dict[str, Any]:
        """Serialize to a JSON-safe dict.

        HTML is excluded by default — it is stored as a separate compressed blob so
        the manifest stays human-readable and diffable.
        """
        payload: dict[str, Any] = {
            "capture_id": self.capture_id,
            "schema_version": self.schema_version,
            "captured_at": self.captured_at,
            "platform": self.platform,
            "page_kind": self.page_kind,
            "input_sku": self.input_sku,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "status": self.status,
            "http_status": self.http_status,
            "page_title": self.page_title,
            "html_sha256": self.html_sha256,
            "html_length": self.html_length,
            "browser": self.browser.to_dict(),
            "scroll": self.scroll.to_dict(),
            "block": self.block.to_dict(),
            # Kept in the manifest, not the blob: this is what makes a capture
            # diagnosable without decompressing the HTML.
            "visible_text": self.visible_text,
            "screenshot_path": self.screenshot_path,
            "results_wait": self.results_wait.to_dict(),
            "notes": list(self.notes),
            "extra": dict(self.extra),
        }
        if include_html:
            payload["html"] = self.html
        return payload

    @staticmethod
    def from_manifest(manifest: Mapping[str, Any], html: str) -> "RawCapture":
        return RawCapture(
            capture_id=manifest["capture_id"],
            schema_version=manifest.get("schema_version", SCHEMA_VERSION),
            captured_at=manifest["captured_at"],
            platform=manifest["platform"],
            page_kind=manifest["page_kind"],
            input_sku=manifest.get("input_sku"),
            requested_url=manifest["requested_url"],
            final_url=manifest["final_url"],
            status=manifest["status"],
            http_status=manifest.get("http_status"),
            page_title=manifest.get("page_title", ""),
            html=html,
            html_sha256=manifest["html_sha256"],
            html_length=manifest["html_length"],
            browser=BrowserMetadata.from_dict(manifest.get("browser", {})),
            scroll=ScrollReport.from_dict(manifest.get("scroll", {})),
            block=BlockEvidence.from_dict(manifest.get("block", {})),
            visible_text=manifest.get("visible_text", ""),
            screenshot_path=manifest.get("screenshot_path"),
            results_wait=ResultsWaitReport.from_dict(manifest.get("results_wait", {})),
            notes=tuple(manifest.get("notes", ())),
            extra=dict(manifest.get("extra", {})),
        )

    def verify_integrity(self) -> tuple[bool, str]:
        """Re-derive the content hash and capture id. Returns (ok, reason)."""
        actual_html_hash = sha256_text(self.html)
        if actual_html_hash != self.html_sha256:
            return False, (
                f"html_sha256 mismatch: manifest={self.html_sha256[:16]}… "
                f"actual={actual_html_hash[:16]}…"
            )
        if len(self.html) != self.html_length:
            return False, f"html_length mismatch: manifest={self.html_length} actual={len(self.html)}"
        expected_id = compute_capture_id(self.to_manifest())
        if expected_id != self.capture_id:
            return False, f"capture_id mismatch: manifest={self.capture_id[:16]}… expected={expected_id[:16]}…"
        return True, "ok"


def compute_capture_id(manifest: Mapping[str, Any]) -> str:
    """Derive a deterministic capture id from manifest content.

    The ``capture_id`` field itself is excluded, otherwise the hash could never be
    reproduced. Everything else — including the timestamp and the block verdict —
    participates, so any tampering is detectable.
    """
    payload = {k: v for k, v in manifest.items() if k not in ("capture_id", "html")}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_capture(
    *,
    platform: str,
    page_kind: str,
    requested_url: str,
    final_url: str,
    status: str,
    html: str,
    page_title: str = "",
    input_sku: str | None = None,
    http_status: int | None = None,
    browser: BrowserMetadata | None = None,
    scroll: ScrollReport | None = None,
    block: BlockEvidence | None = None,
    visible_text: str = "",
    screenshot_path: str | None = None,
    results_wait: ResultsWaitReport | None = None,
    notes: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
    captured_at: str | None = None,
) -> RawCapture:
    """Construct a capture with a correctly derived id. The only sanctioned way to build one."""
    manifest_stub: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at or utc_now_iso(),
        "platform": platform,
        "page_kind": page_kind,
        "input_sku": input_sku,
        "requested_url": requested_url,
        "final_url": final_url,
        "status": status,
        "http_status": http_status,
        "page_title": page_title,
        "html_sha256": sha256_text(html),
        "html_length": len(html),
        "browser": (browser or BrowserMetadata()).to_dict(),
        "scroll": (scroll or ScrollReport()).to_dict(),
        "block": (block or BlockEvidence()).to_dict(),
        "visible_text": visible_text,
        "screenshot_path": screenshot_path,
        "results_wait": (results_wait or ResultsWaitReport()).to_dict(),
        "notes": list(notes),
        "extra": dict(extra or {}),
    }
    capture_id = compute_capture_id(manifest_stub)
    return RawCapture(
        capture_id=capture_id,
        schema_version=manifest_stub["schema_version"],
        captured_at=manifest_stub["captured_at"],
        platform=platform,
        page_kind=page_kind,
        input_sku=input_sku,
        requested_url=requested_url,
        final_url=final_url,
        status=status,
        http_status=http_status,
        page_title=page_title,
        html=html,
        html_sha256=manifest_stub["html_sha256"],
        html_length=manifest_stub["html_length"],
        browser=browser or BrowserMetadata(),
        scroll=scroll or ScrollReport(),
        block=block or BlockEvidence(),
        visible_text=visible_text,
        screenshot_path=screenshot_path,
        results_wait=results_wait or ResultsWaitReport(),
        notes=tuple(notes),
        extra=dict(extra or {}),
    )
