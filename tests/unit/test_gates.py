"""Hard gates. Every case is drawn from a real M0 finding."""

from __future__ import annotations

import unittest

from croma_intel.domain.candidate import CandidateProduct, DiscoveryContext
from croma_intel.domain.matching import GateKind, ListingCondition, ReasonCode
from croma_intel.domain.product import UNKNOWN
from croma_intel.matching.gates import (
    GATE_ORDER,
    accessory_gate,
    bundle_gate,
    category_gate,
    condition_gate,
    detect_condition,
    first_failure,
    identity_redirect_gate,
    run_gates,
)


def make(
    title: str = "Lenovo LOQ 15ARP9 Gaming Laptop 24GB RAM 512GB SSD RTX 4050",
    *,
    platform: str = "vijay_sales",
    category: tuple[str, ...] = ("Home", "Computers and Laptops", "Laptops", "Gaming Laptops"),
    product_id: object = "241406",
    url_id: object = "241406",
    **kw: object,
) -> CandidateProduct:
    return CandidateProduct(
        discovery=DiscoveryContext(platform=platform, source_url="https://x/p/1"),
        title=title,
        category_path=category,
        platform_product_id=product_id,
        url_derived_id=url_id,
        **kw,  # type: ignore[arg-type]
    )


REFERENCE = make(
    title="Lenovo LOQ 15ARP10E AMD Ryzen 5 Gaming Laptop",
    platform="croma",
    category=("Computers & Tablets", "Laptops", "Gaming Laptops"),
    product_id="323360",
    url_id="323360",
)


class TestGateOrder(unittest.TestCase):
    def test_accessory_and_category_precede_identity_work(self):
        """Rule 5 / constraint 4: eligibility is settled before identity."""
        self.assertEqual(GATE_ORDER[0], GateKind.ACCESSORY)
        self.assertEqual(GATE_ORDER[1], GateKind.CATEGORY)

    def test_order_is_fixed_and_complete(self):
        self.assertEqual(len(GATE_ORDER), len(set(GATE_ORDER)))
        self.assertEqual(set(GATE_ORDER), set(GateKind))

    def test_all_gates_run_even_after_a_failure(self):
        gates = run_gates(REFERENCE, make(title="Laptop Sleeve for Lenovo LOQ"))
        self.assertEqual(len(gates), len(GATE_ORDER))

    def test_first_failure_follows_gate_order_not_input_order(self):
        candidate = make(
            title="Store Display Unit - Laptop Bag for Lenovo",
            category=("Home", "Laptop Bags"),
        )
        failure = first_failure(run_gates(REFERENCE, candidate))
        assert failure is not None
        self.assertEqual(failure.kind, GateKind.ACCESSORY)


class TestConditionGate(unittest.TestCase):
    """F7 — the hazard that would otherwise pass silently."""

    def test_store_display_unit_prefix_is_detected(self):
        title = (
            "Store Display Unit - Lenovo LOQ 15 Gaming Laptop (13th Gen Intel Core i5/"
            " 24GB RAM/ 512GB SSD) 83DV018QIN"
        )
        self.assertIs(detect_condition(title), ListingCondition.STORE_DISPLAY_UNIT)

    def test_store_display_unit_is_rejected_with_a_reason_code(self):
        result = condition_gate(make(title="Store Display Unit - Lenovo LOQ 15 Gaming Laptop"))
        self.assertFalse(result.passed)
        self.assertIs(result.reason_code, ReasonCode.GATE_CONDITION_NOT_NEW)

    def test_other_conditions(self):
        for title, expected in (
            ("Open Box Lenovo LOQ", ListingCondition.OPEN_BOX),
            ("Refurbished Lenovo LOQ", ListingCondition.REFURBISHED),
            ("Renewed Lenovo LOQ", ListingCondition.RENEWED),
            ("Pre-owned Lenovo LOQ", ListingCondition.USED),
        ):
            with self.subTest(title=title):
                self.assertIs(detect_condition(title), expected)

    def test_unmarked_title_is_treated_as_new_by_default(self):
        """No platform observed in M0 labels new stock 'New'."""
        self.assertTrue(condition_gate(make()).passed)

    def test_unmarked_can_be_made_strict(self):
        result = condition_gate(make(), assume_new_when_unmarked=False)
        self.assertFalse(result.passed)
        self.assertIs(result.reason_code, ReasonCode.GATE_CONDITION_UNKNOWN)

    def test_the_assumption_is_stated_in_the_detail(self):
        self.assertIn("assumption", condition_gate(make()).detail)


class TestAccessoryGate(unittest.TestCase):
    """M0 §12.2 — the ₹548 sleeve that scores as spec-adjacent to a ₹1,08,990 laptop."""

    def test_sleeve_carrying_the_size_token_is_rejected(self):
        result = accessory_gate(
            make(
                title="CASE_BO 15.6 Toploder T210 black-row Laptop Sleeve/Cover",
                category=("Home", "Computers", "Laptop Accessories", "Laptop Sleeves"),
            )
        )
        self.assertFalse(result.passed)
        self.assertIs(result.reason_code, ReasonCode.GATE_ACCESSORY)

    def test_charger_for_brand_is_rejected(self):
        self.assertFalse(accessory_gate(make(title="Charger for Lenovo LOQ 83S000FYIN")).passed)

    def test_a_laptop_mentioning_a_backlit_keyboard_is_not_an_accessory(self):
        """The head-noun guard. A feature mention is not the product type."""
        result = accessory_gate(
            make(title="Lenovo LOQ Gaming Laptop 16GB RAM 512GB SSD RTX 3050 Backlit Keyboard")
        )
        self.assertTrue(result.passed)

    def test_accessory_rejection_precedes_any_identity_comparison(self):
        accessory = make(
            title="Laptop Bag for Lenovo LOQ 83S000FYIN",
            category=("Home", "Laptop Bags"),
        )
        failure = first_failure(run_gates(REFERENCE, accessory))
        assert failure is not None
        self.assertEqual(failure.kind, GateKind.ACCESSORY)


class TestCategoryGate(unittest.TestCase):
    def test_brand_leaf_and_product_type_leaf_both_pass(self):
        """Flipkart terminates in a brand node, Vijay Sales in a product type."""
        for path in (
            ("Home", "Computers", "Laptops", "Lenovo Laptops"),
            ("Home", "Computers and Laptops", "Laptops", "Gaming Laptops"),
        ):
            with self.subTest(path=path):
                self.assertTrue(category_gate(REFERENCE, make(category=path)).passed)

    def test_non_computing_category_is_rejected(self):
        result = category_gate(REFERENCE, make(category=("Home", "Beauty", "Baby Care")))
        self.assertFalse(result.passed)
        self.assertIs(result.reason_code, ReasonCode.GATE_CATEGORY_MISMATCH)

    def test_absent_category_passes_and_defers_to_specs(self):
        """Rejecting on absence would discard every platform without a breadcrumb."""
        result = category_gate(REFERENCE, make(category=()))
        self.assertTrue(result.passed)
        self.assertIn("deferred", result.detail)


class TestIdentityRedirectGate(unittest.TestCase):
    """F10 — a URL for 230914 served 243423."""

    def test_disagreeing_ids_are_rejected(self):
        result = identity_redirect_gate(make(product_id="243423", url_id="230914"))
        self.assertFalse(result.passed)
        self.assertIs(result.reason_code, ReasonCode.GATE_IDENTITY_REDIRECT)

    def test_agreeing_ids_pass(self):
        self.assertTrue(identity_redirect_gate(make(product_id="1", url_id="1")).passed)

    def test_unknown_id_is_not_evidence_of_a_redirect(self):
        self.assertTrue(identity_redirect_gate(make(product_id=UNKNOWN, url_id="1")).passed)


class TestBundleGate(unittest.TestCase):
    """M0 §6.5 — flags, never rejects."""

    def test_bundle_is_flagged_but_passes(self):
        result = bundle_gate(
            make(title="Lenovo LOQ Gaming Laptop with Free Portronics Soundbar (Worth ₹3,199)")
        )
        self.assertTrue(result.passed)
        self.assertTrue(result.flagged)
        self.assertIs(result.reason_code, ReasonCode.FLAG_BUNDLE)

    def test_plain_listing_is_not_flagged(self):
        self.assertFalse(bundle_gate(make()).flagged)

    def test_a_bundle_never_causes_rejection(self):
        candidate = make(title="Lenovo LOQ Gaming Laptop combo with free gift")
        self.assertIsNone(first_failure(run_gates(REFERENCE, candidate)))


class TestEveryRejectionCarriesAReasonCode(unittest.TestCase):
    """Constraint 12."""

    def test_all_failing_gates_have_codes(self):
        for candidate in (
            make(title="Laptop Sleeve for Lenovo"),
            make(category=("Home", "Beauty")),
            make(title="Store Display Unit - Lenovo LOQ"),
            make(product_id="243423", url_id="230914"),
        ):
            with self.subTest(title=str(candidate.title)[:40]):
                for gate in run_gates(REFERENCE, candidate):
                    if not gate.passed:
                        self.assertIsNotNone(gate.reason_code)


if __name__ == "__main__":
    unittest.main()
