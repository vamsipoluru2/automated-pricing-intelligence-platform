"""M1 correction — human browser discovery flow, tests A–M.

Driven against a scripted fake page so the whole flow is verifiable offline with no
browser and no network. The fake models the parts of Playwright the flow touches.
"""

from __future__ import annotations

import unittest

from croma_intel.config.settings import Settings
from croma_intel.platforms.croma import accessory_filter
from croma_intel.platforms.croma.discovery import CromaDiscovery, DiscoveryResult
from croma_intel.platforms.croma.result_identifier import Confidence, SearchCandidate

SKU = "323360"
MODEL = "83S000FYIN"
PIN = "560087"

TARGET_TITLE = (
    "Lenovo LOQ 15ARP10E AMD Ryzen 5 Gaming Laptop (16GB, 512GB SSD, Windows 11 Home, "
    "6GB Graphics, 15.6 inch 144 Hz Full HD IPS Display, NVIDIA GeForce RTX 3050)"
)


# --------------------------------------------------------------------- fakes


class FakeLocator:
    def __init__(self, page, selector, n=1):
        self._page, self._selector, self._n = page, selector, n

    def count(self):
        return self._n

    @property
    def first(self):
        return self

    def click(self, timeout=0):
        self._page.log.append(f"click:{self._selector}")
        self._page.on_click(self._selector)

    def fill(self, value, timeout=0):
        self._page.log.append(f"fill:{self._selector}={value}")
        self._page.field_value = value

    def type(self, value, delay=0):
        self._page.log.append(f"type:{self._selector}={value}")
        self._page.field_value = value

    def scroll_into_view_if_needed(self, timeout=0):
        pass

    def input_value(self):
        return self._page.field_value


class FakePage:
    """Scripted page. `results` is what the DOM harvest returns after a search."""

    def __init__(self, *, results=None, pincode_in_body=None, has_search=True,
                 pdp_url="https://www.croma.com/lenovo-loq-15arp10e/p/323360",
                 pdp_text="Add to Cart Specifications 323360 " + TARGET_TITLE,
                 pdp_h1=TARGET_TITLE, click_opens_pdp=True):
        self.url = "https://www.croma.com"
        self.log: list[str] = []
        self.field_value = ""
        self._results = results if results is not None else []
        self._pincode = pincode_in_body
        self._has_search = has_search
        self._pdp_url = pdp_url
        self._pdp_text = pdp_text
        self._pdp_h1 = pdp_h1
        self._click_opens_pdp = click_opens_pdp
        self.searched = False

    # -- playwright surface used by the flow
    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, state, timeout=0):
        pass

    def keyboard_press(self, key):
        pass

    @property
    def keyboard(self):
        page = self

        class K:
            def press(self, key):
                page.log.append(f"press:{key}")
                if key == "Enter":
                    page.searched = True
                    page.url = "https://www.croma.com/searchB2C?q=" + page.field_value
        return K()

    def locator(self, selector):
        if "input" in selector and not self._has_search:
            return FakeLocator(self, selector, n=0)
        if selector.startswith("a[href*="):
            return FakeLocator(self, selector, n=1)
        return FakeLocator(self, selector, n=1)

    def get_by_text(self, text, exact=False):
        return FakeLocator(self, f"text:{text}", n=0)

    def get_by_role(self, role, name="", exact=False):
        return FakeLocator(self, f"role:{role}:{name}", n=0)

    def on_click(self, selector):
        if selector.startswith("a[href*=") and self._click_opens_pdp:
            self.url = self._pdp_url

    def evaluate(self, script):
        if "seen" in script and "/p/" in script:          # result harvest
            return self._results if self.searched else []
        if "body_pincode" in script:
            return {"body_pincode": self._pincode, "text_head": ""}
        if "html_len" in script:                           # pdp verification
            return {"title": self._pdp_h1, "h1": self._pdp_h1,
                    "text": self._pdp_text, "html_len": 50000}
        return {}


class FakeDriver:
    def __init__(self, page, *, nav_status=200):
        self._page = page
        self._nav_status = nav_status
        self.location_calls: list[str] = []

    def goto(self, url, wait_until=None):
        from croma_intel.browser.playwright_driver import NavigationOutcome
        self._page.url = url
        return NavigationOutcome(ok=True, http_status=self._nav_status, final_url=url)

    def url(self):
        return self._page.url

    def wait_for_results(self):
        from croma_intel.domain.capture import ResultsWaitReport
        n = len(self._page._results) if self._page.searched else 0
        return ResultsWaitReport(performed=True, appeared=n > 0, final_count=n)

    def set_location(self, pincode):
        from croma_intel.browser.playwright_driver import LocationAttempt
        self.location_calls.append(pincode)
        self._page._pincode = pincode
        return LocationAttempt(performed=True, pincode=pincode, field_found=True)

    def network_log(self):
        return []


def card(pid, title):
    return {"id": pid, "href": f"https://www.croma.com/x/p/{pid}",
            "anchor_text": title, "card_text": title, "aria": "", "title_attr": ""}


def make(results=None, **kw):
    page = FakePage(results=results, **kw)
    return page, CromaDiscovery(FakeDriver(page), Settings()), DiscoveryResult()


# --------------------------------------------------------------------- tests


class TestA_Homepage(unittest.TestCase):
    def test_homepage_opens(self):
        page, d, r = make()
        self.assertTrue(d.open_homepage(r))
        self.assertEqual(r.steps[0].name, "homepage_loaded")
        self.assertTrue(r.steps[0].ok)


class TestB_PincodeDialogHandled(unittest.TestCase):
    def test_pincode_dialog_is_handled(self):
        page, d, r = make(pincode_in_body=None)
        self.assertTrue(d.handle_pincode(r, PIN))
        self.assertEqual(r.pincode_detected, PIN)
        self.assertFalse(r.pincode_already_set)

    def test_failure_when_pincode_not_reflected(self):
        page, d, r = make(pincode_in_body=None)
        d.driver.set_location = lambda p: __import__(
            "croma_intel.browser.playwright_driver", fromlist=["LocationAttempt"]
        ).LocationAttempt(performed=True, pincode=p, field_found=False)
        self.assertFalse(d.handle_pincode(r, PIN))


class TestC_PincodeReused(unittest.TestCase):
    def test_already_set_pincode_is_reused(self):
        page, d, r = make(pincode_in_body=PIN)
        self.assertTrue(d.handle_pincode(r, PIN))
        self.assertTrue(r.pincode_already_set)
        self.assertEqual(d.driver.location_calls, [], "must not reopen the pincode dialog")


class TestD_SearchThroughUI(unittest.TestCase):
    def test_search_uses_visible_input_and_enter(self):
        page, d, r = make()
        self.assertTrue(d.search_via_ui(r, SKU))
        self.assertIn(f"type:input#searchV2={SKU}", page.log)
        self.assertIn("press:Enter", page.log)
        self.assertEqual(r.search_submitted_text, SKU)

    def test_no_search_input_is_a_structured_failure(self):
        page, d, r = make(has_search=False)
        self.assertFalse(d.search_via_ui(r, SKU))
        self.assertFalse(r.steps[-1].ok)


class TestE_ResultsRendered(unittest.TestCase):
    def test_results_read_from_rendered_dom(self):
        page, d, r = make(results=[card("111111", "HP Victus Gaming Laptop"),
                                   card(SKU, TARGET_TITLE)])
        d.search_via_ui(r, SKU)
        cands = d.wait_for_rendered_results(r)
        self.assertEqual(len(cands), 2)
        self.assertEqual(r.results_rendered, 2)

    def test_zero_results_reported_not_invented(self):
        page, d, r = make(results=[])
        d.search_via_ui(r, SKU)
        self.assertEqual(d.wait_for_rendered_results(r), [])
        self.assertFalse(r.steps[-1].ok)


class TestF_CorrectResultSelected(unittest.TestCase):
    def test_selects_the_sku_match(self):
        page, d, r = make(results=[card("111111", "HP Victus Gaming Laptop 16GB SSD"),
                                   card(SKU, TARGET_TITLE)])
        d.search_via_ui(r, SKU)
        rep = d.identify(r, d.wait_for_rendered_results(r), SKU, MODEL)
        self.assertEqual(rep.confidence, Confidence.CONFIDENT.value)
        self.assertEqual(rep.chosen.sku_text, SKU)


class TestG_FirstResultDecoyRejected(unittest.TestCase):
    def test_first_result_is_not_chosen(self):
        page, d, r = make(results=[
            card("999999", "Lenovo LOQ Gaming Laptop 16GB 512GB SSD RTX 3050"),
            card("888888", "Lenovo LOQ 15 Gaming Laptop RTX 3050 SSD"),
            card(SKU, TARGET_TITLE),
        ])
        d.search_via_ui(r, SKU)
        cands = d.wait_for_rendered_results(r)
        rep = d.identify(r, cands, SKU, MODEL)
        self.assertEqual(rep.chosen.position, 3)
        self.assertNotEqual(rep.chosen.position, 1)


class TestH_AccessoryRejected(unittest.TestCase):
    def test_accessory_never_selected_even_with_matching_model(self):
        page, d, r = make(results=[
            card("777777", f"Laptop Sleeve for Lenovo LOQ {MODEL} 15.6 inch"),
            card(SKU, TARGET_TITLE),
        ])
        d.search_via_ui(r, SKU)
        rep = d.identify(r, d.wait_for_rendered_results(r), SKU, MODEL)
        self.assertEqual(rep.chosen.sku_text, SKU)
        rejected = [a for a in rep.assessments if a.rejected_as_accessory]
        self.assertEqual(len(rejected), 1)

    def test_accessory_only_results_yield_not_found(self):
        page, d, r = make(results=[
            card("777777", f"Laptop Bag for Lenovo LOQ {MODEL}"),
            card("666666", "Lenovo 65W Charger Adapter compatible with LOQ"),
        ])
        d.search_via_ui(r, SKU)
        rep = d.identify(r, d.wait_for_rendered_results(r), SKU, MODEL)
        self.assertEqual(rep.confidence, Confidence.NOT_FOUND.value)
        self.assertIsNone(rep.chosen)

    def test_laptop_with_keyboard_feature_is_not_an_accessory(self):
        title = "Lenovo LOQ Gaming Laptop 16GB 512GB SSD RTX 3050 with RGB Backlit Keyboard"
        self.assertFalse(accessory_filter.is_accessory(title))


class TestI_ProductPageOpens(unittest.TestCase):
    def test_click_opens_product(self):
        page, d, r = make(results=[card(SKU, TARGET_TITLE)])
        d.search_via_ui(r, SKU)
        cands = d.wait_for_rendered_results(r)
        self.assertTrue(d.open_product(r, cands[0]))

    def test_no_navigation_is_a_failure(self):
        page, d, r = make(results=[card(SKU, TARGET_TITLE)], click_opens_pdp=False)
        d.search_via_ui(r, SKU)
        cands = d.wait_for_rendered_results(r)
        self.assertFalse(d.open_product(r, cands[0]))


class TestJ_FinalUrlFromPageUrl(unittest.TestCase):
    def test_pdp_url_comes_from_page_url_not_constructed(self):
        discovered = "https://www.croma.com/some-slug-we-did-not-predict/p/323360"
        page, d, r = make(results=[card(SKU, TARGET_TITLE)], pdp_url=discovered)
        d.search_via_ui(r, SKU)
        cands = d.wait_for_rendered_results(r)
        d.open_product(r, cands[0])
        self.assertEqual(d.capture_pdp_url(r), discovered)
        self.assertEqual(r.pdp_url, discovered)
        self.assertEqual(r.steps[-1].data["source"], "page.url")

    def test_url_is_stored_verbatim(self):
        odd = "https://www.croma.com/a-b-c/p/323360?utm_source=x"
        page, d, r = make(results=[card(SKU, TARGET_TITLE)], pdp_url=odd)
        d.search_via_ui(r, SKU)
        d.open_product(r, d.wait_for_rendered_results(r)[0])
        self.assertEqual(d.capture_pdp_url(r), odd, "URL must not be normalised or rebuilt")


class TestK_PdpIdentityVerified(unittest.TestCase):
    def test_verifies_matching_product(self):
        page, d, r = make(results=[card(SKU, TARGET_TITLE)])
        d.search_via_ui(r, SKU)
        d.open_product(r, d.wait_for_rendered_results(r)[0])
        d.capture_pdp_url(r)
        self.assertTrue(d.verify_pdp(r, SKU, MODEL))
        self.assertTrue(r.pdp_verification["url_is_pdp"])
        self.assertTrue(r.pdp_verification["identity_signal"])

    def test_rejects_page_without_identity_signal(self):
        page, d, r = make(results=[card(SKU, TARGET_TITLE)],
                          pdp_url="https://www.croma.com/other/p/999999",
                          pdp_text="Add to Cart Specifications Some Other Laptop",
                          pdp_h1="Some Other Laptop")
        d.search_via_ui(r, SKU)
        d.open_product(r, d.wait_for_rendered_results(r)[0])
        d.capture_pdp_url(r)
        self.assertFalse(d.verify_pdp(r, SKU, MODEL))

    def test_rejects_accessory_pdp(self):
        page, d, r = make(results=[card(SKU, TARGET_TITLE)],
                          pdp_text=f"Add to Cart 323360 Laptop Bag for Lenovo LOQ {MODEL}",
                          pdp_h1=f"Laptop Bag for Lenovo LOQ {MODEL}")
        d.search_via_ui(r, SKU)
        d.open_product(r, d.wait_for_rendered_results(r)[0])
        d.capture_pdp_url(r)
        self.assertFalse(d.verify_pdp(r, SKU, MODEL))
        self.assertFalse(r.pdp_verification["not_accessory"])

    def test_rejects_page_without_pdp_content(self):
        page, d, r = make(results=[card(SKU, TARGET_TITLE)],
                          pdp_text="323360 Lenovo LOQ", pdp_h1="Lenovo LOQ")
        d.search_via_ui(r, SKU)
        d.open_product(r, d.wait_for_rendered_results(r)[0])
        d.capture_pdp_url(r)
        self.assertFalse(d.verify_pdp(r, SKU, MODEL))


class TestL_PdpHtmlCaptured(unittest.TestCase):
    """The artifact fields the M1 correction requires."""

    def test_capture_carries_required_evidence(self):
        from croma_intel.domain.capture import (
            CaptureStatus, PageKind, Platform, build_capture,
        )
        page, d, r = make(results=[card(SKU, TARGET_TITLE)])
        d.search_via_ui(r, SKU)
        d.open_product(r, d.wait_for_rendered_results(r)[0])
        url = d.capture_pdp_url(r)
        d.verify_pdp(r, SKU, MODEL)

        cap = build_capture(
            platform=Platform.CROMA.value, page_kind=PageKind.PDP.value,
            requested_url=url, final_url=url, status=CaptureStatus.SUCCESS.value,
            html="<html><body>Add to Cart 323360</body></html>",
            input_sku=SKU,
            extra={"requested_model": MODEL, "pincode": PIN,
                   "pdp_url_source": "page.url", "discovery": r.to_dict()},
        )
        self.assertEqual(cap.final_url, url)
        self.assertEqual(cap.input_sku, SKU)
        self.assertEqual(cap.extra["requested_model"], MODEL)
        self.assertEqual(cap.extra["pincode"], PIN)
        self.assertTrue(cap.captured_at)
        self.assertTrue(cap.html_sha256)
        self.assertTrue(cap.extra["discovery"]["pdp_url"])
        ok, reason = cap.verify_integrity()
        self.assertTrue(ok, reason)


class TestM_FailureNeverFabricatesUrl(unittest.TestCase):
    def test_no_pdp_url_when_results_absent(self):
        page, d, r = make(results=[])
        d.search_via_ui(r, SKU)
        d.wait_for_rendered_results(r)
        self.assertIsNone(r.pdp_url)

    def test_no_pdp_url_when_identification_fails(self):
        page, d, r = make(results=[card("999999", "Some Unrelated Laptop 16GB SSD")])
        d.search_via_ui(r, SKU)
        rep = d.identify(r, d.wait_for_rendered_results(r), SKU, MODEL)
        self.assertFalse(rep.ok)
        self.assertIsNone(r.pdp_url)
        self.assertIsNone(r.selected)

    def test_gate_failure_path_nulls_pdp_url(self):
        from croma_intel.gate.m1_gate import GateOutcome, GateReport, _fail

        report = GateReport(outcome="X", sku=SKU, model=MODEL, started_at="t")
        report.pdp_url = "https://should.be/cleared/p/1"

        class _D:
            def url(self): return "https://www.croma.com"
            def capture(self, **kw): raise RuntimeError("no browser")
            def network_log(self): return []

        out = _fail(report, _D(), None, DiscoveryResult(), SKU, "search did not render",
                    GateOutcome.NO_GO)
        self.assertIsNone(out.pdp_url)
        self.assertEqual(out.outcome, GateOutcome.NO_GO)
        self.assertIn("did not render", out.summary)


if __name__ == "__main__":
    unittest.main()
