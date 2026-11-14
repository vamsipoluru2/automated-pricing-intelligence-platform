"""Croma product discovery through the real browser UI.

Drives Chromium exactly as a person would:

    homepage -> handle pincode -> click the visible search box -> type the SKU
             -> submit -> wait for results to RENDER -> pick the right product
             -> click it -> wait for the PDP -> read page.url

Deliberate non-goals, per the M1 correction:
  * never navigate to a constructed search URL;
  * never construct or guess a PDP URL — ``/p/{sku}`` is NOT assumed;
  * never call Croma's search API or reverse-engineer it;
  * never fall back to the first result;
  * never fabricate a URL on failure.

The canonical PDP URL is whatever ``page.url`` reports after the product page
opens, stored verbatim.

This module needs a live driver. Everything it *decides* (which result is correct,
what counts as an accessory) lives in pure modules that are tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from ...config.settings import Settings
from .result_identifier import (
    Confidence,
    IdentificationReport,
    ResultIdentifier,
    SearchCandidate,
    contains_model_token,
)

# Read result cards from the RENDERED DOM. Anchor-based because the PDP href shape
# is the one structural fact M0 confirmed (§2.2, twelve real URLs); the surrounding
# card text is harvested for title/category/SKU signals without assuming a class name.
_HARVEST_RESULTS_JS = """
() => {
  const seen = new Map();
  document.querySelectorAll("a[href*='/p/']").forEach(a => {
    const href = a.href || '';
    const m = href.match(/\\/p\\/(\\d+)/);
    if (!m) return;
    const id = m[1];
    // Walk up to a container that carries the card's text.
    let node = a, text = (a.innerText || '').trim();
    for (let i = 0; i < 4 && node.parentElement; i++) {
      node = node.parentElement;
      const t = (node.innerText || '').trim();
      if (t.length > text.length) text = t;
      if (text.length > 400) break;
    }
    const prev = seen.get(id);
    const entry = {
      id,
      href,
      anchor_text: (a.innerText || '').trim(),
      card_text: text.slice(0, 600),
      aria: a.getAttribute('aria-label') || '',
      title_attr: a.getAttribute('title') || ''
    };
    if (!prev || entry.card_text.length > prev.card_text.length) seen.set(id, entry);
  });
  return Array.from(seen.values());
}
"""

_PINCODE_STATE_JS = """
() => {
  const txt = (document.body.innerText || '');
  const m = txt.match(/\\b(\\d{6})\\b/);
  return { body_pincode: m ? m[1] : null, text_head: txt.slice(0, 300) };
}
"""


@dataclass
class StepEvidence:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveryResult:
    """Everything the flow observed. Persisted into the capture as evidence."""

    ok: bool = False
    failure_reason: str = ""
    steps: list[StepEvidence] = field(default_factory=list)

    pincode_requested: str = ""
    pincode_detected: str | None = None
    pincode_already_set: bool = False

    search_input_selector: str = ""
    search_submitted_text: str = ""
    results_rendered: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)

    identification: dict[str, Any] | None = None
    selected: dict[str, Any] | None = None

    pdp_url: str | None = None          # ONLY ever from page.url
    pdp_title: str = ""
    pdp_verified: bool = False
    pdp_verification: dict[str, Any] = field(default_factory=dict)

    def add(self, step: StepEvidence) -> StepEvidence:
        self.steps.append(step)
        return step

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d


class CromaDiscovery:
    def __init__(self, driver, settings: Settings | None = None) -> None:
        self.driver = driver
        self.settings = settings or Settings()

    # ------------------------------------------------------------------ helpers

    @property
    def _page(self):
        return self.driver._page  # noqa: SLF001 - discovery is a driver collaborator

    def _visible_click(self, getter, label: str, timeout: int = 5000) -> bool:
        try:
            node = getter()
            if node.count() == 0:
                return False
            node.first.click(timeout=timeout)
            self._page.wait_for_timeout(900)
            return True
        except Exception:  # noqa: BLE001 - absence/occlusion are outcomes, not errors
            return False

    # ------------------------------------------------------------------ steps

    def open_homepage(self, result: DiscoveryResult) -> bool:
        base = self.settings.croma.base_url
        nav = self.driver.goto(base)
        ok = bool(nav.ok and (nav.http_status or 0) < 400)
        try:
            self._page.wait_for_timeout(3000)
        except Exception:  # noqa: BLE001
            pass
        result.add(
            StepEvidence(
                "homepage_loaded", ok,
                f"http={nav.http_status} final={nav.final_url}",
                {"http_status": nav.http_status, "final_url": nav.final_url, "error": nav.error},
            )
        )
        return ok

    def handle_pincode(self, result: DiscoveryResult, pincode: str) -> bool:
        """Set the delivery pincode, or confirm it is already set.

        Reuses an existing value rather than reopening the dialog — a needless
        interaction is another chance to trip bot detection.
        """
        result.pincode_requested = pincode
        state = {}
        try:
            state = self._page.evaluate(_PINCODE_STATE_JS) or {}
        except Exception:  # noqa: BLE001
            pass

        if state.get("body_pincode") == pincode:
            result.pincode_already_set = True
            result.pincode_detected = pincode
            result.add(
                StepEvidence("pincode_handled", True, f"already set to {pincode}; dialog not reopened",
                             {"reused": True})
            )
            return True

        attempt = self.driver.set_location(pincode)
        try:
            after = self._page.evaluate(_PINCODE_STATE_JS) or {}
        except Exception:  # noqa: BLE001
            after = {}
        detected = after.get("body_pincode")
        result.pincode_detected = detected
        ok = detected == pincode
        result.add(
            StepEvidence(
                "pincode_handled", ok,
                f"requested={pincode} detected={detected} field_found={attempt.field_found}",
                {"attempt": attempt.to_dict(), "reused": False},
            )
        )
        return ok

    def search_via_ui(self, result: DiscoveryResult, sku: str) -> bool:
        """Type the SKU into the real search box and submit. No URL navigation."""
        page = self._page
        selectors = (
            "input#searchV2",
            "input#search",
            "input[name='search']",
            "input[placeholder*='looking for' i]",
            "input[type='search']",
        )
        used = ""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                try:
                    loc.scroll_into_view_if_needed(timeout=3000)
                except Exception:  # noqa: BLE001
                    pass
                loc.click(timeout=4000)
                page.wait_for_timeout(400)
                loc.fill("", timeout=3000)
                loc.type(str(sku), delay=90)     # human-ish keystrokes
                page.wait_for_timeout(700)
                page.keyboard.press("Enter")
                used = sel
                break
            except Exception:  # noqa: BLE001
                continue

        if not used:
            result.add(StepEvidence("search_via_ui", False, "no visible search input could be used",
                                    {"selectors_tried": list(selectors)}))
            return False

        result.search_input_selector = used
        result.search_submitted_text = str(sku)
        try:
            page.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001
            pass
        result.add(
            StepEvidence("search_via_ui", True, f"typed {sku!r} into {used!r} and pressed Enter",
                         {"selector": used, "url_after_submit": page.url})
        )
        return True

    def wait_for_rendered_results(self, result: DiscoveryResult) -> list[SearchCandidate]:
        """Wait for product cards to exist in the DOM, then read them from the DOM."""
        wait = self.driver.wait_for_results()
        rows: list[dict[str, Any]] = []
        try:
            rows = self._page.evaluate(_HARVEST_RESULTS_JS) or []
        except Exception:  # noqa: BLE001
            rows = []

        candidates: list[SearchCandidate] = []
        for i, row in enumerate(rows, start=1):
            title = row.get("card_text") or row.get("anchor_text") or row.get("aria") or row.get("title_attr") or ""
            candidates.append(
                SearchCandidate(
                    position=i,
                    title=" ".join(str(title).split()),
                    url=row.get("href", ""),
                    sku_text=str(row.get("id", "")),
                    category="",
                )
            )

        result.results_rendered = len(candidates)
        result.candidates = [c.to_dict() for c in candidates]
        result.add(
            StepEvidence(
                "results_rendered", bool(candidates),
                f"{len(candidates)} product card(s) present in the rendered DOM",
                {"results_wait": wait.to_dict(), "url": self._page.url},
            )
        )
        return candidates

    def identify(self, result: DiscoveryResult, candidates, sku: str, model: str | None) -> IdentificationReport:
        report = ResultIdentifier().identify(sku=sku, model=model, candidates=candidates)
        result.identification = report.to_dict()
        result.add(
            StepEvidence(
                "result_identified", report.ok, report.reason,
                {"confidence": report.confidence, "considered": report.candidates_considered},
            )
        )
        if report.ok and report.chosen:
            result.selected = report.chosen.to_dict()
        return report

    def open_product(self, result: DiscoveryResult, chosen: SearchCandidate) -> bool:
        """Open the chosen result by clicking it, as a person would.

        Several click strategies are tried because result cards commonly wrap the
        product link in overlays (compare checkboxes, wishlist hearts, hover layers)
        that intercept a plain click. The strategy that worked is recorded.

        The last resort follows the anchor's own href — the href **read from the
        rendered DOM**, never a constructed URL. That is what a click does anyway;
        it is recorded distinctly so the evidence stays honest.
        """
        page = self._page
        before = page.url
        pid = chosen.sku_text
        sel = f"a[href*='/p/{pid}']"
        strategy = ""

        # 1. plain click on the product anchor
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                try:
                    loc.scroll_into_view_if_needed(timeout=4000)
                    page.wait_for_timeout(400)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    loc.click(timeout=6000)
                    strategy = "anchor_click"
                except Exception:  # noqa: BLE001 - likely intercepted by an overlay
                    try:
                        loc.click(timeout=6000, force=True)
                        strategy = "anchor_force_click"
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

        # 2. dispatch a DOM click, bypassing overlay interception
        if not strategy:
            try:
                ok = page.evaluate(
                    """(sel) => { const a = document.querySelector(sel);
                                  if (!a) return false; a.click(); return true; }""",
                    sel,
                )
                if ok:
                    strategy = "dom_click"
            except Exception:  # noqa: BLE001
                pass

        if strategy:
            try:
                page.wait_for_load_state("domcontentloaded",
                                         timeout=self.settings.browser.navigation_timeout_ms)
                page.wait_for_timeout(self.settings.browser.results_settle_ms)
            except Exception:  # noqa: BLE001
                pass

        # 3. last resort: follow the anchor's own discovered href
        if page.url == before:
            href = chosen.url
            if href:
                try:
                    page.evaluate("(u) => { window.location.href = u; }", href)
                    page.wait_for_load_state("domcontentloaded",
                                             timeout=self.settings.browser.navigation_timeout_ms)
                    page.wait_for_timeout(self.settings.browser.results_settle_ms)
                    strategy = strategy or "follow_discovered_href"
                    if strategy != "follow_discovered_href":
                        strategy += "+follow_discovered_href"
                except Exception:  # noqa: BLE001
                    pass

        if not strategy:
            result.add(StepEvidence("product_opened", False,
                                    f"no click strategy succeeded for product id {pid}",
                                    {"selector": sel}))
            return False
        try:
            page.wait_for_load_state("networkidle", timeout=self.settings.browser.networkidle_timeout_ms)
        except Exception:  # noqa: BLE001 - busy analytics may never idle; not fatal
            pass
        after = page.url
        moved = after != before
        result.add(
            StepEvidence("product_opened", moved,
                         f"strategy={strategy} url {before!r} -> {after!r}",
                         {"url_before": before, "url_after": after, "strategy": strategy})
        )
        return moved

    def capture_pdp_url(self, result: DiscoveryResult) -> str:
        """Read the canonical PDP URL from the browser. Never constructed."""
        url = self._page.url
        result.pdp_url = url
        result.add(StepEvidence("pdp_url_captured", bool(url), f"page.url = {url!r}", {"source": "page.url"}))
        return url

    def verify_pdp(self, result: DiscoveryResult, sku: str, model: str | None) -> bool:
        """Confirm the opened page really is the requested product."""
        page = self._page
        try:
            info = page.evaluate(
                """() => ({
                    title: document.title || '',
                    h1: (document.querySelector('h1') || {}).innerText || '',
                    text: (document.body.innerText || '').slice(0, 6000),
                    html_len: document.documentElement.outerHTML.length
                })"""
            ) or {}
        except Exception:  # noqa: BLE001
            info = {}

        url = result.pdp_url or ""
        text = str(info.get("text", ""))
        h1 = str(info.get("h1", ""))
        title = str(info.get("title", ""))
        result.pdp_title = h1 or title

        from .result_identifier import pdp_id_from_url

        url_id = pdp_id_from_url(url)
        checks = {
            "url_is_pdp": url_id is not None,
            "url_id_matches_sku": url_id == str(sku) if url_id else False,
            "sku_present_in_page": str(sku) in text or str(sku) in url,
            "title_present": bool(h1 or title),
            "model_present": bool(model) and (contains_model_token(text, model) or contains_model_token(title, model)),
            "not_accessory": not _is_accessory_title(h1 or title),
            "has_pdp_content": _looks_like_pdp(text),
        }
        # Identity requires the URL to be a PDP, a title, non-accessory, PDP content,
        # AND at least one positive identity signal (SKU or exact model).
        identity_signal = checks["url_id_matches_sku"] or checks["sku_present_in_page"] or checks["model_present"]
        verified = (
            checks["url_is_pdp"] and checks["title_present"]
            and checks["not_accessory"] and checks["has_pdp_content"] and identity_signal
        )
        result.pdp_verified = verified
        result.pdp_verification = {**checks, "identity_signal": identity_signal, "url_id": url_id}
        result.add(
            StepEvidence("pdp_verified", verified,
                         "; ".join(f"{k}={v}" for k, v in checks.items()),
                         {"checks": checks, "h1": h1[:160], "title": title[:160]})
        )
        return verified


def _is_accessory_title(title: str) -> bool:
    from . import accessory_filter

    return accessory_filter.is_accessory(title)


def _looks_like_pdp(text: str) -> bool:
    """Heuristic that a page is a product-detail page, not a listing or error."""
    lowered = (text or "").lower()
    markers = ("add to cart", "buy now", "key features", "specifications", "product details", "overview")
    return sum(1 for m in markers if m in lowered) >= 1
