"""End-to-end matching: the real Croma capture against transcribed competitor data.

The reference product comes from the genuine M1 capture via M2 extraction. The
candidates are **transcribed** from M0 — real listings, second-hand packaging. The
asymmetry is deliberate and is asserted below, so nobody later mistakes one for the
other.
"""

from __future__ import annotations

import json
import socket
import unittest
from pathlib import Path

from croma_intel.browser.artifacts import CaptureStore
from croma_intel.domain.candidate import CandidateProduct, DiscoveryContext, candidate_from_croma
from croma_intel.domain.matching import (
    MatchClassification,
    ModelAvailability,
    ModelComparison,
    ReasonCode,
)
from croma_intel.domain.product import UNKNOWN
from croma_intel.matching.classifier import classify
from croma_intel.normalization.spec_value import split_compound_row
from croma_intel.platforms.croma.pdp_extractor import extract_croma_pdp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CROMA_STORE = FIXTURES / "croma" / "pdp_323360"
CAPTURE_ID = "5df6c46a45c3c93d941a5c4c078cd7e5585613729b0e5844101c65ccd13c1f4b"
TRANSCRIBED = FIXTURES / "transcribed" / "competitor_listings.json"


def load_transcribed() -> dict:
    return json.loads(TRANSCRIBED.read_text(encoding="utf-8"))


def build_candidate(record: dict) -> CandidateProduct:
    """Project a transcribed record into a candidate, splitting its compound rows."""
    specs: dict[str, str] = {}
    for label, value in (record.get("compound_rows") or {}).items():
        for key, parsed in split_compound_row(label, value).items():
            specs.setdefault(key.value, parsed)
    if "os" not in specs and (record.get("compound_rows") or {}).get("OS"):
        specs["os"] = record["compound_rows"]["OS"]

    return CandidateProduct(
        discovery=DiscoveryContext(
            platform=record["platform"],
            source_url=record["source_url"],
            final_url=record["source_url"],
            query=record.get("query", ""),
            query_strategy=record.get("query_strategy", ""),
            position=record.get("position", 0),
        ),
        title=record["title"],
        model_number=record.get("model_number") or UNKNOWN,
        model_series=record.get("model_series") or UNKNOWN,
        platform_product_id=record.get("platform_product_id") or UNKNOWN,
        url_derived_id=record.get("url_derived_id") or UNKNOWN,
        category_path=tuple(record.get("category_path") or ()),
        specs=specs,
        spec_text=" ".join(f"{k}: {v}" for k, v in (record.get("compound_rows") or {}).items()),
        model_availability=ModelAvailability(record.get("model_availability", "UNKNOWN")),
    )


def by_id(listing_id: str) -> CandidateProduct:
    for record in load_transcribed()["listings"]:
        if record["id"] == listing_id:
            return build_candidate(record)
    raise KeyError(listing_id)


class TestFixtureLabellingDiscipline(unittest.TestCase):
    """§7 of the M3 brief. These must never be mistaken for captures."""

    def test_the_file_declares_itself_transcribed(self):
        data = load_transcribed()
        self.assertIn("NOT A CAPTURE", data["_warning"])
        self.assertEqual(data["_source_document"], "docs/recon/M0_RECON_FINDINGS.md")

    def test_every_record_cites_its_m0_section(self):
        for record in load_transcribed()["listings"]:
            with self.subTest(record=record["id"]):
                self.assertTrue(record["provenance"]["m0_section"].startswith("§"))
                self.assertEqual(record["provenance"]["evidence_kind"], "transcribed")

    def test_transcribed_fixtures_live_apart_from_real_captures(self):
        self.assertNotIn("transcribed", str(CROMA_STORE))
        self.assertFalse((TRANSCRIBED.parent / "manifests").exists())

    def test_the_readme_states_what_they_cannot_validate(self):
        readme = (TRANSCRIBED.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("NOT sufficient", readme)
        self.assertIn("extraction", readme)


class MatchingTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture = CaptureStore(CROMA_STORE).read(CAPTURE_ID)
        cls.reference = candidate_from_croma(extract_croma_pdp(capture))


class TestReferenceProjection(MatchingTestBase):
    """The real Croma product, projected into the neutral candidate form."""

    def test_identity_survives_projection(self):
        self.assertEqual(self.reference.model_number, "83S000FYIN")
        self.assertEqual(self.reference.model_series, "LOQ 15ARP10E")
        self.assertEqual(self.reference.platform, "croma")

    def test_critical_specs_are_populated_from_the_real_capture(self):
        self.assertEqual(self.reference.spec("cpu.model"), "7535HS")
        self.assertEqual(self.reference.spec("ram.size_gb"), "16")
        self.assertEqual(self.reference.spec("storage.size_gb"), "512")
        self.assertEqual(self.reference.spec("gpu.model"), "RTX 3050")
        self.assertEqual(self.reference.spec("gpu.vram_gb"), "6")
        self.assertEqual(self.reference.spec("display.size_in"), "15.6")
        self.assertEqual(self.reference.spec("display.refresh_hz"), "144")
        self.assertEqual(self.reference.spec("os"), "Windows 11 Home")

    def test_discovery_provenance_travels_with_it(self):
        self.assertEqual(self.reference.discovery.capture_id, CAPTURE_ID)
        self.assertEqual(self.reference.discovery.pincode, "560087")

    def test_absent_spec_is_unknown_not_empty(self):
        self.assertIs(self.reference.spec("nonexistent.key"), UNKNOWN)


class TestAgainstTranscribedListings(MatchingTestBase):
    def test_store_display_unit_is_rejected(self):
        """F7 — the silent-wrong hazard, end to end."""
        verdict = classify(self.reference, by_id("vs_83dv018qin_display_unit"))
        self.assertIs(verdict.classification, MatchClassification.REJECTED)
        self.assertIs(verdict.reason_code, ReasonCode.GATE_CONDITION_NOT_NEW)

    def test_accessory_carrying_a_model_code_is_rejected(self):
        """M0 §12.2 — T210 appeared in four of the first fifteen results."""
        verdict = classify(self.reference, by_id("fk_accessory_sleeve"))
        self.assertIs(verdict.classification, MatchClassification.REJECTED)
        self.assertIs(verdict.reason_code, ReasonCode.GATE_ACCESSORY)

    def test_url_id_mismatch_is_rejected(self):
        """F10 — the URL for 230914 served 243423."""
        verdict = classify(self.reference, by_id("vs_230914_redirect"))
        self.assertIs(verdict.classification, MatchClassification.REJECTED)
        self.assertIs(verdict.reason_code, ReasonCode.GATE_IDENTITY_REDIRECT)

    def test_a_different_lenovo_laptop_is_not_a_match(self):
        verdict = classify(self.reference, by_id("vs_83jc00jvin"))
        self.assertNotEqual(verdict.classification, MatchClassification.EXACT_MATCH)
        self.assertIs(verdict.model_comparison, ModelComparison.DIFFERENT)

    def test_reliance_digital_model_is_read_from_the_title_position_independently(self):
        candidate = by_id("rd_83dv00l9in")
        self.assertEqual(candidate.model_number, "83DV00L9IN")
        verdict = classify(self.reference, candidate)
        self.assertIs(verdict.model_comparison, ModelComparison.DIFFERENT)

    def test_flipkart_candidate_has_no_model_and_stays_specification_only(self):
        candidate = by_id("fk_15arp9_no_mtm")
        self.assertIs(candidate.model_number, UNKNOWN)
        self.assertIs(candidate.model_availability, ModelAvailability.NEVER)
        verdict = classify(self.reference, candidate)
        self.assertIs(verdict.model_comparison, ModelComparison.UNAVAILABLE)
        self.assertNotEqual(verdict.classification, MatchClassification.EXACT_MATCH)

    def test_every_verdict_preserves_discovery_provenance(self):
        """Constraint 13."""
        for listing_id in ("vs_83jc00jvin", "fk_15arp9_no_mtm", "rd_83dv00l9in"):
            with self.subTest(listing=listing_id):
                candidate = by_id(listing_id)
                self.assertTrue(candidate.discovery.source_url)
                self.assertTrue(candidate.discovery.platform)
                self.assertTrue(candidate.discovery.query)
                self.assertGreaterEqual(candidate.discovery.position, 1)


class TestCompoundRowsReduceToComparableSpecs(MatchingTestBase):
    """The justification for S1, proved against real transcribed rows."""

    def test_vijay_sales_compound_rows_become_canonical_specs(self):
        candidate = by_id("vs_83jc00jvin")
        self.assertEqual(candidate.spec("cpu.model"), "7435HS")
        self.assertEqual(candidate.spec("ram.size_gb"), "24")
        self.assertEqual(candidate.spec("storage.size_gb"), "512")
        self.assertEqual(candidate.spec("gpu.model"), "RTX 3050A")
        self.assertEqual(candidate.spec("display.refresh_hz"), "144")

    def test_the_gpu_variant_suffix_survives_and_causes_a_mismatch(self):
        """RTX 3050A is not RTX 3050. The reference has the latter."""
        candidate = by_id("vs_83jc00jvin")
        self.assertEqual(candidate.spec("gpu.model"), "RTX 3050A")
        self.assertEqual(self.reference.spec("gpu.model"), "RTX 3050")
        verdict = classify(self.reference, candidate)
        self.assertIn("gpu.model", verdict.specs.mismatched)

    def test_display_size_agrees_across_the_rounding_difference(self):
        """Vijay Sales publishes 39.6 cm; Croma publishes 15.6 in."""
        candidate = by_id("vs_83dv018qin_display_unit")
        self.assertEqual(candidate.spec("display.size_in"), "15.6")


class TestOfflineAndDeterministic(MatchingTestBase):
    def test_matching_runs_with_sockets_disabled(self):
        real_socket, real_create = socket.socket, socket.create_connection

        def blocked(*_a, **_kw):
            raise AssertionError("network access attempted during matching")

        socket.socket = blocked  # type: ignore[assignment]
        socket.create_connection = blocked  # type: ignore[assignment]
        try:
            verdict = classify(self.reference, by_id("vs_83jc00jvin"))
        finally:
            socket.socket = real_socket  # type: ignore[assignment]
            socket.create_connection = real_create  # type: ignore[assignment]
        self.assertIsInstance(verdict.reason_code, ReasonCode)

    def test_classification_is_a_pure_function(self):
        candidate = by_id("vs_83jc00jvin")
        first = json.dumps(classify(self.reference, candidate).to_dict(), sort_keys=True)
        second = json.dumps(classify(self.reference, candidate).to_dict(), sort_keys=True)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
