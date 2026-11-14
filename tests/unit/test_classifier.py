"""The decision table, spec comparison semantics, and end-to-end classification."""

from __future__ import annotations

import itertools
import unittest

from croma_intel.domain.candidate import CandidateProduct, DiscoveryContext
from croma_intel.domain.matching import (
    MatchClassification,
    ModelAvailability,
    ModelComparison,
    ReasonCode,
    SpecComparison,
    SpecVerdict,
)
from croma_intel.domain.product import UNKNOWN
from croma_intel.matching.classifier import DECISION_TABLE, classify, order_score
from croma_intel.matching.spec_compare import (
    MIN_KNOWN_CRITICAL_SPECS,
    SpecState,
    compare_spec,
    compare_specs,
    spec_state,
)
from croma_intel.normalization.spec_value import CRITICAL_SPECS

FULL_SPECS = {
    "cpu.model": "7535HS",
    "ram.size_gb": "16",
    "storage.size_gb": "512",
    "gpu.model": "RTX 3050",
    "gpu.vram_gb": "6",
    "display.size_in": "15.6",
    "display.refresh_hz": "144",
    "os": "Windows 11 Home",
}

CATEGORY = ("Computers & Tablets", "Laptops", "Gaming Laptops")


def make(
    *,
    model: object = "83S000FYIN",
    series: object = "LOQ 15ARP10E",
    specs: dict | None = None,
    availability: ModelAvailability = ModelAvailability.ALWAYS,
    title: str = "Lenovo LOQ Gaming Laptop 16GB 512GB RTX 3050",
    platform: str = "vijay_sales",
) -> CandidateProduct:
    return CandidateProduct(
        discovery=DiscoveryContext(platform=platform, source_url="https://x/p/1"),
        title=title,
        model_number=model,
        model_series=series,
        category_path=CATEGORY,
        platform_product_id="1",
        url_derived_id="1",
        specs=FULL_SPECS if specs is None else specs,
        model_availability=availability,
    )


REFERENCE = make(platform="croma")


class TestDecisionTableIsTotal(unittest.TestCase):
    def test_every_combination_has_an_entry(self):
        missing = [
            (m, s) for m in ModelComparison for s in SpecState if (m, s) not in DECISION_TABLE
        ]
        self.assertEqual(missing, [], f"decision table is not total: {missing}")

    def test_size_is_the_full_cross_product(self):
        self.assertEqual(len(DECISION_TABLE), len(list(ModelComparison)) * len(list(SpecState)))

    def test_every_entry_carries_a_reason_code(self):
        for key, (classification, code) in DECISION_TABLE.items():
            with self.subTest(key=key):
                self.assertIsInstance(classification, MatchClassification)
                self.assertIsInstance(code, ReasonCode)

    def test_no_weights_or_thresholds_in_the_module(self):
        """Constraint 5: deterministic table, not a weighted scorer."""
        import ast
        import pathlib

        import croma_intel.matching.classifier as module

        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        floats = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, float)
        ]
        self.assertEqual(floats, [], f"no float weights permitted, found {floats}")


class TestSpecComparisonSemantics(unittest.TestCase):
    def test_unknown_on_either_side_yields_unknown(self):
        self.assertIs(compare_spec("os", UNKNOWN, "Windows 11"), SpecVerdict.UNKNOWN)
        self.assertIs(compare_spec("os", "Windows 11", UNKNOWN), SpecVerdict.UNKNOWN)

    def test_unknown_never_counts_as_agreement(self):
        comparison = compare_specs(REFERENCE, make(specs={}))
        self.assertEqual(comparison.matched, ())
        self.assertEqual(len(comparison.unknown), len(CRITICAL_SPECS))
        self.assertFalse(comparison.all_known_match)

    def test_every_critical_spec_lands_in_exactly_one_bucket(self):
        comparison = compare_specs(REFERENCE, make(specs={"ram.size_gb": "24"}))
        total = len(comparison.matched) + len(comparison.mismatched) + len(comparison.unknown)
        self.assertEqual(total, len(CRITICAL_SPECS))

    def test_capacities_compare_numerically(self):
        self.assertIs(compare_spec("ram.size_gb", "16", "16"), SpecVerdict.MATCH)
        self.assertIs(compare_spec("ram.size_gb", "16", "24"), SpecVerdict.MISMATCH)

    def test_display_size_uses_tolerance(self):
        self.assertIs(compare_spec("display.size_in", "15.598", "15.590"), SpecVerdict.MATCH)
        self.assertIs(compare_spec("display.size_in", "15.6", "16.0"), SpecVerdict.MISMATCH)

    def test_gpu_variant_suffix_is_a_mismatch(self):
        self.assertIs(compare_spec("gpu.model", "RTX 3050", "RTX 3050A"), SpecVerdict.MISMATCH)

    def test_comparison_is_case_insensitive_but_not_fuzzy(self):
        self.assertIs(compare_spec("os", "Windows 11 Home", "windows 11 home"), SpecVerdict.MATCH)
        self.assertIs(compare_spec("os", "Windows 11 Home", "Windows 11 Pro"), SpecVerdict.MISMATCH)


class TestSpecState(unittest.TestCase):
    def test_all_match(self):
        self.assertIs(spec_state(compare_specs(REFERENCE, make())), SpecState.ALL_MATCH)

    def test_mismatch_dominates(self):
        specs = dict(FULL_SPECS, ram_placeholder="x")
        specs["ram.size_gb"] = "24"
        state = spec_state(compare_specs(REFERENCE, make(specs=specs)))
        self.assertIs(state, SpecState.ANY_MISMATCH)

    def test_partial_requires_the_minimum(self):
        partial = {k: FULL_SPECS[k] for k in list(FULL_SPECS)[:MIN_KNOWN_CRITICAL_SPECS]}
        self.assertIs(spec_state(compare_specs(REFERENCE, make(specs=partial))), SpecState.PARTIAL)

    def test_below_the_minimum_is_insufficient(self):
        few = {k: FULL_SPECS[k] for k in list(FULL_SPECS)[: MIN_KNOWN_CRITICAL_SPECS - 1]}
        self.assertIs(
            spec_state(compare_specs(REFERENCE, make(specs=few))), SpecState.INSUFFICIENT
        )


class TestClassification(unittest.TestCase):
    def test_exact_model_and_matching_specs(self):
        verdict = classify(REFERENCE, make())
        self.assertIs(verdict.classification, MatchClassification.EXACT_MATCH)
        self.assertIs(verdict.reason_code, ReasonCode.MODEL_EXACT_SPECS_CORROBORATE)
        self.assertTrue(verdict.is_positive)

    def test_exact_model_with_contradicting_specs_is_conflict(self):
        specs = dict(FULL_SPECS)
        specs["gpu.model"] = "RTX 4050"
        verdict = classify(REFERENCE, make(specs=specs))
        self.assertIs(verdict.classification, MatchClassification.CONFLICT)
        self.assertIs(verdict.reason_code, ReasonCode.MODEL_EXACT_SPEC_CONTRADICTION)
        self.assertTrue(verdict.requires_review)
        self.assertFalse(verdict.is_positive)

    def test_conflict_does_not_assert_a_cause(self):
        """Constraint 3: contradictory evidence, cause unstated."""
        specs = dict(FULL_SPECS)
        specs["gpu.model"] = "RTX 4050"
        verdict = classify(REFERENCE, make(specs=specs))
        blob = f"{verdict.reason} {verdict.reason_code.value}".lower()
        for causal in ("mis-extract", "misextract", "extraction error", "bug"):
            self.assertNotIn(causal, blob)

    def test_exact_model_without_specs_is_unverified(self):
        verdict = classify(REFERENCE, make(specs={}))
        self.assertIs(verdict.classification, MatchClassification.EXACT_MATCH_UNVERIFIED)
        self.assertTrue(verdict.requires_review)

    def test_different_model_but_every_critical_spec_matches(self):
        """Approved 2026-08-27: same configuration, different SKU.

        F9 is not reopened. It warned against matching on model *similarity* without
        checking specs; here all eight CRITICAL specs were compared and agree, so the
        RAM/GPU difference F9 found between 83DV018NIN and 83DV018QIN cannot be
        present. The verdict still records model_comparison=DIFFERENT.
        """
        verdict = classify(REFERENCE, make(model="83S000CPIN"))
        self.assertIs(verdict.classification, MatchClassification.CONFIGURATION_MATCH)
        self.assertIs(verdict.reason_code, ReasonCode.SPECS_MATCH_MODEL_DIFFERENT)
        self.assertIs(verdict.model_comparison, ModelComparison.DIFFERENT)
        self.assertTrue(verdict.is_positive)

    def test_different_model_with_a_spec_mismatch_is_still_different(self):
        """The F9 case itself: one differing CRITICAL spec keeps it DIFFERENT."""
        specs = dict(FULL_SPECS)
        specs["gpu.model"] = "RTX 4050"          # 83DV018NIN vs 83DV018QIN shape
        verdict = classify(REFERENCE, make(model="83DV018QIN", series="LOQ 15IRX9", specs=specs))
        self.assertIs(verdict.classification, MatchClassification.DIFFERENT)
        self.assertIs(verdict.reason_code, ReasonCode.MODEL_DIFFERENT)

    def test_different_model_with_incomplete_specs_is_not_promoted(self):
        """Rule 3: unverified specs cannot buy a CONFIGURATION_MATCH.

        Lands on VARIANT rather than DIFFERENT because the series still matches -
        informative, but pointedly *not* a positive match.
        """
        partial = {k: FULL_SPECS[k] for k in list(FULL_SPECS)[:5]}
        verdict = classify(REFERENCE, make(model="83S000CPIN", specs=partial))
        self.assertIs(verdict.classification, MatchClassification.VARIANT)
        self.assertFalse(verdict.is_positive)

    def test_edit_distance_one_never_matches(self):
        """F9 proof 3, end to end."""
        reference = make(model="83DV018NIN", series="LOQ 15")
        verdict = classify(reference, make(model="83DV018QIN", series="LOQ 15"))
        self.assertNotEqual(verdict.classification, MatchClassification.EXACT_MATCH)
        self.assertIs(verdict.model_comparison, ModelComparison.DIFFERENT)


class TestSpecificationOnlyPath(unittest.TestCase):
    """The Flipkart path (F1). Constraint 1: specification-first."""

    def _flipkart(self, specs: dict) -> CandidateProduct:
        return make(
            model=UNKNOWN,
            series="15ARP9",
            specs=specs,
            availability=ModelAvailability.NEVER,
            platform="flipkart",
            title="Lenovo LOQ AMD Ryzen 7 - (24 GB/512 GB SSD) 15ARP9 Gaming Laptop",
        )

    def test_complete_specs_reach_configuration_match(self):
        verdict = classify(REFERENCE, self._flipkart(FULL_SPECS))
        self.assertIs(verdict.classification, MatchClassification.CONFIGURATION_MATCH)
        self.assertIs(verdict.reason_code, ReasonCode.SPECS_MATCH_MODEL_UNAVAILABLE)
        self.assertIs(verdict.model_comparison, ModelComparison.UNAVAILABLE)
        self.assertTrue(verdict.is_positive)

    def test_flipkart_can_never_reach_exact_match(self):
        """F1 stated as an executable property."""
        verdict = classify(REFERENCE, self._flipkart(FULL_SPECS))
        self.assertNotEqual(verdict.classification, MatchClassification.EXACT_MATCH)

    def test_summary_specs_alone_are_insufficient(self):
        """Rule 3. Flipkart's initial payload lands here until M3b-1 expands the table."""
        partial = {k: FULL_SPECS[k] for k in list(FULL_SPECS)[:5]}
        verdict = classify(REFERENCE, self._flipkart(partial))
        self.assertIs(verdict.classification, MatchClassification.INSUFFICIENT_EVIDENCE)
        self.assertIs(verdict.reason_code, ReasonCode.SPECS_PARTIAL_MODEL_UNAVAILABLE)

    def test_mismatching_specs_without_a_model_is_different(self):
        specs = dict(FULL_SPECS)
        specs["gpu.model"] = "RTX 4060"
        verdict = classify(REFERENCE, self._flipkart(specs))
        self.assertIs(verdict.classification, MatchClassification.DIFFERENT)
        self.assertIs(verdict.reason_code, ReasonCode.SPECS_MISMATCH_MODEL_UNAVAILABLE)


class TestGatesPrecedeClassification(unittest.TestCase):
    """Constraint 4 / rule 5."""

    def test_accessory_is_rejected_before_identity_is_considered(self):
        accessory = make(
            title="Laptop Sleeve for Lenovo LOQ 83S000FYIN",
            model="83S000FYIN",  # the accessory carries the exact reference MTM
        )
        verdict = classify(REFERENCE, accessory)
        self.assertIs(verdict.classification, MatchClassification.REJECTED)
        self.assertIs(verdict.reason_code, ReasonCode.GATE_ACCESSORY)

    def test_a_rejected_candidate_has_no_spec_comparison(self):
        verdict = classify(REFERENCE, make(title="Charger for Lenovo LOQ"))
        self.assertEqual(verdict.specs, SpecComparison())
        self.assertEqual(verdict.score, 0)

    def test_store_display_unit_is_rejected_despite_a_perfect_match(self):
        """F7: same model, same specs, still not comparable retail stock."""
        display_unit = make(title="Store Display Unit - Lenovo LOQ 15 Gaming Laptop")
        verdict = classify(REFERENCE, display_unit)
        self.assertIs(verdict.classification, MatchClassification.REJECTED)
        self.assertIs(verdict.reason_code, ReasonCode.GATE_CONDITION_NOT_NEW)


class TestScoreCannotChangeClass(unittest.TestCase):
    """Constraint 5: score orders within a class, never between."""

    def test_ranks_are_separated_by_more_than_the_maximum_bonus(self):
        max_bonus = len(CRITICAL_SPECS)
        ranks = sorted(
            {order_score(c, SpecComparison()) for c in MatchClassification}, reverse=True
        )
        gaps = [a - b for a, b in itertools.pairwise(ranks) if a != b]
        self.assertTrue(all(gap > max_bonus for gap in gaps), f"gaps {gaps} <= bonus {max_bonus}")

    def test_a_fully_corroborated_lower_class_never_outscores_a_bare_higher_one(self):
        full = SpecComparison(matched=tuple(k.value for k in CRITICAL_SPECS))
        self.assertGreater(
            order_score(MatchClassification.EXACT_MATCH, SpecComparison()),
            order_score(MatchClassification.CONFIGURATION_MATCH, full),
        )


class TestVerdictIsAuditable(unittest.TestCase):
    def test_every_verdict_carries_a_reason_code(self):
        for candidate in (
            make(), make(specs={}), make(model="83DV018QIN", series="X"),
            make(title="Laptop Bag for Lenovo"),
        ):
            with self.subTest(title=str(candidate.title)[:30]):
                self.assertIsInstance(classify(REFERENCE, candidate).reason_code, ReasonCode)

    def test_evidence_covers_every_critical_spec(self):
        verdict = classify(REFERENCE, make())
        spec_evidence = [e for e in verdict.evidence if e.kind.startswith("SPEC:")]
        self.assertEqual(len(spec_evidence), len(CRITICAL_SPECS))

    def test_verdict_serialises(self):
        import json

        payload = json.dumps(classify(REFERENCE, make()).to_dict())
        self.assertIn("EXACT_MATCH", payload)
        self.assertIn("reason_code", payload)


if __name__ == "__main__":
    unittest.main()
