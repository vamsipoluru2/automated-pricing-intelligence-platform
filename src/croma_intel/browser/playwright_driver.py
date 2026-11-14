"""Playwright-backed browser driver.

This is the ONLY module permitted to import Playwright. The import is lazy so that
the offline core (domain, artifacts, block detection, identification) remains
importable and testable in an environment where Playwright is not installed —
which is exactly the dev-sandbox situation recorded in CLAUDE.md.

Anything that needs the network lives here. Everything downstream consumes a
``RawCapture`` and never touches this module.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..config.settings import Settings
from ..domain.capture import (
    BrowserMetadata,
    CaptureStatus,
    PageKind,
    Platform,
    RawCapture,
    ResultsWaitReport,
    ScrollReport,
    build_capture,
)
from ..domain.errors import BrowserUnavailableError, NavigationError
from .block_detector import BlockDetector, is_empty_shell


def playwright_available() -> tuple[bool, str]:
    """Report whether Playwright can be imported, without raising."""
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, "available"


@dataclass
class NavigationOutcome:
    ok: bool
    http_status: int | None
    final_url: str
    error: str | None = None


@dataclass
class LocationAttempt:
    """Result of trying to satisfy Croma's location gate on an already-loaded page.

    ``products_appeared`` is the decisive bit: it separates "Croma gates results
    behind a location" from "the empty page was a degraded anti-bot response".
    """

    performed: bool = False
    pincode: str = ""
    field_found: bool = False
    pdp_anchors_before: int = 0
    pdp_anchors_after: int = 0
    products_appeared: bool = False
    steps: tuple[str, ...] = ()
    # Terminal reason, distinct from the step trace: ``steps`` records what was
    # attempted on the page, ``detail`` says why nothing was attempted at all.
    # Mirrors BlockEvidence's (signals, detail) split.
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steps"] = list(self.steps)
        return d


class PlaywrightDriver:
    """Thin, explicit wrapper over Playwright's sync API.

    Deliberately not clever: launch, navigate, scroll, read, capture, close. Every
    interesting decision (is this a block? is this the right product?) belongs to a
    pure module that can be tested without a browser.
    """

    def __init__(self, settings: Settings | None = None, *, headless: bool | None = None) -> None:
        self.settings = settings or Settings()
        self.headless = self.settings.browser.headless if headless is None else headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._browser_version = "unknown"
        self._navigations = 0
        self._attached = False
        self._last_navigation_at: float | None = None
        self._network: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ lifecycle

    def __enter__(self) -> "PlaywrightDriver":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def start(self) -> None:
        ok, detail = playwright_available()
        if not ok:
            raise BrowserUnavailableError(
                "Playwright is not importable. Install with:\n"
                "    pip install playwright\n"
                "    playwright install chromium\n"
                f"Import error: {detail}"
            )

        from playwright.sync_api import sync_playwright

        bcfg = self.settings.browser
        self._attached = bool(bcfg.cdp_endpoint)
        try:
            self._pw = sync_playwright().start()
            launcher = getattr(self._pw, bcfg.engine, None)
            if launcher is None:
                raise BrowserUnavailableError(f"unknown browser engine {bcfg.engine!r}")
            if self._attached:
                # Attach to a browser the operator already started. It is a normal
                # Chrome process, so navigator.webdriver is false and Akamai treats
                # it as an ordinary visitor.
                self._browser = launcher.connect_over_cdp(bcfg.cdp_endpoint)
            else:
                self._browser = launcher.launch(
                    headless=self.headless,
                    slow_mo=bcfg.slow_mo_ms or 0,
                )
            self._browser_version = getattr(self._browser, "version", "unknown")
        except BrowserUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - browser binary missing, sandbox denial, etc.
            raise BrowserUnavailableError(
                f"failed to launch {bcfg.engine}: {type(exc).__name__}: {exc}\n"
                "If the browser binary is missing run: playwright install chromium"
            ) from exc

        context_kwargs: dict[str, Any] = {
            "viewport": {"width": bcfg.viewport_width, "height": bcfg.viewport_height},
            "user_agent": bcfg.user_agent,
            "locale": bcfg.locale,
            "timezone_id": bcfg.timezone_id,
        }
        state_path = Path(bcfg.storage_state_path)
        if bcfg.persist_storage_state and state_path.exists():
            context_kwargs["storage_state"] = str(state_path)

        if self._attached:
            # An attached browser already owns its context; creating a new one would
            # discard the real profile and reintroduce an automation-shaped session.
            self._context = (
                self._browser.contexts[0] if self._browser.contexts
                else self._browser.new_context(**context_kwargs)
            )
        else:
            self._context = self._browser.new_context(**context_kwargs)
        self._context.set_default_timeout(bcfg.default_timeout_ms)
        self._context.set_default_navigation_timeout(bcfg.navigation_timeout_ms)
        pages = self._context.pages if self._attached else []
        self._page = pages[0] if pages else self._context.new_page()
        self._attach_network_recorder()

    # ------------------------------------------------------------------ network

    def _attach_network_recorder(self) -> None:
        """Record XHR/fetch responses.

        M0 §17.1 required this and it was missing until the search failure could not
        be explained from the DOM alone. When a React app renders no results, the
        question "did the results API get called, and what did it answer?" is the
        only thing that separates 'API refused the client' from 'API returned empty'.

        Bodies are captured only for JSON-ish responses and are size-capped, so a
        capture stays a reasonable size.
        """
        if self._page is None:
            return
        cfg = self.settings.network

        def on_response(response) -> None:  # noqa: ANN001 - playwright type
            try:
                req = response.request
                if req.resource_type not in ("xhr", "fetch"):
                    return
                url = response.url
                if len(self._network) >= cfg.max_recorded:
                    return
                entry: dict[str, Any] = {
                    "url": url[: cfg.max_url_chars],
                    "method": req.method,
                    "status": response.status,
                    "resource_type": req.resource_type,
                    "body_snippet": None,
                    "body_length": None,
                    "content_type": (response.headers or {}).get("content-type", ""),
                }
                if cfg.capture_bodies and any(k in url.lower() for k in cfg.body_url_keywords):
                    try:
                        text = response.text()
                        entry["body_length"] = len(text)
                        entry["body_snippet"] = text[: cfg.max_body_chars]
                    except Exception:  # noqa: BLE001 - body may be unavailable
                        entry["body_snippet"] = "<unavailable>"
                self._network.append(entry)
            except Exception:  # noqa: BLE001 - recording must never break a run
                pass

        try:
            self._page.on("response", on_response)
        except Exception:  # noqa: BLE001
            pass

    def network_log(self) -> list[dict[str, Any]]:
        return list(self._network)

    def close(self) -> None:
        """Shut down cleanly, persisting session state. Never raises."""
        bcfg = self.settings.browser
        try:
            if self._context is not None and bcfg.persist_storage_state:
                state_path = Path(bcfg.storage_state_path)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                self._context.storage_state(path=str(state_path))
        except Exception:  # noqa: BLE001 - state persistence is best-effort
            pass
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._context = self._browser = self._pw = self._page = None

    # ------------------------------------------------------------------ actions

    def _enforce_rate_limit(self) -> None:
        """Space out navigations and cap them per session.

        Croma allows roughly ONE navigation per cooldown window before returning
        HTTP 403 (Akamai). Observed twice on 2026-08-13: request 1 → 200,
        request 2 → 403, both times. Debugging by re-navigating destroys the very
        access being debugged, so the driver refuses rather than relying on
        operator discipline.
        """
        cfg = self.settings.rate_limit
        if self._navigations >= cfg.max_requests_per_session:
            raise NavigationError(
                f"rate-limit guard: {self._navigations} navigations already made this session "
                f"(max {cfg.max_requests_per_session}). Croma blocks aggressively — reuse the "
                "current page or analyse a stored capture with `probe` instead."
            )
        if self._last_navigation_at is not None:
            gap = time.monotonic() - self._last_navigation_at
            if gap < cfg.min_seconds_between_requests:
                wait = cfg.min_seconds_between_requests - gap
                time.sleep(wait)
        self._navigations += 1
        self._last_navigation_at = time.monotonic()

    def goto(self, url: str, *, wait_until: str | None = None) -> NavigationOutcome:
        if self._page is None:
            raise NavigationError("driver not started; call start() first")
        self._enforce_rate_limit()
        wait_until = wait_until or self.settings.browser.wait_until
        try:
            response = self._page.goto(url, wait_until=wait_until)
            status = response.status if response is not None else None
            return NavigationOutcome(ok=True, http_status=status, final_url=self._page.url)
        except Exception as exc:  # noqa: BLE001 - timeouts, DNS, refused connections
            return NavigationOutcome(
                ok=False,
                http_status=None,
                final_url=getattr(self._page, "url", url),
                error=f"{type(exc).__name__}: {exc}",
            )

    # PDP hrefs are the ONE structural fact M0 confirmed, across twelve real URLs
    # (M0 §2.2). Waiting on this is evidence-derived, not a guessed CSS selector.
    _PDP_ANCHOR_COUNT_JS = "() => document.querySelectorAll(\"a[href*='/p/']\").length"
    _PDP_ANCHOR_PRESENT_JS = "() => document.querySelectorAll(\"a[href*='/p/']\").length > 0"

    def wait_for_results(self, *, timeout_ms: int | None = None) -> ResultsWaitReport:
        """Wait until product links actually exist in the DOM.

        The defect this fixes: ``domcontentloaded`` fires before a client-rendered
        page hydrates, and ``scroll_until_stable`` returns almost immediately on an
        empty page (height is trivially stable), so the capture recorded the
        pre-hydration shell and identification legitimately found zero candidates.

        Returns a report either way — a timeout here is evidence, not an exception.
        """
        if self._page is None:
            return ResultsWaitReport(performed=False)
        timeout_ms = timeout_ms or self.settings.browser.results_timeout_ms
        started = time.monotonic()

        def count() -> int:
            try:
                return int(self._page.evaluate(self._PDP_ANCHOR_COUNT_JS))
            except Exception:  # noqa: BLE001
                return 0

        initial = count()
        try:
            self._page.wait_for_function(self._PDP_ANCHOR_PRESENT_JS, timeout=timeout_ms)
            appeared = True
        except Exception:  # noqa: BLE001 - timeout is a legitimate outcome, not an error
            appeared = False

        # Settle: let the rest of the grid finish rendering before we snapshot.
        if appeared:
            try:
                self._page.wait_for_timeout(self.settings.browser.results_settle_ms)
            except Exception:  # noqa: BLE001
                pass

        try:
            self._page.wait_for_load_state("networkidle", timeout=self.settings.browser.networkidle_timeout_ms)
            networkidle = True
        except Exception:  # noqa: BLE001 - busy analytics can prevent idle; not fatal
            networkidle = False

        return ResultsWaitReport(
            performed=True,
            appeared=appeared,
            initial_count=initial,
            final_count=count(),
            reached_networkidle=networkidle,
            waited_ms=int((time.monotonic() - started) * 1000),
        )

    def screenshot(self, path: str | Path, *, full_page: bool = True) -> str | None:
        """Persist a screenshot. Returns the path, or None if it could not be taken."""
        if self._page is None:
            return None
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(p), full_page=full_page)
            return str(p)
        except Exception:  # noqa: BLE001 - evidence capture must never break a run
            return None

    def set_location(self, pincode: str) -> LocationAttempt:
        """Try to satisfy Croma's location gate WITHOUT navigating.

        Critical constraint: this must operate on the already-loaded page. Croma
        blocks the second navigation in a window, so navigate-then-interact costs
        the whole experiment. Everything here is same-page DOM interaction.

        Returns a report either way — failure to find the control is evidence about
        the DOM, not an error.
        """
        if self._page is None:
            return LocationAttempt(performed=False, detail="driver not started")

        page = self._page
        before = self._pdp_count()
        steps: list[str] = []

        def try_click(desc: str, fn) -> bool:
            try:
                fn()
                steps.append(f"clicked:{desc}")
                page.wait_for_timeout(1200)
                return True
            except Exception as exc:  # noqa: BLE001
                steps.append(f"failed:{desc}({type(exc).__name__})")
                return False

        # 1. Surface the location widget if it is behind a trigger.
        for label in ("Add Pincode", "Update pincode", "Select your location", "Deliver to"):
            try:
                node = page.get_by_text(label, exact=False).first
                if node.count() > 0 and node.is_visible():
                    if try_click(f"text:{label}", lambda n=node: n.click(timeout=4000)):
                        break
            except Exception as exc:  # noqa: BLE001
                steps.append(f"failed:locate:{label}({type(exc).__name__})")

        # 2. Fill the pincode field.
        filled = False
        for sel in ("input[placeholder*='Pincode' i]", "input[placeholder*='pin code' i]", "input[name='number']"):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(pincode, timeout=4000)
                    steps.append(f"filled:{sel}")
                    filled = True
                    break
            except Exception as exc:  # noqa: BLE001
                steps.append(f"failed:fill:{sel}({type(exc).__name__})")

        # 3. Submit.
        if filled:
            submitted = False
            for label in ("Continue", "Confirm", "Apply", "Submit", "Done", "Check"):
                try:
                    btn = page.get_by_role("button", name=label, exact=False).first
                    if btn.count() > 0 and btn.is_visible():
                        if try_click(f"button:{label}", lambda b=btn: b.click(timeout=4000)):
                            submitted = True
                            break
                except Exception as exc:  # noqa: BLE001
                    steps.append(f"failed:btn:{label}({type(exc).__name__})")
            if not submitted:
                try:
                    page.keyboard.press("Enter")
                    steps.append("pressed:Enter")
                except Exception as exc:  # noqa: BLE001
                    steps.append(f"failed:Enter({type(exc).__name__})")

        # 4. Give the app time to refetch and render.
        try:
            page.wait_for_timeout(self.settings.browser.results_settle_ms)
            page.wait_for_function(
                self._PDP_ANCHOR_PRESENT_JS,
                timeout=self.settings.browser.results_timeout_ms,
            )
        except Exception:  # noqa: BLE001 - timeout is an outcome
            pass

        after = self._pdp_count()
        return LocationAttempt(
            performed=True,
            pincode=pincode,
            field_found=filled,
            pdp_anchors_before=before,
            pdp_anchors_after=after,
            products_appeared=after > before,
            steps=tuple(steps),
        )

    def _pdp_count(self) -> int:
        try:
            return int(self._page.evaluate(self._PDP_ANCHOR_COUNT_JS))
        except Exception:  # noqa: BLE001
            return 0

    def visible_text(self) -> str:
        """document.body.innerText — what a human actually sees."""
        if self._page is None:
            return ""
        try:
            return self._page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:  # noqa: BLE001
            return ""

    def input_values(self) -> list[dict[str, str]]:
        """Read every text input's value — proves what the search box actually contains."""
        if self._page is None:
            return []
        try:
            return self._page.evaluate(
                """() => Array.from(document.querySelectorAll('input'))
                        .filter(i => ['text','search',''].includes((i.type||'').toLowerCase()))
                        .slice(0, 20)
                        .map(i => ({
                            name: i.name || '', id: i.id || '',
                            placeholder: i.placeholder || '', value: i.value || ''
                        }))"""
            ) or []
        except Exception:  # noqa: BLE001
            return []

    def scroll_until_stable(self) -> ScrollReport:
        """Controlled scrolling until page height stabilises.

        Returns a report either way — the evidence that distinguishes "content is
        absent" from "content had not loaded yet".
        """
        if self._page is None:
            return ScrollReport(performed=False)
        cfg = self.settings.scroll
        started = time.monotonic()

        def height() -> int:
            try:
                return int(self._page.evaluate("document.body ? document.body.scrollHeight : 0"))
            except Exception:  # noqa: BLE001
                return 0

        initial = height()
        last = initial
        stable_streak = 0
        scrolls = 0

        for _ in range(cfg.max_scrolls):
            if (time.monotonic() - started) * 1000 > cfg.max_duration_ms:
                break
            try:
                self._page.mouse.wheel(0, self.settings.browser.viewport_height)
            except Exception:  # noqa: BLE001
                break
            scrolls += 1
            self._page.wait_for_timeout(cfg.settle_ms)
            current = height()
            if current == last:
                stable_streak += 1
                if stable_streak >= cfg.stability_checks:
                    break
            else:
                stable_streak = 0
            last = current

        return ScrollReport(
            performed=True,
            scroll_count=scrolls,
            initial_height_px=initial,
            final_height_px=last,
            height_changed=last != initial,
            reached_stable=stable_streak >= cfg.stability_checks,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def title(self) -> str:
        try:
            return self._page.title() if self._page else ""
        except Exception:  # noqa: BLE001
            return ""

    def content(self) -> str:
        try:
            return self._page.content() if self._page else ""
        except Exception:  # noqa: BLE001
            return ""

    def url(self) -> str:
        return getattr(self._page, "url", "") if self._page else ""

    def query_all_attrs(self, selector: str, attrs: tuple[str, ...]) -> list[dict[str, str]]:
        """Read attributes/text from matching elements. Returns [] on any failure."""
        if self._page is None:
            return []
        try:
            handles = self._page.query_selector_all(selector)
        except Exception:  # noqa: BLE001
            return []
        rows: list[dict[str, str]] = []
        for h in handles:
            row: dict[str, str] = {}
            for attr in attrs:
                try:
                    row[attr] = (h.inner_text() if attr == "text" else h.get_attribute(attr)) or ""
                except Exception:  # noqa: BLE001
                    row[attr] = ""
            rows.append(row)
        return rows

    def browser_metadata(self) -> BrowserMetadata:
        bcfg = self.settings.browser
        return BrowserMetadata(
            engine=bcfg.engine,
            browser_version=str(self._browser_version),
            headless=self.headless,
            user_agent=bcfg.user_agent,
            viewport_width=bcfg.viewport_width,
            viewport_height=bcfg.viewport_height,
            locale=bcfg.locale,
            timezone_id=bcfg.timezone_id,
            proxy_used=False,
        )

    # ------------------------------------------------------------------ capture

    def capture(
        self,
        *,
        page_kind: PageKind,
        requested_url: str,
        nav: NavigationOutcome,
        input_sku: str | None = None,
        scroll: ScrollReport | None = None,
        results_wait: ResultsWaitReport | None = None,
        screenshot_label: str | None = None,
        notes: tuple[str, ...] = (),
        extra: dict[str, Any] | None = None,
    ) -> RawCapture:
        """Build an immutable capture from current page state, classifying the outcome."""
        html = self.content()
        title = self.title()
        visible = self.visible_text()

        shot_path: str | None = None
        if self.settings.browser.capture_screenshot and screenshot_label:
            shot_path = self.screenshot(
                Path(self.settings.browser.screenshot_dir) / f"{screenshot_label}.png"
            )
        detector = BlockDetector(
            threshold=self.settings.block.threshold,
            content_markers=self.settings.croma.content_markers,
        )
        block = detector.assess(html=html, title=title, http_status=nav.http_status)

        if not nav.ok:
            status = CaptureStatus.NAVIGATION_FAILED
        elif block.is_blocked:
            status = CaptureStatus.BLOCKED
        elif is_empty_shell(
            html,
            content_markers=self.settings.croma.content_markers,
            min_length=self.settings.block.empty_shell_min_length,
        ):
            status = CaptureStatus.EMPTY_SHELL
        else:
            status = CaptureStatus.SUCCESS

        all_notes = tuple(notes)
        if nav.error:
            all_notes = all_notes + (f"navigation_error: {nav.error}",)

        return build_capture(
            platform=Platform.CROMA.value,
            page_kind=page_kind.value,
            requested_url=requested_url,
            final_url=nav.final_url or self.url() or requested_url,
            status=status.value,
            html=html,
            page_title=title,
            input_sku=input_sku,
            http_status=nav.http_status,
            browser=self.browser_metadata(),
            scroll=scroll or ScrollReport(),
            block=block,
            visible_text=visible,
            screenshot_path=shot_path,
            results_wait=results_wait or ResultsWaitReport(),
            notes=all_notes,
            extra=extra or {},
        )
