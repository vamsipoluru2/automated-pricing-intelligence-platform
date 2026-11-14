"""Model-code normalization and comparison.

The never-strip invariant (rule 4) is the single most consequential rule in the
matching layer, and every case below is drawn from a real model code recorded in
M0 §10.2 — fifteen genuine Lenovo MTMs plus one HP part number.
"""

from __future__ import annotations

import unittest

from croma_intel.domain.matching import ModelAvailability, ModelComparison
from croma_intel.normalization.model_code import (
    compare_models,
    contains_model_token,
    normalize_model,
    split_delimited_suffix,
    strip_droppable_suffix,
)

# Real MTMs from M0 §10.2.
REFERENCE = "83S000FYIN"       # LOQ 15ARP10E, India — the project's reference product
US_SIBLING = "83S00000US"      # LOQ-E, United States — different product (F9 proof 1)
SAME_PREFIX = "83S000CPIN"     # LOQ Essential — shares six leading chars (F9 proof 2)
EDIT_DISTANCE_1 = "83DV018QIN"  # 24 GB / RTX 4050
EDIT_DISTANCE_1_SIBLING = "83DV018NIN"  # 16 GB / RTX 3050 (F9 proof 3)
HP_PART = "2Z326PA#ACJ"        # the only real delimiter-suffix evidence collected


class TestSafeNormalization(unittest.TestCase):
    def test_case_folding_is_safe(self):
        """Vijay Sales URL slugs are lowercase; titles are uppercase. Same product."""
        self.assertEqual(normalize_model("83jc0045in"), "83JC0045IN")

    def test_whitespace_collapse(self):
        self.assertEqual(normalize_model("  83S000FYIN  "), "83S000FYIN")
        self.assertEqual(normalize_model("LOQ   15ARP10E"), "LOQ 15ARP10E")

    def test_unicode_nfkc(self):
        self.assertEqual(normalize_model("８３Ｓ０００ＦＹＩＮ"), REFERENCE)

    def test_nothing_is_removed(self):
        for code in (REFERENCE, US_SIBLING, EDIT_DISTANCE_1, HP_PART):
            with self.subTest(code=code):
                self.assertEqual(len(normalize_model(code)), len(code))

    def test_empty_and_none(self):
        self.assertEqual(normalize_model(None), "")
        self.assertEqual(normalize_model(""), "")


class TestNeverStripInsideARun(unittest.TestCase):
    """Rule 4. Each test corresponds to a proof in M0 §10.3."""

    def test_trailing_in_is_never_split_off(self):
        """There is no delimiter, so there is nothing to split."""
        self.assertEqual(split_delimited_suffix(REFERENCE), (REFERENCE, ""))
        self.assertEqual(strip_droppable_suffix(REFERENCE), REFERENCE)

    def test_region_codes_keep_products_distinct(self):
        """F9 proof 1: stripping IN/US would merge India and US products."""
        self.assertNotEqual(
            strip_droppable_suffix(REFERENCE), strip_droppable_suffix(US_SIBLING)
        )
        comparison, _ = compare_models(REFERENCE, US_SIBLING)
        self.assertIs(comparison, ModelComparison.DIFFERENT)

    def test_shared_prefix_does_not_make_a_match(self):
        """F9 proof 2: six identical leading characters, different configuration."""
        self.assertEqual(REFERENCE[:6], SAME_PREFIX[:6])
        comparison, _ = compare_models(REFERENCE, SAME_PREFIX)
        self.assertIs(comparison, ModelComparison.DIFFERENT)

    def test_edit_distance_one_is_different(self):
        """F9 proof 3: N vs Q — different RAM and different GPU."""
        differences = sum(
            1 for a, b in zip(EDIT_DISTANCE_1, EDIT_DISTANCE_1_SIBLING) if a != b
        )
        self.assertEqual(differences, 1)
        comparison, detail = compare_models(EDIT_DISTANCE_1, EDIT_DISTANCE_1_SIBLING)
        self.assertIs(comparison, ModelComparison.DIFFERENT)
        self.assertIn("no fuzzy fallback", detail)

    def test_no_similarity_machinery_is_imported_or_called(self):
        """Guards against a future 'just add approximate matching' change.

        Checked at the AST level, on imports and call names only. A substring scan
        would fire on the module docstring, which *declares* the ban — the same trap
        the EUP guard fell into and was rewritten to avoid. Prose stating a boundary
        must never be punished as if it were an implementation.
        """
        import ast
        import pathlib

        import croma_intel.normalization.model_code as module

        banned = {"difflib", "sequencematcher", "levenshtein", "rapidfuzz", "fuzzywuzzy",
                  "get_close_matches", "ratio", "jaro", "damerau"}
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [a.name for a in node.names if a.name.split(".")[0].lower() in banned]
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0].lower() in banned:
                    offenders.append(node.module or "")
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name and name.lower() in banned:
                    offenders.append(name)
        self.assertEqual(offenders, [], f"approximate matching is banned (rule 4 / F9): {offenders}")


class TestDelimitedSuffix(unittest.TestCase):
    def test_hp_hash_delimiter_splits(self):
        """The only real delimiter-suffix evidence collected (M0 §15.2)."""
        self.assertEqual(split_delimited_suffix(HP_PART), ("2Z326PA", "ACJ"))
        self.assertEqual(strip_droppable_suffix(HP_PART), "2Z326PA")

    def test_hyphenated_market_suffix_is_equivalent(self):
        comparison, _ = compare_models(REFERENCE, f"{REFERENCE}-IND")
        self.assertIs(comparison, ModelComparison.SUFFIX_EQUIVALENT)

    def test_unrecognised_suffix_is_kept_not_assumed_decorative(self):
        """An unknown token might be what distinguishes two configurations."""
        self.assertEqual(strip_droppable_suffix("83DV018N-XYZ"), "83DV018N-XYZ")
        comparison, _ = compare_models("83DV018N", "83DV018N-XYZ")
        self.assertIsNot(comparison, ModelComparison.SUFFIX_EQUIVALENT)

    def test_suffix_only_input_is_not_split(self):
        self.assertEqual(split_delimited_suffix("-IN"), ("-IN", ""))


class TestBoundarySafeTokenScan(unittest.TestCase):
    def test_finds_model_mid_title_reliance_digital_shape(self):
        title = "Lenovo LOQ 15IRX9 83DV00L9IN Gaming Laptop (Intel Core i5-13450HX/24 GB)"
        self.assertTrue(contains_model_token(title, "83DV00L9IN"))

    def test_finds_model_end_of_title_vijay_sales_shape(self):
        title = "Lenovo LOQ 15 Gaming Laptop (13th Gen/ 24GB RAM/ MS-Office) 83DV018QIN"
        self.assertTrue(contains_model_token(title, "83DV018QIN"))

    def test_matches_when_bounded_by_a_delimiter(self):
        self.assertTrue(contains_model_token("code: 83S000FYIN-IND", REFERENCE))

    def test_does_not_match_inside_a_longer_run(self):
        """83S000FYINX is a different code and must not match."""
        self.assertFalse(contains_model_token("83S000FYINX", REFERENCE))

    def test_does_not_match_a_longer_code_ending_in_the_needle(self):
        self.assertFalse(contains_model_token("X83S000FYIN", REFERENCE))

    def test_case_insensitive(self):
        self.assertTrue(contains_model_token("loq 83jc0045in gaming", "83JC0045IN"))

    def test_empty_inputs(self):
        self.assertFalse(contains_model_token("", REFERENCE))
        self.assertFalse(contains_model_token("some text", ""))
        self.assertFalse(contains_model_token(None, REFERENCE))


class TestAgreesWithTheM1Implementation(unittest.TestCase):
    """The M1 identifier has its own copy of this rule; they must never diverge.

    M1 is deliberately left untouched, so the safeguard is a cross-check rather than
    a refactor.
    """

    CORPUS = (
        ("Lenovo LOQ 15IRX9 83DV00L9IN Gaming Laptop", "83DV00L9IN"),
        ("... MS-Office) 83DV018QIN", "83DV018QIN"),
        ("83S000FYINX", REFERENCE),
        ("X83S000FYIN", REFERENCE),
        ("83S000FYIN-IND", REFERENCE),
        ("loq 83jc0045in gaming", "83JC0045IN"),
        ("no model here", REFERENCE),
        ("", REFERENCE),
        ("Compare Lenovo LOQ 15ARP10E ... 83S000FYIN", REFERENCE),
    )

    def test_both_implementations_agree(self):
        from croma_intel.platforms.croma.result_identifier import (
            contains_model_token as m1_contains,
        )

        for text, model in self.CORPUS:
            with self.subTest(text=text[:40], model=model):
                self.assertEqual(
                    contains_model_token(text, model),
                    m1_contains(text, model),
                    "M3 and M1 boundary-safe matching disagree",
                )


class TestModelAvailabilitySemantics(unittest.TestCase):
    """Absent-because-the-platform-never-publishes-one is not absent-because-missing."""

    def test_flipkart_style_absence_is_unavailable(self):
        comparison, detail = compare_models(
            REFERENCE, None, availability=ModelAvailability.NEVER
        )
        self.assertIs(comparison, ModelComparison.UNAVAILABLE)
        self.assertIn("specification-only", detail)

    def test_unexpected_absence_is_unknown(self):
        comparison, _ = compare_models(
            REFERENCE, None, availability=ModelAvailability.ALWAYS
        )
        self.assertIs(comparison, ModelComparison.UNKNOWN)

    def test_absent_reference_is_unknown(self):
        comparison, _ = compare_models(None, REFERENCE)
        self.assertIs(comparison, ModelComparison.UNKNOWN)

    def test_embedded_beats_unavailable_when_the_text_carries_the_code(self):
        """Even on a NEVER platform, a model in the title is real evidence."""
        comparison, _ = compare_models(
            REFERENCE,
            None,
            candidate_text=f"Lenovo LOQ {REFERENCE} Gaming Laptop",
            availability=ModelAvailability.NEVER,
        )
        self.assertIs(comparison, ModelComparison.EMBEDDED)


class TestCrossPlatformPair(unittest.TestCase):
    """M0 §15.1 case A: the one confirmed cross-platform exact-model pair."""

    def test_reliance_digital_and_vijay_sales_agree_on_83jc0045in(self):
        rd_title = "Buy Lenovo LOQ 15ARP9 83JC0045IN Gaming Laptop at Reliance Digital"
        vs_title = "Lenovo LOQ 15ARP9 Gaming Laptop (AMD Ryzen 7) 83JC0045IN"
        self.assertTrue(contains_model_token(rd_title, "83JC0045IN"))
        self.assertTrue(contains_model_token(vs_title, "83JC0045IN"))
        comparison, _ = compare_models("83JC0045IN", "83JC0045IN")
        self.assertIs(comparison, ModelComparison.EXACT)


if __name__ == "__main__":
    unittest.main()
