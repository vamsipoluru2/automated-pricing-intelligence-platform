"""Extraction over the REAL Croma PDP captured live on 2026-08-25.

Provenance: the M1 gate run that returned GO. SKU 323360 was searched through the
visible browser UI under pincode 560087, the result was identified by SKU evidence,
the product was opened and the PDP URL read from ``page.url``. The capture is
committed here as a content-addressed store, so these tests exercise the real
artifact path — ``CaptureStore.read()`` verifies the SHA-256 and re-derives the
capture id before extraction ever runs.

Everything asserted below is a fact about that artifact. Nothing is mocked.
"""

from __future__ import annotations

import json
import re
import socket
import unittest
from pathlib import Path

from croma_intel.browser.artifacts import CaptureStore
from croma_intel.domain.product import UNKNOWN, Money, PriceStatus, SourceKind, SpecFeature
from croma_intel.extraction.initial_data import extract_state
from croma_intel.platforms.croma.pdp_extractor import (
    MOP_PATHS,
    MRP_PATHS,
    extract_croma_pdp,
    iter_sourced_fields,
)

FIXTURE_STORE = Path(__file__).resolve().parents[1] / "fixtures" / "croma" / "pdp_323360"
CAPTURE_ID = "5df6c46a45c3c93d941a5c4c078cd7e5585613729b0e5844101c65ccd13c1f4b"

SKU = "323360"
MODEL_NUMBER = "83S000FYIN"
MODEL_SERIES = "LOQ 15ARP10E"
PINCODE = "560087"


def load_capture():
    return CaptureStore(FIXTURE_STORE).read(CAPTURE_ID, verify=True)


class _NoNetwork:
    """Makes any socket creation raise, so 'offline' is enforced not assumed."""

    def __enter__(self):
        self._socket = socket.socket
        self._create = socket.create_connection

        def blocked(*_a, **_kw):
            raise AssertionError("network access attempted during extraction")

        socket.socket = blocked  # type: ignore[assignment]
        socket.create_connection = blocked  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc):
        socket.socket = self._socket  # type: ignore[assignment]
        socket.create_connection = self._create  # type: ignore[assignment]


class ExtractionTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = load_capture()
        cls.product = extract_croma_pdp(cls.capture)


class TestFixtureProvenance(ExtractionTestBase):
    def test_artifact_verifies(self):
        ok, reason = self.capture.verify_integrity()
        self.assertTrue(ok, reason)

    def test_it_is_the_pdp_that_m1_discovered(self):
        self.assertEqual(self.capture.page_kind, "PDP")
        self.assertEqual(self.capture.status, "SUCCESS")
        self.assertFalse(self.capture.block.is_blocked)
        self.assertEqual(self.capture.input_sku, SKU)
        self.assertEqual(self.capture.html_length, 707_724)

    def test_pdp_url_was_discovered_not_constructed(self):
        self.assertEqual(self.capture.extra["pdp_url_source"], "page.url")
        self.assertTrue(self.capture.final_url.endswith(f"/p/{SKU}"))


class TestBoundedParsing(ExtractionTestBase):
    """Only the reducers the product needs are parsed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.state = extract_state(
            cls.capture.html,
            required=("pdpReducer",),
            optional=("pdpPriceReducer", "reviewProductReducer", "extendedWarrantyReducer"),
        )

    def test_all_six_reducers_are_discovered(self):
        self.assertEqual(
            self.state.available,
            (
                "pdpReducer",
                "reviewProductReducer",
                "extendedWarrantyReducer",
                "hybrisCmsReducer",
                "pdpPriceReducer",
                "headerReducer",
            ),
        )

    def test_blob_is_bounded_correctly(self):
        self.assertEqual(self.state.bytes_available, 441_028)

    def test_page_chrome_is_never_parsed(self):
        """headerReducer is 89% of the payload and nothing here reads it."""
        self.assertIn("headerReducer", self.state.available)
        self.assertNotIn("headerReducer", self.state.payloads)
        self.assertIs(self.state.value("headerReducer"), UNKNOWN)

    def test_only_a_ninth_of_the_payload_is_parsed(self):
        self.assertEqual(self.state.bytes_parsed, 49_134)
        self.assertLess(self.state.bytes_parsed / self.state.bytes_available, 0.12)

    def test_undefined_appears_inside_the_reducers_we_do_parse(self):
        """Slicing alone would not have been enough."""
        for name in ("pdpReducer", "pdpPriceReducer"):
            with self.subTest(reducer=name):
                self.assertIn(":undefined", self.state.payloads[name].raw)
                self.assertIs(self.state.value(name)["errorMessage"], UNKNOWN)

    def test_whole_blob_json_parsing_would_have_failed(self):
        """Records why the bounded design exists, against the real bytes."""
        blob = self.capture.html[
            self.state.location.start : self.state.location.end
        ]
        with self.assertRaises(json.JSONDecodeError):
            json.loads(blob)

    def test_raw_slices_are_retained_for_provenance(self):
        payload = self.state.payloads["pdpPriceReducer"]
        self.assertIn('"sellingPrice":{"value":"113990.00"}', payload.raw)
        self.assertEqual(len(payload.raw_sha256), 64)


class TestIdentity(ExtractionTestBase):
    def test_sku(self):
        self.assertEqual(self.product.identity.sku.value, SKU)
        self.assertEqual(self.product.identity.sku.path, "pdpReducer.pdpData.code")

    def test_model_number_from_the_labelled_field(self):
        field = self.product.identity.model_number
        self.assertEqual(field.value, MODEL_NUMBER)
        self.assertIn("classifications", field.path)
        self.assertIn("featureValues", field.path)
        self.assertEqual(field.note, "matched by code")

    def test_model_series_is_separate_from_the_model_number(self):
        self.assertEqual(self.product.identity.model_series.value, MODEL_SERIES)
        self.assertNotEqual(
            self.product.identity.model_series.value,
            self.product.identity.model_number.value,
        )

    def test_model_number_is_preserved_character_for_character(self):
        """Rule 4 / M0 §10.3: 83S000FYIN and 83S00000US are different products."""
        value = self.product.identity.model_number.value
        self.assertEqual(value, "83S000FYIN")
        self.assertTrue(str(value).endswith("IN"))
        self.assertEqual(len(str(value)), 10)

    def test_brand_and_name(self):
        self.assertEqual(self.product.identity.brand.value, "Lenovo")
        self.assertEqual(
            self.product.identity.brand.path, "pdpReducer.pdpData.manufacturer"
        )
        self.assertTrue(str(self.product.identity.name.value).startswith("Lenovo LOQ 15ARP10E"))

    def test_ean(self):
        self.assertEqual(self.product.identity.ean.value, "199276028259")

    def test_platform_is_croma(self):
        self.assertEqual(self.product.identity.platform, "croma")


class TestCategory(ExtractionTestBase):
    def test_numeric_hierarchy(self):
        category = self.product.identity.category
        self.assertEqual(
            (category.l0.value, category.l1.value, category.l2.value), ("3", "20", "806")
        )

    def test_category_ids_match_the_accessory_gate_findings(self):
        """M0 §12.1: /c/20 Laptops, /c/806 Gaming Laptops — product, not accessory."""
        self.assertEqual(self.product.identity.category.l2.value, "806")

    def test_breadcrumb_trail(self):
        crumbs = self.product.identity.category.breadcrumbs
        self.assertEqual(
            [c.name for c in crumbs[:3]],
            ["Computers & Tablets", "Laptops", "Gaming Laptops"],
        )
        self.assertEqual([c.category_code for c in crumbs[:3]], ["3", "20", "806"])

    def test_final_breadcrumb_is_the_product_itself(self):
        crumbs = self.product.identity.category.breadcrumbs
        self.assertEqual(len(crumbs), 4)
        self.assertTrue(crumbs[-1].url.endswith(f"/p/{SKU}"))


class TestPricing(ExtractionTestBase):
    def test_resolved_from_the_allow_listed_paths(self):
        pricing = self.product.pricing
        self.assertIs(pricing.status, PriceStatus.RESOLVED)
        self.assertEqual(pricing.mop.path, MOP_PATHS[0])
        self.assertEqual(pricing.mrp.path, MRP_PATHS[0])

    def test_exact_amounts(self):
        mop, mrp = self.product.pricing.mop.value, self.product.pricing.mrp.value
        assert isinstance(mop, Money) and isinstance(mrp, Money)
        self.assertEqual(str(mop.amount), "113990.00")
        self.assertEqual(str(mrp.amount), "146790.00")
        self.assertEqual(mop.currency, "INR")

    def test_mop_is_below_mrp(self):
        mop, mrp = self.product.pricing.mop.value, self.product.pricing.mrp.value
        assert isinstance(mop, Money) and isinstance(mrp, Money)
        self.assertLess(mop.amount, mrp.amount)

    def test_the_emi_figure_on_the_page_is_not_the_price(self):
        """The rendered page shows ₹5,366/mo. It must appear nowhere in pricing."""
        self.assertIn("5,366", self.capture.visible_text)
        self.assertNotIn("5366", json.dumps(self.product.pricing.to_dict()))

    def test_exchange_and_offer_values_are_not_the_price(self):
        payload = json.dumps(self.product.pricing.to_dict())
        for figure in ("32,800", "32800", "1500"):
            with self.subTest(figure=figure):
                self.assertNotIn(figure, payload)


class TestSpecifications(ExtractionTestBase):
    def test_complete_table_is_preserved(self):
        table = self.product.specifications
        self.assertEqual(table.group_count, 28)
        self.assertEqual(table.feature_count, 111)

    def test_structure_is_grouped_not_flattened(self):
        names = [g.name for g in self.product.specifications.groups]
        for expected in (
            "Laptop Category", "Manufacturer Details", "Laptop Processor Details",
            "Internal Memory", "Storage Specification", "Graphic Processor",
            "Ports & Slots", "Laptop Battery", "After Sales & Services",
        ):
            self.assertIn(expected, names)

    def test_the_fields_the_brief_named(self):
        table = self.product.specifications
        for label, expected in (
            ("Processor Variant", "7535HS"),
            ("RAM Configuration", "1 x 16GB"),
            ("Total Number Of Slots", "2"),
            ("Number of Empty Slots", "1"),
            ("Dedicated Graphics Memory", "6GB"),
            ("Refresh Rate", "144"),
        ):
            with self.subTest(label=label):
                self.assertEqual(table.value_of(label), expected)

    def test_units_are_retained_alongside_values(self):
        table = self.product.specifications
        weight = table.find("Product Weight")
        assert isinstance(weight, SpecFeature)
        self.assertEqual(weight.value, "1.8")
        self.assertEqual(weight.unit, "Kg")
        self.assertEqual(weight.value_with_unit, "1.8 Kg")
        refresh = table.find("Refresh Rate")
        assert isinstance(refresh, SpecFeature)
        self.assertEqual(refresh.value_with_unit, "144 Hz")

    def test_matching_grade_specs_are_present(self):
        table = self.product.specifications
        self.assertEqual(table.value_of("Model Number"), MODEL_NUMBER)
        self.assertEqual(table.value_of("RAM"), "16GB")
        self.assertEqual(table.value_of("Type of RAM"), "DDR5")
        self.assertEqual(table.value_of("SSD Capacity"), "512GB")
        self.assertEqual(table.value_of("GPU Model"), "NVIDIA GeForce RTX 3050")
        self.assertEqual(table.value_of("Operating System"), "Windows 11 Home")
        self.assertEqual(table.value_of("Display Size (In Inches)"), "15.6")

    def test_gpu_variant_suffix_is_not_normalised_away(self):
        """M0 §9: RTX 3050A is a different part from RTX 3050. Store verbatim."""
        self.assertEqual(
            self.product.specifications.value_of("GPU Model"), "NVIDIA GeForce RTX 3050"
        )

    def test_multi_valued_feature_keeps_all_values(self):
        feature = self.product.specifications.find("Suitable For")
        assert isinstance(feature, SpecFeature)
        self.assertGreater(len(feature.values), 1)
        self.assertIn("Gaming", feature.values)
        self.assertIs(feature.value, UNKNOWN)  # refuses to pick one

    def test_duplicate_labels_across_groups_are_not_collapsed(self):
        """A flat dict would silently drop one of these."""
        matches = [
            (g.name, f.value)
            for g, f in self.product.specifications.iter_features()
            if f.name == "Customer Support Number"
        ]
        self.assertEqual(len(matches), 2)
        self.assertNotEqual(matches[0][0], matches[1][0])


class TestMarketingVersusSpecification(ExtractionTestBase):
    """The contradiction found in this very capture."""

    def test_prose_claims_lpddr5(self):
        state = extract_state(self.capture.html, required=("pdpReducer",))
        self.assertIn("LPDDR5", state.value("pdpReducer")["pdpData"]["description"])

    def test_specification_says_ddr5_socketed(self):
        table = self.product.specifications
        self.assertEqual(table.value_of("Type of RAM"), "DDR5")
        self.assertEqual(table.value_of("RAM Form Factor"), "SO-DIMM")
        self.assertEqual(table.value_of("Total Number Of Slots"), "2")

    def test_the_specification_wins(self):
        self.assertEqual(self.product.specifications.value_of("Type of RAM"), "DDR5")
        self.assertNotIn(
            "LPDDR5", json.dumps(self.product.specifications.to_dict())
        )

    def test_the_disagreement_is_surfaced_not_hidden(self):
        conflicts = [w for w in self.product.warnings if "LPDDR5" in w]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("specification wins", conflicts[0])


class TestJsonLdIsIrrelevant(ExtractionTestBase):
    """The page's JSON-LD is malformed, and extraction neither needs nor reads it."""

    def _first_ld(self) -> str:
        blocks = re.findall(
            r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
            self.capture.html,
            re.DOTALL | re.IGNORECASE,
        )
        return blocks[0].strip()

    def test_the_json_ld_really_is_malformed(self):
        with self.assertRaises(json.JSONDecodeError):
            json.loads(self._first_ld())

    def test_extraction_succeeds_regardless(self):
        self.assertEqual(self.product.identity.sku.value, SKU)
        self.assertIs(self.product.pricing.status, PriceStatus.RESOLVED)

    def test_json_ld_could_not_have_supplied_the_model_number(self):
        """It is a poorer source even when it parses: no MTM, no specifications."""
        self.assertNotIn(MODEL_NUMBER, self._first_ld())

    def test_no_field_is_sourced_from_json_ld(self):
        for name, field in iter_sourced_fields(self.product):
            with self.subTest(field=name):
                self.assertNotIn("ld+json", field.path)
                self.assertNotIn("jsonld", field.path.lower())


class TestOffers(ExtractionTestBase):
    def test_offers_come_from_the_rendered_dom(self):
        self.assertEqual(self.product.offers.kind, SourceKind.RENDERED_DOM)
        self.assertGreater(self.product.offers.extracted_count, 0)

    def test_offers_are_absent_from_the_state_blob(self):
        state = extract_state(self.capture.html, required=("pdpReducer",))
        self.assertNotIn("bank-offer", state.payloads["pdpReducer"].raw)

    def test_declared_and_extracted_counts_are_both_reported(self):
        offers = self.product.offers
        self.assertEqual(offers.declared_count, 7)
        self.assertEqual(offers.extracted_count, 6)
        self.assertIs(offers.complete, False)

    def test_the_shortfall_is_warned_about(self):
        self.assertTrue(
            any("declared 7 offers but 6" in w for w in self.product.warnings)
        )

    def test_bank_names_are_captured_with_provenance(self):
        names = [o.bank_name.value for o in self.product.offers.offers]
        self.assertIn("Kotak Mahindra Bank", names)
        self.assertIn("Federal Bank", names)

    def test_the_nameless_slide_stays_unknown_without_shifting_the_rest(self):
        offers = self.product.offers.offers
        self.assertIs(offers[1].bank_name.value, UNKNOWN)
        self.assertIn("Kotak", str(offers[1].text.value))

    def test_bank_codes_are_unknown_after_hydration_not_invented(self):
        for offer in self.product.offers.offers:
            with self.subTest(index=offer.index):
                self.assertIs(offer.bank_code.value, UNKNOWN)
                self.assertIn("substitutes", offer.bank_code.note)

    def test_offer_text_is_preserved(self):
        texts = " | ".join(str(o.text.value) for o in self.product.offers.offers)
        self.assertIn("No-Cost EMI", texts)
        self.assertIn("NeuCoins", texts)


class TestPincodeProvenance(ExtractionTestBase):
    def test_pincode_is_recorded_from_the_capture_manifest(self):
        pincode = self.product.provenance.pincode
        self.assertEqual(pincode.value, PINCODE)
        self.assertEqual(pincode.kind, SourceKind.CAPTURE_MANIFEST)
        self.assertEqual(pincode.path, "capture.extra.pincode")

    def test_the_page_itself_confirms_the_location(self):
        self.assertIn(PINCODE, self.capture.visible_text)

    def test_location_scoping_is_visible_in_this_capture(self):
        """Evidence that the pincode is load-bearing, not decorative."""
        self.assertIn("not available for your location", self.capture.visible_text)

    def test_capture_identity_travels_with_the_product(self):
        provenance = self.product.provenance
        self.assertEqual(provenance.capture_id, CAPTURE_ID)
        self.assertEqual(provenance.html_sha256, self.capture.html_sha256)
        self.assertEqual(provenance.requested_model.value, MODEL_NUMBER)
        self.assertEqual(provenance.pdp_url_source.value, "page.url")


class TestEveryFieldIsSourced(ExtractionTestBase):
    def test_no_field_is_unknown_on_a_healthy_capture(self):
        self.assertEqual(self.product.unknown_fields, ())

    def test_every_field_carries_a_source_path(self):
        for name, field in iter_sourced_fields(self.product):
            with self.subTest(field=name):
                self.assertTrue(field.path, f"{name} has no source path")
                self.assertTrue(field.known, f"{name} is UNKNOWN")

    def test_authoritative_fields_come_from_the_state_blob(self):
        blob_sourced = {
            "identity.sku", "identity.name", "identity.brand", "identity.model_number",
            "identity.model_series", "identity.ean", "pricing.mop", "pricing.mrp",
        }
        for name, field in iter_sourced_fields(self.product):
            if name in blob_sourced:
                with self.subTest(field=name):
                    self.assertEqual(field.kind, SourceKind.INITIAL_DATA)

    def test_result_serialises_to_json(self):
        payload = json.dumps(self.product.to_dict())
        self.assertIn(MODEL_NUMBER, payload)
        self.assertIn("113990.00", payload)


class TestOfflineAndDeterministic(unittest.TestCase):
    def test_extraction_runs_with_sockets_disabled(self):
        """Rule 1, enforced physically rather than by convention."""
        with _NoNetwork():
            capture = load_capture()
            product = extract_croma_pdp(capture)
        self.assertEqual(product.identity.model_number.value, MODEL_NUMBER)
        self.assertIs(product.pricing.status, PriceStatus.RESOLVED)
        self.assertEqual(product.specifications.feature_count, 111)

    def test_extraction_is_a_pure_function_of_the_capture(self):
        capture = load_capture()
        first = extract_croma_pdp(capture).to_dict()
        second = extract_croma_pdp(capture).to_dict()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_reloading_the_artifact_yields_the_same_product(self):
        first = json.dumps(extract_croma_pdp(load_capture()).to_dict(), sort_keys=True)
        second = json.dumps(extract_croma_pdp(load_capture()).to_dict(), sort_keys=True)
        self.assertEqual(first, second)

    def test_extraction_does_not_mutate_the_capture(self):
        capture = load_capture()
        before = capture.html_sha256
        extract_croma_pdp(capture)
        ok, reason = capture.verify_integrity()
        self.assertTrue(ok, reason)
        self.assertEqual(capture.html_sha256, before)


if __name__ == "__main__":
    unittest.main()
