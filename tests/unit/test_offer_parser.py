"""Offer extraction from rendered markup, and its provenance discipline."""

from __future__ import annotations

import unittest

from croma_intel.domain.product import UNKNOWN, SourceKind
from croma_intel.platforms.croma.offer_parser import extract_offers


def _slide(bank: str, text: str) -> str:
    return (
        '<div class="swiper-slide"><div class="bank-offer-product">'
        '<div class="offer-pdp-carousel"><div class="bank-text-name-container">'
        f'<span class="bank-name-text">{bank}</span></div></div>'
        '<div class="flex-style-carousel"><span class="bank-offer-details-carousel">'
        f'<span class="bank-offers-text-pdp-carousel">{text}</span></span>'
        '<span><a class="learn-more-text-carousel">View more</a></span></div>'
        "</div></div>"
    )


def _page(slides: str, declared: int = 2) -> str:
    return (
        "<html><body><div>irrelevant</div>"
        '<div class="offer-container"><div><span class="display-flex">'
        f'<span class="extra-savings-text">Super Savings <span class="extra-savings-text-br">'
        f"({declared} OFFERS)</span></span></span>"
        f'<div class="offer-section-pdp"><div class="bank-offer-swiper">'
        f'<div class="swiper-wrapper">{slides}</div></div></div></div></div>'
        '<div class="footer">after</div></body></html>'
    )


class TestOfferExtraction(unittest.TestCase):
    def test_extracts_name_and_text_per_slide(self):
        offers = extract_offers(
            _page(_slide("Kotak Mahindra Bank", "Instant discount of 10% up to Rs 1500"))
        )
        self.assertEqual(offers.extracted_count, 1)
        offer = offers.offers[0]
        self.assertEqual(offer.bank_name.value, "Kotak Mahindra Bank")
        self.assertIn("Instant discount", str(offer.text.value))
        self.assertEqual(offer.bank_name.kind, SourceKind.RENDERED_DOM)

    def test_entities_are_resolved(self):
        offers = extract_offers(_page(_slide("HDFC &amp; SBI", "T&amp;Cs apply")))
        self.assertEqual(offers.offers[0].bank_name.value, "HDFC & SBI")
        self.assertEqual(offers.offers[0].text.value, "T&Cs apply")

    def test_offers_stay_paired_when_a_bank_name_is_empty(self):
        """The real capture has a nameless slide; zipping two lists would misalign."""
        offers = extract_offers(
            _page(_slide("", "offer A") + _slide("Federal Bank", "offer B"), declared=2)
        )
        self.assertEqual(offers.extracted_count, 2)
        self.assertIs(offers.offers[0].bank_name.value, UNKNOWN)
        self.assertEqual(offers.offers[0].text.value, "offer A")
        self.assertEqual(offers.offers[1].bank_name.value, "Federal Bank")
        self.assertEqual(offers.offers[1].text.value, "offer B")

    def test_declared_count_mismatch_is_reported_not_hidden(self):
        offers = extract_offers(_page(_slide("A", "x"), declared=7))
        self.assertEqual(offers.declared_count, 7)
        self.assertEqual(offers.extracted_count, 1)
        self.assertIs(offers.complete, False)

    def test_complete_when_counts_agree(self):
        offers = extract_offers(_page(_slide("A", "x") + _slide("B", "y"), declared=2))
        self.assertIs(offers.complete, True)

    def test_no_container_yields_an_empty_set_not_an_error(self):
        offers = extract_offers("<html><body>no offers</body></html>")
        self.assertEqual(offers.extracted_count, 0)
        self.assertIs(offers.declared_count, UNKNOWN)
        self.assertIs(offers.complete, UNKNOWN)

    def test_content_outside_the_container_is_not_harvested(self):
        page = _page(_slide("A", "x")) + _slide("Outside Bank", "not an offer")
        self.assertEqual(extract_offers(page).extracted_count, 1)


class TestBankIdentifierProvenance(unittest.TestCase):
    """A display name must never overwrite a raw code, nor be reversed into one."""

    def test_raw_code_is_preserved_when_present(self):
        offers = extract_offers(_page(_slide("JP_KOTAK", "pre-hydration capture")))
        offer = offers.offers[0]
        self.assertEqual(offer.bank_code.value, "JP_KOTAK")
        self.assertEqual(offer.bank_code.kind, SourceKind.RENDERED_DOM)
        self.assertEqual(offer.bank_name.value, "JP_KOTAK")

    def test_code_is_unknown_when_the_page_only_shows_a_display_name(self):
        """After hydration Croma's script replaces the code; it is gone, not derivable."""
        offers = extract_offers(_page(_slide("Kotak Mahindra Bank", "post-hydration")))
        offer = offers.offers[0]
        self.assertEqual(offer.bank_name.value, "Kotak Mahindra Bank")
        self.assertIs(offer.bank_code.value, UNKNOWN)
        self.assertIn("substitutes", offer.bank_code.note)

    def test_netbanking_prefix_also_recognised(self):
        offers = extract_offers(_page(_slide("NB_FEDERAL", "x")))
        self.assertEqual(offers.offers[0].bank_code.value, "NB_FEDERAL")


class TestOffersAreNotPrices(unittest.TestCase):
    def test_offer_carries_no_monetary_field(self):
        """Rule 6: a promotion is not a price, structurally as well as by policy."""
        from croma_intel.domain.product import Offer

        fields = set(Offer.__dataclass_fields__)
        self.assertEqual(fields, {"index", "text", "bank_name", "bank_code"})
        for forbidden in ("amount", "value", "price", "discount", "money"):
            self.assertNotIn(forbidden, fields)

    def test_rupee_figures_in_offer_text_stay_text(self):
        offers = extract_offers(_page(_slide("A", "Instant discount of 10% up to Rs 1500")))
        self.assertIsInstance(offers.offers[0].text.value, str)


if __name__ == "__main__":
    unittest.main()
