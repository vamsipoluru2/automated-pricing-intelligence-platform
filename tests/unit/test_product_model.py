"""Domain-model semantics: UNKNOWN, Money, and the shape of the spec table.

Rule 2 says UNKNOWN is a first-class value. These tests hold that to its literal
meaning — the coercions that would quietly destroy an unknown must fail.
"""

from __future__ import annotations

import copy
import pickle
import unittest
from decimal import Decimal

from croma_intel.domain.product import (
    UNKNOWN,
    Money,
    PriceQuote,
    PriceStatus,
    Sourced,
    SourceKind,
    SpecFeature,
    SpecGroup,
    SpecificationTable,
    Unknown,
    is_known,
)


class TestUnknownSemantics(unittest.TestCase):
    def test_is_a_singleton(self):
        self.assertIs(Unknown(), UNKNOWN)
        self.assertIs(copy.copy(UNKNOWN), UNKNOWN)
        self.assertIs(copy.deepcopy(UNKNOWN), UNKNOWN)
        self.assertIs(pickle.loads(pickle.dumps(UNKNOWN)), UNKNOWN)

    def test_is_not_none(self):
        self.assertIsNot(UNKNOWN, None)
        self.assertNotEqual(UNKNOWN, None)

    def test_truth_testing_raises(self):
        """`if value:` and `value or default` are the two silent killers."""
        with self.assertRaises(TypeError):
            bool(UNKNOWN)
        with self.assertRaises(TypeError):
            if UNKNOWN:
                pass

    def test_or_default_raises_rather_than_defaulting(self):
        with self.assertRaises(TypeError):
            _ = UNKNOWN or "fallback"

    def test_equality_is_identity(self):
        self.assertEqual(UNKNOWN, UNKNOWN)
        self.assertNotEqual(UNKNOWN, "UNKNOWN")
        self.assertNotEqual(UNKNOWN, 0)
        self.assertNotEqual(UNKNOWN, "")

    def test_is_hashable(self):
        self.assertEqual(len({UNKNOWN, UNKNOWN}), 1)

    def test_is_known_helper(self):
        self.assertFalse(is_known(UNKNOWN))
        self.assertTrue(is_known(None))  # None is a real value; absence is UNKNOWN
        self.assertTrue(is_known(""))

    def test_repr_is_legible(self):
        self.assertEqual(repr(UNKNOWN), "UNKNOWN")


class TestSourced(unittest.TestCase):
    def test_known_value_carries_its_path(self):
        field = Sourced("83S000FYIN", "a.b.c")
        self.assertTrue(field.known)
        self.assertEqual(field.path, "a.b.c")
        self.assertEqual(field.kind, SourceKind.INITIAL_DATA)

    def test_missing_still_records_where_it_looked(self):
        field = Sourced.missing("a.b.c", note="not present")
        self.assertFalse(field.known)
        self.assertIs(field.value, UNKNOWN)
        self.assertEqual(field.path, "a.b.c")
        self.assertEqual(field.kind, SourceKind.ABSENT)

    def test_serialises_unknown_as_a_string(self):
        self.assertEqual(Sourced.missing("p").to_dict()["value"], "UNKNOWN")


class TestMoney(unittest.TestCase):
    def test_parses_the_reducer_format_exactly(self):
        money = Money.parse("113990.00")
        self.assertIsInstance(money, Money)
        assert isinstance(money, Money)
        self.assertEqual(money.amount, Decimal("113990.00"))
        self.assertEqual(money.currency, "INR")
        self.assertEqual(money.raw, "113990.00")

    def test_uses_decimal_not_float(self):
        money = Money.parse("0.10")
        assert isinstance(money, Money)
        self.assertIsInstance(money.amount, Decimal)
        self.assertNotEqual(float(money.amount) * 3, 0.3)  # float would drift
        self.assertEqual(money.amount * 3, Decimal("0.30"))

    def test_rejects_rendered_display_text(self):
        """Grouped/symbol-bearing text comes from the DOM, not a sanctioned path."""
        for raw in ("₹1,13,990.00", "1,13,990", "Rs. 113990", "113990/-", ""):
            with self.subTest(raw=raw):
                self.assertIs(Money.parse(raw), UNKNOWN)

    def test_rejects_negative(self):
        self.assertIs(Money.parse("-1"), UNKNOWN)

    def test_unknown_in_unknown_out(self):
        self.assertIs(Money.parse(UNKNOWN), UNKNOWN)
        self.assertIs(Money.parse(None), UNKNOWN)


class TestPriceQuote(unittest.TestCase):
    def test_resolved_quote(self):
        quote = PriceQuote(
            mop=Sourced(Money.parse("100"), "p.mop"),
            mrp=Sourced(Money.parse("200"), "p.mrp"),
            status=PriceStatus.RESOLVED,
        )
        self.assertTrue(quote.resolved)

    def test_has_no_field_for_a_conditional_price(self):
        """Rule 6 / M0 §11.2, enforced structurally rather than by convention."""
        fields = set(PriceQuote.__dataclass_fields__)
        for forbidden in (
            "exchange", "exchange_value", "bank_offer", "coupon", "emi",
            "instalment", "additive_fee", "fees", "personalised_price", "lowest_price",
        ):
            self.assertNotIn(forbidden, fields)


class TestSpecificationTable(unittest.TestCase):
    def setUp(self):
        self.table = SpecificationTable(
            groups=(
                SpecGroup(
                    name="Internal Memory",
                    features=(
                        SpecFeature(name="RAM", values=("16GB",)),
                        SpecFeature(name="Type of RAM", values=("DDR5",)),
                        SpecFeature(
                            name="RAM Frequency", values=("4800",), unit="MHz",
                            unit_type="UoM-MHz",
                        ),
                    ),
                ),
                SpecGroup(
                    name="Laptop Category",
                    features=(SpecFeature(name="Suitable For", values=("Work", "Gaming")),),
                ),
            )
        )

    def test_structure_is_preserved_not_flattened(self):
        self.assertEqual(self.table.group_count, 2)
        self.assertEqual(self.table.feature_count, 4)
        self.assertEqual(self.table.groups[0].name, "Internal Memory")

    def test_lookup_is_case_insensitive(self):
        feature = self.table.find("type of ram")
        assert isinstance(feature, SpecFeature)
        self.assertEqual(feature.value, "DDR5")

    def test_absent_feature_is_unknown(self):
        self.assertIs(self.table.find("Nonexistent"), UNKNOWN)
        self.assertIs(self.table.value_of("Nonexistent"), UNKNOWN)

    def test_multi_valued_feature_refuses_to_pick_one(self):
        feature = self.table.find("Suitable For")
        assert isinstance(feature, SpecFeature)
        self.assertEqual(feature.values, ("Work", "Gaming"))
        self.assertIs(feature.value, UNKNOWN)

    def test_unit_is_kept_with_the_value(self):
        feature = self.table.find("RAM Frequency")
        assert isinstance(feature, SpecFeature)
        self.assertEqual(feature.unit, "MHz")
        self.assertEqual(feature.value_with_unit, "4800 MHz")

    def test_value_with_unit_is_unknown_when_the_value_is(self):
        self.assertIs(SpecFeature(name="x", values=()).value_with_unit, UNKNOWN)


if __name__ == "__main__":
    unittest.main()
