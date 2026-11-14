"""Category profiles: resolution, the laptop migration, and cross-category safety.

Only LAPTOP is admitted today. The cross-category cases register a stand-in TABLET
profile for the duration of one test — enough to prove that resolution and the
category gate behave correctly once a second category exists, without admitting a
real tablet profile before a tablet capture does (see profiles.py §Admission rule).
"""

from __future__ import annotations

import itertools
import unittest
from enum import Enum
from unittest import mock

from croma_intel.domain.candidate import CandidateProduct, DiscoveryContext
from croma_intel.domain.matching import MatchClassification, ModelAvailability, ReasonCode
from croma_intel.matching import classifier, profiles
from croma_intel.matching.gates import accessory_gate, category_gate
from croma_intel.matching.profiles import (
    LAPTOP,
    MAX_CRITICAL_SPECS,
    PROFILES,
    CategoryProfile,
    ProductCategory,
    category_crumbs,
    resolve_category,
    resolve_product_category,
)
from croma_intel.matching.spec_compare import MIN_KNOWN_CRITICAL_SPECS
from croma_intel.normalization.spec_value import CRITICAL_SPECS, SpecKey
from croma_intel.platforms.accessory_filter import PRODUCT_ANCHORS

CROMA_LAPTOP_PATH = ("Computers & Tablets", "Laptops", "Gaming Laptops")


class _StandIn(str, Enum):
    TABLET = "TABLET"
    APPLIANCE = "APPLIANCE"


STAND_IN_TABLET = CategoryProfile(
    category=_StandIn.TABLET,  # type: ignore[arg-type]
    category_tokens=("tablet",),
    critical_specs=(SpecKey.RAM_GB, SpecKey.STORAGE_GB),
    min_known_critical_specs=2,
    product_anchors=("tablet", "tab"),
)


def make(
    *,
    title: object = "Lenovo LOQ Gaming Laptop 16GB 512GB RTX 3050",
    category: tuple[str, ...] = CROMA_LAPTOP_PATH,
    model: object = "83S000FYIN",
    platform: str = "vijay_sales",
) -> CandidateProduct:
    return CandidateProduct(
        discovery=DiscoveryContext(platform=platform, source_url="https://x/p/1"),
        title=title,
        model_number=model,
        category_path=category,
        platform_product_id="1",
        url_derived_id="1",
        specs={"ram.size_gb": "16"},
        model_availability=ModelAvailability.ALWAYS,
    )


class TestLaptopMigrationIsBehaviourPreserving(unittest.TestCase):
    """Moving laptops onto a profile must not move a single laptop verdict."""

    def test_laptop_profile_carries_the_validated_constants(self):
        self.assertEqual(LAPTOP.critical_specs, CRITICAL_SPECS)
        self.assertEqual(LAPTOP.min_known_critical_specs, MIN_KNOWN_CRITICAL_SPECS)
        self.assertEqual(LAPTOP.product_anchors, PRODUCT_ANCHORS)

    def test_only_laptop_is_admitted(self):
        """Admission requires a real Croma capture; only laptops have one."""
        self.assertEqual([p.category for p in PROFILES], [ProductCategory.LAPTOP])

    def test_verdict_records_the_category_it_was_judged_as(self):
        verdict = classifier.classify(make(platform="croma"), make())
        self.assertEqual(verdict.extra["category"], "LAPTOP")


class TestResolution(unittest.TestCase):
    def test_croma_laptop_path(self):
        self.assertIs(resolve_category(CROMA_LAPTOP_PATH), LAPTOP)

    def test_flipkart_brand_leaf(self):
        self.assertIs(resolve_category(("Home", "Computers", "Laptops", "Lenovo Laptops")), LAPTOP)

    def test_absent_and_unrelated_paths_are_unresolved(self):
        self.assertIsNone(resolve_category(()))
        self.assertIsNone(resolve_category(("Home", "Kitchen Appliances", "Mixer Grinders")))

    def test_tokens_match_word_starts_not_substrings(self):
        self.assertIsNone(resolve_category(("Minicomputer Parts",)))

    def test_leaf_decides_despite_a_misleading_root(self):
        """Croma's L0 for a laptop is 'Computers & Tablets'."""
        with mock.patch.object(profiles, "PROFILES", (LAPTOP, STAND_IN_TABLET)):
            self.assertIs(resolve_category(CROMA_LAPTOP_PATH), LAPTOP)
            self.assertIs(
                resolve_category(("Computers & Tablets", "Tablets", "Android Tablets")),
                STAND_IN_TABLET,
            )

    def test_an_ambiguous_deciding_crumb_resolves_to_nothing(self):
        with mock.patch.object(profiles, "PROFILES", (LAPTOP, STAND_IN_TABLET)):
            self.assertIsNone(resolve_category(("Computers & Tablets",)))

    def test_the_product_title_is_not_a_category(self):
        """Croma's breadcrumb trail ends with the product name (323360 capture)."""
        title = "Lenovo Yoga 2-in-1 Laptop with Pen, flips into tablet mode"
        product = make(title=title, category=(*CROMA_LAPTOP_PATH, title))
        self.assertEqual(category_crumbs(product), CROMA_LAPTOP_PATH)
        with mock.patch.object(profiles, "PROFILES", (LAPTOP, STAND_IN_TABLET)):
            self.assertIs(resolve_product_category(product), LAPTOP)


class TestProfileValidation(unittest.TestCase):
    def _profile(self, **overrides: object) -> CategoryProfile:
        fields: dict[str, object] = {
            "category": ProductCategory.LAPTOP,
            "category_tokens": ("laptop",),
            "critical_specs": (SpecKey.RAM_GB,),
            "min_known_critical_specs": 1,
            "product_anchors": ("laptop",),
        }
        fields.update(overrides)
        return CategoryProfile(**fields)  # type: ignore[arg-type]

    def test_rejects_empty_and_duplicate_sets(self):
        for overrides in (
            {"category_tokens": ()},
            {"critical_specs": ()},
            {"critical_specs": (SpecKey.RAM_GB, SpecKey.RAM_GB), "min_known_critical_specs": 1},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self._profile(**overrides)

    def test_minimum_known_must_be_satisfiable(self):
        for minimum in (0, 2):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                self._profile(min_known_critical_specs=minimum)

    def test_critical_set_cannot_let_score_cross_a_class(self):
        ranks = sorted(set(classifier._CLASSIFICATION_RANK.values()), reverse=True)
        smallest_gap = min(a - b for a, b in itertools.pairwise(ranks))
        self.assertLess(MAX_CRITICAL_SPECS, smallest_gap)
        too_many = tuple(SpecKey)[: MAX_CRITICAL_SPECS + 1]
        if len(too_many) > MAX_CRITICAL_SPECS:
            with self.assertRaises(ValueError):
                self._profile(critical_specs=too_many)


class TestCrossCategorySafety(unittest.TestCase):
    def test_reference_with_unresolved_category_is_never_matched(self):
        """Rule 10: no CRITICAL set applies, so an exact model must not carry it."""
        reference = make(category=("Home", "Kitchen Appliances"), platform="croma")
        verdict = classifier.classify(reference, make(category=("Home", "Kitchen Appliances")))
        self.assertIs(verdict.classification, MatchClassification.INSUFFICIENT_EVIDENCE)
        self.assertIs(verdict.reason_code, ReasonCode.REFERENCE_CATEGORY_UNRESOLVED)

    def test_a_tablet_under_computers_is_rejected_for_a_laptop(self):
        """'Computers & Tablets > Tablets' contains 'computer', a laptop token."""
        reference = make(platform="croma")
        tablet = make(title="Lenovo Tab M11", category=("Computers & Tablets", "Tablets"))
        with mock.patch.object(profiles, "PROFILES", (LAPTOP, STAND_IN_TABLET)):
            gate = category_gate(reference, tablet)
        self.assertFalse(gate.passed)
        self.assertIs(gate.reason_code, ReasonCode.GATE_CATEGORY_MISMATCH)

    def test_absent_candidate_category_still_defers(self):
        self.assertTrue(category_gate(make(platform="croma"), make(category=())).passed)

    def test_accessory_anchors_follow_the_reference_category(self):
        """Laptop anchors know nothing of appliances: 'stand' outside the head noun
        with no anchor present reads as an accessory. The profile's anchors fix it."""
        fan = make(title="Crompton High Speed 400 mm Pedestal Fan with Stand", category=())
        self.assertFalse(accessory_gate(fan).passed)
        appliance = CategoryProfile(
            category=_StandIn.APPLIANCE,  # type: ignore[arg-type]
            category_tokens=("fan",),
            critical_specs=(SpecKey.OS,),
            min_known_critical_specs=1,
            product_anchors=("fan", "mm"),
        )
        self.assertTrue(accessory_gate(fan, appliance).passed)


if __name__ == "__main__":
    unittest.main()
