"""State-blob discovery, boundary detection and bounded reducer extraction."""

from __future__ import annotations

import unittest

from croma_intel.domain.errors import ReducerNotFoundError, StateBlobNotFoundError
from croma_intel.domain.product import UNKNOWN
from croma_intel.extraction.initial_data import (
    dig,
    dig_str,
    extract_state,
    locate_state_blob,
)

PAGE = """<!doctype html><html><head><title>t</title></head><body>
<script>window.__INITIAL_DATA__= {"pdpReducer":{"pdpData":{"code":"323360",\
"name":"Lenovo LOQ","ean":"199276028259","empty":"","n":7,"flag":false},\
"isFetching":false,"errorMessage":undefined},\
"pdpPriceReducer":{"pdpPriceData":{"sellingPrice":{"value":"113990.00"},\
"mrp":{"value":"146790.00"}},"errorMessage":undefined},\
"headerReducer":{"junk":"}not the end{","more":[1,2,3]}}</script>
<div id="root"></div></body></html>"""


class TestBlobDiscovery(unittest.TestCase):
    def test_locates_the_assignment(self):
        loc = locate_state_blob(PAGE)
        self.assertEqual(PAGE[loc.start], "{")
        self.assertEqual(PAGE[loc.end - 1], "}")

    def test_boundary_is_string_aware(self):
        """A '}' inside headerReducer's junk string must not end the blob early."""
        loc = locate_state_blob(PAGE)
        self.assertIn("headerReducer", PAGE[loc.start : loc.end])
        self.assertNotIn("</script>", PAGE[loc.start : loc.end])

    def test_spacing_around_equals_is_not_assumed(self):
        for assignment in (
            "window.__INITIAL_DATA__={\"a\":1}",
            "window.__INITIAL_DATA__ = {\"a\":1}",
            "window . __INITIAL_DATA__   =\n{\"a\":1}",
        ):
            with self.subTest(assignment=assignment):
                loc = locate_state_blob(f"<script>{assignment}</script>")
                self.assertEqual(loc.length, len('{"a":1}'))

    def test_absent_blob_raises_its_own_error(self):
        with self.assertRaises(StateBlobNotFoundError):
            locate_state_blob("<html><body>no state here</body></html>")

    def test_a_mere_mention_is_skipped_for_the_real_assignment(self):
        """The marker also appears in prose and in unrelated scripts."""
        page = (
            '<p>we set window.__INITIAL_DATA__ elsewhere</p>'
            '<script>window.__INITIAL_DATA__= {"pdpReducer":{"pdpData":{"code":"1"}}}</script>'
        )
        state = extract_state(page, required=("pdpReducer",))
        self.assertEqual(dig_str(state.value("pdpReducer"), "pdpData.code"), "1")

    def test_marker_without_an_object_raises(self):
        with self.assertRaises(StateBlobNotFoundError):
            locate_state_blob("<script>window.__INITIAL_DATA__ = null;</script>")


class TestBoundedExtraction(unittest.TestCase):
    def setUp(self):
        self.state = extract_state(
            PAGE, required=("pdpReducer",), optional=("pdpPriceReducer", "nopeReducer")
        )

    def test_all_top_level_members_are_reported(self):
        self.assertEqual(
            self.state.available, ("pdpReducer", "pdpPriceReducer", "headerReducer")
        )

    def test_only_requested_members_are_parsed(self):
        self.assertEqual(sorted(self.state.payloads), ["pdpPriceReducer", "pdpReducer"])
        self.assertNotIn("headerReducer", self.state.payloads)

    def test_unparsed_member_is_unknown_not_empty(self):
        self.assertIs(self.state.value("headerReducer"), UNKNOWN)

    def test_byte_accounting_proves_the_saving(self):
        self.assertLess(self.state.bytes_parsed, self.state.bytes_available)

    def test_missing_optional_is_recorded_not_raised(self):
        self.assertEqual(self.state.missing_optional, ("nopeReducer",))

    def test_missing_required_raises(self):
        with self.assertRaises(ReducerNotFoundError) as ctx:
            extract_state(PAGE, required=("noSuchReducer",))
        self.assertIn("noSuchReducer", str(ctx.exception))

    def test_raw_slice_is_preserved_for_provenance(self):
        payload = self.state.payloads["pdpPriceReducer"]
        self.assertTrue(payload.raw.startswith("{"))
        self.assertIn("113990.00", payload.raw)
        self.assertEqual(len(payload.raw_sha256), 64)

    def test_raw_slice_hash_is_stable(self):
        again = extract_state(PAGE, required=("pdpReducer",))
        self.assertEqual(
            again.payloads["pdpReducer"].raw_sha256,
            self.state.payloads["pdpReducer"].raw_sha256,
        )

    def test_undefined_inside_a_required_reducer_is_handled(self):
        """Slicing alone is not enough — undefined lives inside pdpReducer too."""
        self.assertIs(dig(self.state.value("pdpReducer"), "errorMessage"), UNKNOWN)


class TestDig(unittest.TestCase):
    def setUp(self):
        self.state = extract_state(PAGE, required=("pdpReducer",))
        self.pdp = self.state.value("pdpReducer")

    def test_walks_a_dotted_path(self):
        self.assertEqual(dig(self.pdp, "pdpData.code"), "323360")

    def test_absent_key_is_unknown(self):
        self.assertIs(dig(self.pdp, "pdpData.nothing"), UNKNOWN)

    def test_walking_through_a_scalar_is_unknown_not_an_error(self):
        self.assertIs(dig(self.pdp, "pdpData.code.deeper"), UNKNOWN)

    def test_null_becomes_unknown(self):
        self.assertIs(dig({"a": None}, "a"), UNKNOWN)

    def test_list_index(self):
        self.assertEqual(dig({"xs": [{"v": 9}]}, "xs.0.v"), 9)

    def test_out_of_range_index_is_unknown(self):
        self.assertIs(dig({"xs": []}, "xs.4"), UNKNOWN)

    def test_dig_str_rejects_the_empty_string(self):
        """Croma writes "" for 'no value'; that is an absence, not data."""
        self.assertIs(dig_str(self.pdp, "pdpData.empty"), UNKNOWN)

    def test_dig_str_stringifies_numbers(self):
        self.assertEqual(dig_str(self.pdp, "pdpData.n"), "7")

    def test_dig_str_rejects_booleans(self):
        """False must never become the string 'False'."""
        self.assertIs(dig_str(self.pdp, "pdpData.flag"), UNKNOWN)


if __name__ == "__main__":
    unittest.main()
