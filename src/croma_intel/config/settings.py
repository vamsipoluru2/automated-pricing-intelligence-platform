"""Configuration. No magic numbers in code — every tunable lives here.

JSON rather than YAML for M1: the dev sandbox cannot install PyYAML (no network),
and the offline core is deliberately dependency-free. Migrate to YAML when
dependencies are installable; the shape is unchanged.

Layering: dataclass defaults -> optional JSON file -> explicit overrides.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class BrowserConfig:
    engine: str = "chromium"
    headless: bool = True
    navigation_timeout_ms: int = 45_000
    default_timeout_ms: int = 30_000
    viewport_width: int = 1440
    viewport_height: int = 900
    locale: str = "en-IN"
    timezone_id: str = "Asia/Kolkata"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    storage_state_path: str = "artifacts/state/croma_storage_state.json"
    persist_storage_state: bool = True
    slow_mo_ms: int = 0

    #: When set, attach to an already-running Chrome over the DevTools protocol
    #: instead of launching a fresh automated browser.
    #:
    #: Confirmed 2026-08-27: Croma's Akamai edge returns HTTP 403 to a
    #: Playwright-launched browser because it reports ``navigator.webdriver ===
    #: true``. The same machine and IP loads Croma normally in an ordinary Chrome
    #: window, so this is automation detection, not a rate limit or an IP block.
    #: Attaching to a real browser is a legitimate use of an existing session
    #: rather than an attempt to disguise one.
    cdp_endpoint: str = ""

    # --- capture timing -----------------------------------------------------
    # "domcontentloaded" fires before a client-rendered page hydrates. The first
    # live run captured the pre-hydration shell and identification legitimately
    # found zero candidates. These control waiting for content to actually exist.
    wait_until: str = "domcontentloaded"
    results_timeout_ms: int = 20_000       # how long to wait for product links
    results_settle_ms: int = 750           # let the grid finish after the first link
    networkidle_timeout_ms: int = 10_000   # backstop; busy analytics may never idle
    capture_screenshot: bool = True
    screenshot_dir: str = "artifacts/screenshots"


@dataclass(frozen=True)
class ScrollConfig:
    max_scrolls: int = 30
    settle_ms: int = 350
    stability_checks: int = 3
    max_duration_ms: int = 45_000
    step: str = "viewport"


@dataclass(frozen=True)
class NetworkConfig:
    """XHR/fetch recording. Required by M0 §17.1; the preferred extraction source.

    When a client-rendered page shows no results, the network log is the only
    evidence that distinguishes "the results API refused us" from "the results API
    returned an empty set".
    """

    record: bool = True
    capture_bodies: bool = True
    max_recorded: int = 120
    max_body_chars: int = 4000
    max_url_chars: int = 400
    # Only bodies from URLs containing these are captured, to keep artifacts small.
    body_url_keywords: tuple[str, ...] = (
        "search", "product", "plp", "catalog", "occ", "api", "graphql", "solr", "query",
    )


@dataclass(frozen=True)
class RateLimitConfig:
    """Croma applies progressive, rate-based bot defense (Akamai).

    Observed live 2026-08-13: request 1 returned HTTP 200 with content; requests 2
    and 3, within minutes, returned HTTP 403 "Access Denied" with an
    errors.edgesuite.net reference. Repeated probing makes the block worse, so the
    defaults here are deliberately conservative.
    """

    min_seconds_between_requests: float = 20.0
    max_requests_per_session: int = 10
    cooldown_seconds_after_block: float = 900.0   # 15 min before retrying after a 403
    abort_run_after_consecutive_blocks: int = 2


@dataclass(frozen=True)
class CromaConfig:
    base_url: str = "https://www.croma.com"
    search_path: str = "/search/?text={query}"
    test_pincode: str = "560087"
    attempt_pincode: bool = True
    # Strings that indicate legitimate Croma content. Used to suppress weak
    # size-based block signals so a slim real page is not misread as a wall.
    content_markers: tuple[str, ...] = (
        "croma",
        "add to cart",
        "product details",
        "/p/",
    )
    max_search_candidates: int = 40


@dataclass(frozen=True)
class StorageConfig:
    artifacts_root: str = "artifacts"
    captures_dir: str = "artifacts/captures"
    reports_dir: str = "artifacts/reports"


@dataclass(frozen=True)
class BlockConfig:
    threshold: float = 1.0
    empty_shell_min_length: int = 5000


@dataclass(frozen=True)
class Settings:
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    scroll: ScrollConfig = field(default_factory=ScrollConfig)
    croma: CromaConfig = field(default_factory=CromaConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    block: BlockConfig = field(default_factory=BlockConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "Settings":
        def build(cls, key):
            known = {f for f in cls.__dataclass_fields__}
            raw = dict(data.get(key, {}) or {})
            filtered = {k: v for k, v in raw.items() if k in known}
            # tuple fields must not arrive as lists or frozen-dataclass equality breaks
            for k, v in list(filtered.items()):
                if isinstance(getattr(cls(), k, None), tuple) and isinstance(v, list):
                    filtered[k] = tuple(v)
            return cls(**filtered)

        return Settings(
            browser=build(BrowserConfig, "browser"),
            scroll=build(ScrollConfig, "scroll"),
            croma=build(CromaConfig, "croma"),
            storage=build(StorageConfig, "storage"),
            block=build(BlockConfig, "block"),
            rate_limit=build(RateLimitConfig, "rate_limit"),
            network=build(NetworkConfig, "network"),
        )

    @staticmethod
    def load(path: str | Path | None = None) -> "Settings":
        """Load settings, falling back to defaults when no file is present."""
        if path is None:
            return Settings()
        p = Path(path)
        if not p.exists():
            return Settings()
        with open(p, "r", encoding="utf-8") as fh:
            return Settings.from_dict(json.load(fh))

    def search_url(self, query: str) -> str:
        from urllib.parse import quote

        return self.croma.base_url.rstrip("/") + self.croma.search_path.format(query=quote(str(query)))
