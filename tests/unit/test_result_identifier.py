"""Result identification — the "do not guess" guarantees.

M1 requires: must not simply choose the first result; must return a structured
failure when confidence is absent. Architectural rules 2, 3, 4 and 10 all surface here.
"""

from __future__ import annotations

import unittest

from croma_intel.platforms.croma.result_identifier import (
    Confidence,
    ResultIdentifier,
    SearchCandidate,
    contains_model_token,
    pdp_id_from_url,
)

SKU = "323360"
MODEL = "83S000FYIN"


def cand(position, url, title="", sku_text=""):
    return SearchCandidate(position=position, title=title, url=url, sku_text=sku_text)


class TestPdpIdExtraction(unittest.TestCase):
    def test_extracts_numeric_id(self):
        self.assertEqual(pdp_id_from_url("https://www.croma.com/lenovo-loq/p/323360"), "323360")

    def test_extracts_with_query_string(self):
        self.assertEqual(pdp_id_from_url("https://www.croma.com/x/p/312746?utm=a"), "312746")

    def test_non_pdp_url_returns_none(self):
        self.assertIsNone(pdp_id_from_url("https://www.croma.com/computers-tablets/laptops/c/20"))

    def test_empty_url_returns_none(self):
        self.assertIsNone(pdp_id_from_url(""))


class TestBoundarySafeModelToken(unittest.TestCase):
    """Architectural rule 4, read side. Validated against M0 §10.3 real data."""

    def test_whole_token_matches(self):
        self.assertTrue(contains_model_token("Lenovo LOQ 83S000FYIN Gaming Laptop", MODEL))

    def test_delimiter_bounded_suffix_matches(self):
        self.assertTrue(contains_model_token("Lenovo 83S000FYIN-IND laptop", MODEL))

    def test_trailing_alphanumeric_does_not_match(self):
        """83S000FYINX is a different code and must never match 83S000FYIN."""
        self.assertFalse(contains_model_token("Lenovo 83S000FYINX laptop", MODEL))

    def test_leading_alphanumeric_does_not_match(self):
        self.assertFalse(contains_model_token("Lenovo 183S000FYIN laptop", MODEL))

    def test_us_variant_does_not_match_india_variant(self):
        """M0 §10.3 proof 1: 83S00000US and 83S000FYIN are different products."""
        self.assertFalse(contains_model_token("Lenovo LOQ-E 83S00000US", MODEL))

    def test_edit_distance_one_does_not_match(self):
        """M0 §10.3 proof 3: 83DV018NIN vs 83DV018QIN differ in RAM and GPU."""
        self.assertFalse(contains_model_token("Lenovo LOQ 83DV018QIN", "83DV018NIN"))

    def test_case_insensitive(self):
        self.assertTrue(contains_model_token("lenovo loq 83s000fyin gaming", MODEL))

    def test_empty_inputs_are_false_not_true(self):
        """Rule 3: missing information must never produce a match."""
        self.assertFalse(contains_model_token("", MODEL))
        self.assertFalse(contains_model_token("Lenovo LOQ", ""))
        self.assertFalse(contains_model_token("", ""))


class TestDoesNotPickFirstResult(unittest.TestCase):
    """The single most important behaviour in this module."""

    def setUp(self):
        self.identifier = ResultIdentifier()

    def test_picks_sku_match_not_position_one(self):
        candidates = [
            cand(1, "https://www.croma.com/other-laptop/p/999999", "Some Other Laptop"),
            cand(2, "https://www.croma.com/another/p/888888", "Another Laptop"),
            cand(3, f"https://www.croma.com/lenovo-loq/p/{SKU}", "Lenovo LOQ 15ARP10E"),
        ]
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.CONFIDENT.value)
        self.assertIsNotNone(report.chosen)
        self.assertEqual(report.chosen.position, 3)
        self.assertIn(SKU, report.chosen.url)

    def test_refuses_when_no_sku_evidence_even_with_candidates(self):
        candidates = [
            cand(1, "https://www.croma.com/a/p/111111", "Lenovo LOQ Gaming Laptop"),
            cand(2, "https://www.croma.com/b/p/222222", "Lenovo LOQ Gaming Laptop"),
        ]
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.NOT_FOUND.value)
        self.assertIsNone(report.chosen)
        self.assertIn("refusing to fall back to first result", report.reason)

    def test_exact_model_is_decisive_at_tier_2(self):
        """Spec change (M1 correction): exact model is rank 2, and is decisive.

        Previously this asserted model-alone was insufficient. That was a design
        choice, not an architectural rule, and the M1 correction overrides it: an
        exact boundary-safe MTM identifies one configuration (M0 §10.3 — 83DV018NIN
        vs 83DV018QIN differ by one character and are different products), so it is
        identity evidence rather than similarity.
        """
        candidates = [
            cand(1, "https://www.croma.com/a/p/111111", f"Lenovo LOQ {MODEL} Gaming Laptop 16GB SSD")
        ]
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.CONFIDENT.value)
        self.assertEqual(report.chosen.position, 1)
        self.assertIn("MODEL_EXACT", report.reason)

    def test_exact_sku_outranks_exact_model(self):
        """Tier 1 beats tier 2 — the SKU is the stronger identity signal."""
        candidates = [
            cand(1, "https://www.croma.com/decoy/p/111111", f"Lenovo LOQ {MODEL} Gaming Laptop SSD"),
            cand(2, f"https://www.croma.com/target/p/{SKU}", "Lenovo LOQ 15ARP10E Gaming Laptop SSD"),
        ]
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.CONFIDENT.value)
        self.assertEqual(report.chosen.position, 2)
        self.assertIn("SKU_EXACT", report.reason)

    def test_weak_similarity_alone_still_cannot_promote(self):
        """Rule 10 preserved: brand/series words are not identity."""
        candidates = [
            cand(1, "https://www.croma.com/a/p/111111", "Lenovo LOQ Gaming Laptop 16GB 512GB SSD"),
            cand(2, "https://www.croma.com/b/p/222222", "Lenovo LOQ 15 Gaming Laptop RTX 3050"),
        ]
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.NOT_FOUND.value)
        self.assertIsNone(report.chosen)
        self.assertIn("refusing to fall back to first result", report.reason)


class TestStructuredFailures(unittest.TestCase):
    def setUp(self):
        self.identifier = ResultIdentifier()

    def test_zero_candidates(self):
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=[])
        self.assertEqual(report.confidence, Confidence.NOT_FOUND.value)
        self.assertEqual(report.candidates_considered, 0)
        self.assertIn("zero candidate", report.reason)

    def test_duplicate_sku_evidence_is_ambiguous_not_arbitrary(self):
        candidates = [
            cand(1, f"https://www.croma.com/a/p/{SKU}", "Listing A"),
            cand(2, f"https://www.croma.com/b/p/{SKU}", "Listing B"),
        ]
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.AMBIGUOUS.value)
        self.assertIsNone(report.chosen)
        self.assertIn("refusing to guess", report.reason)

    def test_report_is_json_serializable(self):
        import json

        candidates = [cand(1, f"https://www.croma.com/a/p/{SKU}", "Lenovo LOQ")]
        report = self.identifier.identify(sku=SKU, model=MODEL, candidates=candidates)
        json.dumps(report.to_dict())  # must not raise

    def test_ok_property_matches_confidence(self):
        confident = self.identifier.identify(
            sku=SKU, model=MODEL, candidates=[cand(1, f"https://www.croma.com/a/p/{SKU}")]
        )
        self.assertTrue(confident.ok)
        self.assertFalse(self.identifier.identify(sku=SKU, model=MODEL, candidates=[]).ok)


class TestSkuTextEvidence(unittest.TestCase):
    def test_sku_rendered_on_card_is_decisive(self):
        candidates = [cand(1, "https://www.croma.com/a/p/999999", "Lenovo LOQ", sku_text=SKU)]
        report = ResultIdentifier().identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.CONFIDENT.value)

    def test_partial_sku_text_is_not_decisive(self):
        candidates = [cand(1, "https://www.croma.com/a/p/999999", "Lenovo LOQ", sku_text="32336")]
        report = ResultIdentifier().identify(sku=SKU, model=MODEL, candidates=candidates)
        self.assertEqual(report.confidence, Confidence.NOT_FOUND.value)


if __name__ == "__main__":
    unittest.main()
