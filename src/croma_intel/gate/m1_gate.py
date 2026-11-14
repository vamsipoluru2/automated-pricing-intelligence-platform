"""M1 Browser Access Gate — real human browser discovery flow.

Proves exactly one chain, driven through the visible UI:

    homepage -> pincode -> click search box -> type SKU -> submit
             -> results RENDER -> identify correct product -> click
             -> PDP opens -> read page.url -> capture -> replay offline

Hard rules (M1 correction):
  * the gate takes a SKU, never a URL;
  * the PDP URL is whatever ``page.url`` reports, never constructed;
  * ``/p/{sku}`` is NOT assumed to be the product URL;
  * no direct search URL, no search API, no reverse engineering;
  * never the first result; accessories are hard-rejected before ranking;
  * on failure, no URL is fabricated — the reason is explicit.

Out of scope: competitor scrapers, matching, ranking, derivative detection,
specification extraction, Excel, LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..browser.artifacts import CaptureStore
from ..browser.dom_probe import diagnose_zero_results, probe_html
from ..browser.playwright_driver import (
    NavigationOutcome,
    PlaywrightDriver,
    playwright_available,
)
from ..config.settings import Settings
from ..domain.capture import CaptureStatus, PageKind, utc_now_iso
from ..domain.errors import BrowserUnavailableError
from ..platforms.croma.discovery import CromaDiscovery, DiscoveryResult


class GateOutcome:
    GO = "GO"
    NO_GO = "NO_GO"
    INCONCLUSIVE = "INCONCLUSIVE"  # environment prevented the test from running


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateReport:
    outcome: str
    sku: str
    model: str | None
    started_at: str
    finished_at: str = ""
    steps: list[StepResult] = field(default_factory=list)
    search_capture_id: str | None = None
    pdp_capture_id: str | None = None
    pdp_url: str | None = None
    pincode: str | None = None
    identification: dict[str, Any] | None = None
    discovery: dict[str, Any] | None = None
    dom_probe: dict[str, Any] | None = None
    root_cause: str | None = None
    summary: str = ""

    def add(self, step: StepResult) -> StepResult:
        self.steps.append(step)
        return step

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "sku": self.sku,
            "model": self.model,
            "pincode": self.pincode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pdp_url": self.pdp_url,
            "search_capture_id": self.search_capture_id,
            "pdp_capture_id": self.pdp_capture_id,
            "identification": self.identification,
            "discovery": self.discovery,
            "dom_probe": self.dom_probe,
            "root_cause": self.root_cause,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True, ensure_ascii=False)
        return p


def _fail(report: GateReport, driver, store, result: DiscoveryResult,
          sku: str, reason: str, outcome: str) -> GateReport:
    """Structured failure. Captures evidence and NEVER fabricates a PDP URL."""
    try:
        cap = driver.capture(
            page_kind=PageKind.OTHER,
            requested_url=driver.url(),
            nav=NavigationOutcome(True, None, driver.url()),
            input_sku=str(sku),
            screenshot_label=f"failure_{sku}",
            extra={"stage": "failure", "discovery": result.to_dict(), "network": driver.network_log()},
        )
        store.write(cap)
        report.add(StepResult("failure_evidence_captured", True, f"capture_id={cap.capture_id}"))
    except Exception as exc:  # noqa: BLE001 - evidence capture is best-effort
        report.add(StepResult("failure_evidence_captured", False, f"{type(exc).__name__}: {exc}"))

    result.ok = False
    result.failure_reason = reason
    report.discovery = result.to_dict()
    report.pdp_url = None  # explicit: no fabricated URL, ever
    report.outcome = outcome
    report.summary = reason
    report.finished_at = utc_now_iso()
    return report


def run_m1_gate(
    *,
    sku: str,
    model: str | None = None,
    settings: Settings | None = None,
    headless: bool | None = None,
) -> GateReport:
    settings = settings or Settings()
    pincode = settings.croma.test_pincode
    report = GateReport(
        outcome=GateOutcome.INCONCLUSIVE,
        sku=str(sku),
        model=model,
        pincode=pincode,
        started_at=utc_now_iso(),
    )
    store = CaptureStore(settings.storage.captures_dir)

    available, detail = playwright_available()
    report.add(StepResult("playwright_available", available, detail,
                          {"install_hint": "pip install -e '.[browser]' && playwright install chromium"}))
    if not available:
        report.outcome = GateOutcome.INCONCLUSIVE
        report.summary = ("Playwright is not installed, so the gate could not run. "
                          "ENVIRONMENT limitation, not evidence about Croma.")
        report.finished_at = utc_now_iso()
        return report

    driver = PlaywrightDriver(settings, headless=headless)
    result = DiscoveryResult()

    try:
        try:
            driver.start()
            report.add(StepResult("browser_launch", True, f"{settings.browser.engine} launched",
                                  driver.browser_metadata().to_dict()))
        except BrowserUnavailableError as exc:
            report.add(StepResult("browser_launch", False, str(exc)))
            report.outcome = GateOutcome.INCONCLUSIVE
            report.summary = "Browser could not launch. ENVIRONMENT limitation."
            report.finished_at = utc_now_iso()
            return report

        discovery = CromaDiscovery(driver, settings)

        # --- 1. homepage ------------------------------------------------------
        if not discovery.open_homepage(result):
            return _fail(report, driver, store, result, sku,
                         "Croma homepage did not load", GateOutcome.NO_GO)
        home_capture = driver.capture(
            page_kind=PageKind.OTHER, requested_url=settings.croma.base_url,
            nav=NavigationOutcome(True, None, driver.url()), input_sku=str(sku),
            screenshot_label=f"home_{sku}", extra={"stage": "homepage"},
        )
        store.write(home_capture)
        report.add(StepResult("homepage", home_capture.is_success,
                              f"status={home_capture.status} title={home_capture.page_title[:60]!r}",
                              {"capture_id": home_capture.capture_id}))
        if home_capture.status == CaptureStatus.BLOCKED.value:
            return _fail(report, driver, store, result, sku,
                         f"homepage BLOCKED ({home_capture.block.decisive_signal})", GateOutcome.NO_GO)

        # --- 2. pincode -------------------------------------------------------
        pin_ok = discovery.handle_pincode(result, pincode)
        report.add(StepResult("pincode", pin_ok,
                              f"requested={pincode} detected={result.pincode_detected} "
                              f"reused={result.pincode_already_set}"))
        if not pin_ok:
            return _fail(report, driver, store, result, sku,
                         f"pincode {pincode} could not be set or verified", GateOutcome.NO_GO)

        # --- 3. search through the visible UI ---------------------------------
        if not discovery.search_via_ui(result, str(sku)):
            return _fail(report, driver, store, result, sku,
                         "no usable visible search input on the Croma homepage", GateOutcome.NO_GO)
        report.add(StepResult("search_via_ui", True,
                              f"typed {sku!r} into {result.search_input_selector!r}"))

        # --- 4. results must RENDER -------------------------------------------
        candidates = discovery.wait_for_rendered_results(result)
        search_capture = driver.capture(
            page_kind=PageKind.SEARCH, requested_url=driver.url(),
            nav=NavigationOutcome(True, None, driver.url()), input_sku=str(sku),
            screenshot_label=f"search_{sku}",
            extra={"stage": "search_ui", "discovery": result.to_dict(), "network": driver.network_log()},
        )
        store.write(search_capture)
        report.search_capture_id = search_capture.capture_id
        report.add(StepResult("results_rendered", bool(candidates),
                              f"{len(candidates)} product card(s) in the rendered DOM",
                              {"capture_id": search_capture.capture_id}))
        if not candidates:
            probe = probe_html(search_capture.html,
                               tokens=[str(sku)] + ([model] if model else []),
                               visible_text=search_capture.visible_text or None)
            report.dom_probe = probe.to_dict()
            report.root_cause = diagnose_zero_results(probe, sku=str(sku), model=model)
            return _fail(report, driver, store, result, sku,
                         f"search results did not render. ROOT CAUSE: {report.root_cause}",
                         GateOutcome.NO_GO)

        # --- 5. identify (never the first result) -----------------------------
        ident = discovery.identify(result, candidates, str(sku), model)
        report.identification = result.identification
        report.add(StepResult("result_identified", ident.ok, ident.reason,
                              {"confidence": ident.confidence}))
        if not ident.ok or ident.chosen is None:
            return _fail(report, driver, store, result, sku,
                         f"correct product not identified ({ident.confidence}): {ident.reason}",
                         GateOutcome.NO_GO)

        # --- 6. open the product ----------------------------------------------
        if not discovery.open_product(result, ident.chosen):
            return _fail(report, driver, store, result, sku,
                         "could not open the identified product result", GateOutcome.NO_GO)

        # --- 7. canonical PDP URL from the browser ----------------------------
        pdp_url = discovery.capture_pdp_url(result)
        report.pdp_url = pdp_url
        report.add(StepResult("pdp_url_captured", bool(pdp_url),
                              f"page.url={pdp_url!r}", {"source": "page.url"}))

        # --- 8. verify identity + capture the artifact ------------------------
        verified = discovery.verify_pdp(result, str(sku), model)
        pdp_capture = driver.capture(
            page_kind=PageKind.PDP, requested_url=pdp_url,
            nav=NavigationOutcome(True, None, pdp_url), input_sku=str(sku),
            scroll=driver.scroll_until_stable(), screenshot_label=f"pdp_{sku}",
            extra={"stage": "pdp", "discovery": result.to_dict(), "requested_model": model,
                   "pincode": pincode, "pdp_url_source": "page.url", "network": driver.network_log()},
        )
        store.write(pdp_capture)
        report.pdp_capture_id = pdp_capture.capture_id
        report.add(StepResult("pdp_verified", verified,
                              "; ".join(f"{k}={v}" for k, v in result.pdp_verification.items()),
                              {"capture_id": pdp_capture.capture_id, "pdp_url": pdp_url}))
        if pdp_capture.status == CaptureStatus.BLOCKED.value:
            return _fail(report, driver, store, result, sku,
                         f"PDP BLOCKED ({pdp_capture.block.decisive_signal})", GateOutcome.NO_GO)
        if not verified:
            return _fail(report, driver, store, result, sku,
                         "opened page failed product-identity verification", GateOutcome.NO_GO)

        # --- 9. offline replay guarantee --------------------------------------
        replayed = store.read(pdp_capture.capture_id, verify=True)
        ok_integrity, integrity_reason = replayed.verify_integrity()
        report.add(StepResult("offline_replay", ok_integrity, integrity_reason))

        result.ok = ok_integrity
        report.discovery = result.to_dict()
        report.outcome = GateOutcome.GO if ok_integrity else GateOutcome.NO_GO
        report.summary = (
            f"homepage -> pincode {pincode} -> UI search {sku} -> {len(candidates)} results "
            f"-> identified -> PDP {pdp_url} -> captured -> replayed offline"
            if ok_integrity else f"capture replay failed integrity: {integrity_reason}"
        )
        report.finished_at = utc_now_iso()
        return report

    finally:
        driver.close()
