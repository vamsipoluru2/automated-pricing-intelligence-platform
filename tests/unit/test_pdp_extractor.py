"""Extractor behaviour on degraded, missing and malformed input.

The reference capture is a healthy page; these are the cases that decide whether a
wrong answer or an honest gap comes out when it is not.
"""

from __future__ import annotations

import json
import unittest

from croma_intel.domain.capture import (
    BlockEvidence,
    CaptureStatus,
    PageKind,
    Platform,
    build_capture,
)
from croma_intel.domain.errors import ReducerNotFoundError, StructureError
from croma_intel.domain.product import UNKNOWN, PriceStatus, SpecFeature
from croma_intel.extraction.initial_data import extract_state
from croma_intel.platforms.croma.pdp_extractor import (
    MOP_PATHS,
    build_identity,
    build_price_quote,
    build_specification_table,
    detect_description_conflicts,
    extract_croma_pdp,
)

MANUFACTURER_GROUP = {
    "code": "Grp-ManufacturerDetails-ManufacturerDetails",
    "displayOrder": 5,
    "name": "Manufacturer Details",
    "features": [
        {
            "code": "cromaclassification/1.0/Grp-ManufacturerDetails-"
            "ManufacturerDetails.SG-ManufacturerDetails-ModelSeries",
            "name": "Model Series",
            "featureValues": [{"value": "LOQ 15ARP10E"}],
        },
        {
            "code": "cromaclassification/1.0/Grp-ManufacturerDetails-"
            "ManufacturerDetails.SG-ManufacturerDetails-ModelNumber",
            "name": "Model Number",
            "featureValues": [{"value": "83S000FYIN"}],
        },
    ],
}


def make_page(pdp_data: dict, price: dict | None = None, *, raw_extra: str = "") -> str:
    blob: dict = {"pdpReducer": {"pdpData": pdp_data}}
    if price is not None:
        blob["pdpPriceReducer"] = {"pdpPriceData": price}
    body = json.dumps(blob)
    if raw_extra:
        body = body[:-1] + "," + raw_extra + "}"
    return f"<html><body><script>window.__INITIAL_DATA__= {body}</script></body></html>"


def make_capture(html: str, **kw):
    params = {
        "platform": Platform.CROMA.value,
        "page_kind": PageKind.PDP.value,
        "requested_url": "https://www.croma.com/x/p/323360",
        "final_url": "https://www.croma.com/x/p/323360",
        "status": CaptureStatus.SUCCESS.value,
        "html": html,
        "input_sku": "323360",
        "http_status": 200,
        "extra": {"pincode": "560087"},
    }
    params.update(kw)
    return build_capture(**params)  # type: ignore[arg-type]


HEALTHY = {
    "code": "323360",
    "name": "Lenovo LOQ 15ARP10E",
    "manufacturer": "Lenovo",
    "ean": "199276028259",
    "categoryL0": "3",
    "categoryL1": "20",
    "categoryL2": "806",
    "classifications": [MANUFACTURER_GROUP],
}
PRICE = {"sellingPrice": {"value": "113990.00"}, "mrp": {"value": "146790.00"}}


class TestCaptureGating(unittest.TestCase):
    """A bad capture must fail as a capture problem, never as a thin product."""

    def test_blocked_capture_is_refused(self):
        capture = make_capture(
            make_page(HEALTHY, PRICE),
            block=BlockEvidence(is_blocked=True, decisive_signal="HTTP_403_FORBIDDEN"),
        )
        with self.assertRaises(StructureError) as ctx:
            extract_croma_pdp(capture)
        self.assertIn("block", str(ctx.exception).lower())

    def test_non_success_status_is_refused(self):
        capture = make_capture(
            make_page(HEALTHY, PRICE), status=CaptureStatus.EMPTY_SHELL.value
        )
        with self.assertRaises(StructureError):
            extract_croma_pdp(capture)

    def test_wrong_platform_is_refused(self):
        capture = make_capture(
            make_page(HEALTHY, PRICE), platform=Platform.VIJAY_SALES.value
        )
        with self.assertRaises(StructureError):
            extract_croma_pdp(capture)

    def test_page_without_the_state_blob_is_refused(self):
        from croma_intel.domain.errors import StateBlobNotFoundError

        with self.assertRaises(StateBlobNotFoundError):
            extract_croma_pdp(make_capture("<html><body>shell</body></html>"))

    def test_missing_product_reducer_is_refused(self):
        page = "<script>window.__INITIAL_DATA__= {\"headerReducer\":{}}</script>"
        with self.assertRaises(ReducerNotFoundError):
            extract_croma_pdp(make_capture(page))


class TestMissingFieldsStayUnknown(unittest.TestCase):
    def setUp(self):
        self.product = extract_croma_pdp(
            make_capture(make_page({"code": "323360"}, None))
        )

    def test_absent_identity_fields_are_unknown(self):
        identity = self.product.identity
        for field in (identity.name, identity.brand, identity.ean,
                      identity.model_number, identity.model_series):
            self.assertFalse(field.known)
            self.assertIs(field.value, UNKNOWN)

    def test_present_field_still_extracted(self):
        self.assertEqual(self.product.identity.sku.value, "323360")

    def test_unknown_fields_are_enumerated(self):
        self.assertIn("identity.model_number", self.product.unknown_fields)
        self.assertNotIn("identity.sku", self.product.unknown_fields)

    def test_missing_field_still_records_the_path_searched(self):
        self.assertIn("classifications", self.product.identity.model_number.path)

    def test_no_specifications_is_a_warning_not_a_crash(self):
        self.assertEqual(self.product.specifications.feature_count, 0)
        self.assertTrue(any("specification" in w for w in self.product.warnings))

    def test_absent_price_reducer_yields_unresolved(self):
        self.assertIs(self.product.pricing.status, PriceStatus.MOP_UNRESOLVED)
        self.assertFalse(self.product.pricing.mop.known)
        self.assertTrue(any("pdpPriceReducer absent" in w for w in self.product.warnings))


class TestMalformedReducerStructure(unittest.TestCase):
    def test_classifications_of_the_wrong_type_fails_closed(self):
        state = extract_state(
            make_page({"code": "1", "classifications": "not a list"}),
            required=("pdpReducer",),
        )
        with self.assertRaises(StructureError):
            build_specification_table(state)

    def test_non_mapping_group_is_skipped_not_fatal(self):
        state = extract_state(
            make_page({"code": "1", "classifications": ["junk", MANUFACTURER_GROUP]}),
            required=("pdpReducer",),
        )
        self.assertEqual(build_specification_table(state).group_count, 1)

    def test_feature_without_values_is_dropped(self):
        group = {"name": "G", "features": [{"name": "Empty", "featureValues": []}]}
        state = extract_state(
            make_page({"code": "1", "classifications": [group]}), required=("pdpReducer",)
        )
        table = build_specification_table(state)
        self.assertEqual(table.group_count, 1)
        self.assertEqual(table.feature_count, 0)

    def test_undefined_feature_value_does_not_become_a_value(self):
        page = make_page(
            {"code": "1", "classifications": [{"name": "G", "features": [
                {"name": "Model Number", "featureValues": [{"value": None}]}]}]}
        )
        state = extract_state(page, required=("pdpReducer",))
        table = build_specification_table(state)
        self.assertEqual(table.feature_count, 0)
        self.assertIs(build_identity(state, table).model_number.value, UNKNOWN)

    def test_multi_valued_model_number_refuses_to_choose(self):
        """Two model numbers is ambiguity, not a menu."""
        group = {
            "name": "Manufacturer Details",
            "features": [{
                "code": "x.SG-ManufacturerDetails-ModelNumber",
                "name": "Model Number",
                "featureValues": [{"value": "83S000FYIN"}, {"value": "83S000CPIN"}],
            }],
        }
        state = extract_state(
            make_page({"code": "1", "classifications": [group]}), required=("pdpReducer",)
        )
        identity = build_identity(state, build_specification_table(state))
        self.assertIs(identity.model_number.value, UNKNOWN)
        self.assertIn("refusing to choose", identity.model_number.note)


class TestModelNumberSourcing(unittest.TestCase):
    def test_matched_by_stable_code_not_display_label(self):
        relabelled = json.loads(json.dumps(MANUFACTURER_GROUP))
        relabelled["features"][1]["name"] = "Modelnummer"  # localised label
        state = extract_state(
            make_page({"code": "1", "classifications": [relabelled]}),
            required=("pdpReducer",),
        )
        field = build_identity(state, build_specification_table(state)).model_number
        self.assertEqual(field.value, "83S000FYIN")
        self.assertIn("code", field.note)

    def test_falls_back_to_the_label_when_the_code_is_absent(self):
        group = {"name": "M", "features": [
            {"name": "Model Number", "featureValues": [{"value": "83S000FYIN"}]}]}
        state = extract_state(
            make_page({"code": "1", "classifications": [group]}), required=("pdpReducer",)
        )
        field = build_identity(state, build_specification_table(state)).model_number
        self.assertEqual(field.value, "83S000FYIN")
        self.assertIn("label", field.note)

    def test_title_is_never_a_model_source(self):
        """No labelled field, but the title carries the MTM. It must stay UNKNOWN."""
        state = extract_state(
            make_page({"code": "1", "name": "Lenovo LOQ 83S000FYIN Gaming Laptop"}),
            required=("pdpReducer",),
        )
        identity = build_identity(state, build_specification_table(state))
        self.assertIs(identity.model_number.value, UNKNOWN)
        self.assertIn("83S000FYIN", str(identity.name.value))


class TestPriceGuards(unittest.TestCase):
    def _quote(self, price):
        return build_price_quote(
            extract_state(make_page(HEALTHY, price), required=("pdpReducer",),
                          optional=("pdpPriceReducer",))
        )

    def test_healthy_price_resolves(self):
        quote = self._quote(PRICE)
        self.assertIs(quote.status, PriceStatus.RESOLVED)
        self.assertEqual(quote.mop.path, MOP_PATHS[0])

    def test_selling_price_above_mrp_is_refused(self):
        quote = self._quote(
            {"sellingPrice": {"value": "200000.00"}, "mrp": {"value": "146790.00"}}
        )
        self.assertIs(quote.status, PriceStatus.MOP_UNRESOLVED)
        self.assertFalse(quote.mop.known)
        self.assertIn("exceeded MRP", quote.reason)

    def test_zero_price_is_refused(self):
        quote = self._quote({"sellingPrice": {"value": "0"}, "mrp": {"value": "1"}})
        self.assertIs(quote.status, PriceStatus.MOP_UNRESOLVED)

    def test_unparseable_price_is_refused(self):
        quote = self._quote({"sellingPrice": {"value": "₹1,13,990"}, "mrp": {"value": "1"}})
        self.assertIs(quote.status, PriceStatus.MOP_UNRESOLVED)

    def test_mrp_may_be_unknown_while_mop_resolves(self):
        quote = self._quote({"sellingPrice": {"value": "113990.00"}})
        self.assertIs(quote.status, PriceStatus.RESOLVED)
        self.assertFalse(quote.mrp.known)

    def test_a_price_at_an_unlisted_path_is_ignored(self):
        """An EMI instalment sitting in the price reducer must not become MOP."""
        quote = self._quote(
            {"emiPrice": {"value": "5366.00"}, "exchangeValue": {"value": "42600.00"}}
        )
        self.assertIs(quote.status, PriceStatus.MOP_UNRESOLVED)
        self.assertFalse(quote.mop.known)


class TestDescriptionConflict(unittest.TestCase):
    def _table(self, ram_type: str):
        group = {"name": "Internal Memory", "features": [
            {"name": "Type of RAM", "featureValues": [{"value": ram_type}]}]}
        return build_specification_table(
            extract_state(
                make_page({"code": "1", "classifications": [group]}),
                required=("pdpReducer",),
            )
        )

    def test_contradiction_is_reported(self):
        warnings = detect_description_conflicts(
            "<p>Incorporating 16GB of <strong>LPDDR5-5500</strong> memory</p>",
            self._table("DDR5"),
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("LPDDR5-5500", warnings[0])
        self.assertIn("specification wins", warnings[0])

    def test_agreement_is_silent(self):
        self.assertEqual(
            detect_description_conflicts("<p>16GB DDR5 memory</p>", self._table("DDR5")), ()
        )

    def test_no_claim_in_prose_is_silent(self):
        self.assertEqual(
            detect_description_conflicts("<p>A fast laptop.</p>", self._table("DDR5")), ()
        )

    def test_absent_spec_means_nothing_to_contradict(self):
        self.assertEqual(
            detect_description_conflicts("<p>LPDDR5 memory</p>", self._table("")), ()
        )

    def test_detector_never_alters_the_specification(self):
        table = self._table("DDR5")
        detect_description_conflicts("<p>LPDDR5-5500</p>", table)
        feature = table.find("Type of RAM")
        assert isinstance(feature, SpecFeature)
        self.assertEqual(feature.value, "DDR5")


if __name__ == "__main__":
    unittest.main()
