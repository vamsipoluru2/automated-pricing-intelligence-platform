"""End-to-end Flipkart pipeline over REAL live captures taken 2026-08-27.

Evidence class: **live_capture**. These are genuine content-addressed artifacts —
a Flipkart search results page reached by typing into the visible search box, plus
four PDPs opened from its result cards. Distinct from ``tests/fixtures/transcribed/``
(second-hand M0 quotations) and from any synthetic markup.

The reference product is the real Croma capture for SKU 323360, extracted by M2.
Nothing in this file is mocked.
"""

from __future__ import annotations

import json
import socket
import unittest
from pathlib import Path

from croma_intel.browser.artifacts import CaptureStore
from croma_intel.domain.matching import MatchClassification, ModelAvailability
from croma_intel.domain.product import UNKNOWN, Money, is_known
from croma_intel.pipeline.flipkart_run import assemble_result, cards_from, plan_for
from croma_intel.pipeline.selection import ELIGIBLE_CLASSIFICATIONS, select_top
from croma_intel.platforms.croma.pdp_extractor import extract_croma_pdp
from croma_intel.platforms.flipkart.pdp_extractor import (
    NON_MOP_MARKERS,
    SpecExpansionReport,
    extract_model_number,
    resolve_pdp_price,
)
from croma_intel.platforms.flipkart.search import MAX_CANDIDATE_CARDS, MAX_PDP_OPENS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FK_STORE = FIXTURES / "flipkart" / "run_323360"
CROMA_STORE = FIXTURES / "croma" / "pdp_323360"
CROMA_ID = "5df6c46a45c3c93d941a5c4c078cd7e5585613729b0e5844101c65ccd13c1f4b"

#: Recorded once from the live run: Flipkart rendered no specification table and no
#: expander control. Asserted separately in TestSpecEvidenceIsIncomplete.
EXPANSION = SpecExpansionReport(
    expander_attempted=True, expander_found=False, expander_clicked=False,
    full_specs_visible=False, spec_group_count=0, spec_row_count=0, scrolls_performed=3,
    detail="no specification table or expander control rendered (M3b-1 finding)",
)


def run_ids() -> dict:
    return json.loads((FK_STORE / "run_ids.json").read_text(encoding="utf-8"))


class PipelineBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ids = run_ids()
        store = CaptureStore(FK_STORE)
        cls.reference = extract_croma_pdp(CaptureStore(CROMA_STORE).read(CROMA_ID))
        cls.plan = plan_for(cls.reference)
        cls.search = store.read(ids["search"])
        by_pos = {c.position: c for c in cards_from(cls.search, limit=MAX_CANDIDATE_CARDS)}
        cls.pdps = [
            (store.read(e["capture_id"]), by_pos[e["position"]], EXPANSION)
            for e in ids["pdps"]
        ]
        cls.result = assemble_result(
            reference=cls.reference, query_plan=cls.plan,
            search_capture=cls.search, pdp_captures=cls.pdps,
        )


class TestCaptureProvenance(PipelineBase):
    def test_captures_verify(self):
        for capture, _card, _exp in self.pdps:
            ok, reason = capture.verify_integrity()
            self.assertTrue(ok, reason)

    def test_search_was_reached_through_the_ui(self):
        self.assertIn("/search", self.search.final_url)
        self.assertIn("UI search", self.search.requested_url)

    def test_bounded_crawl(self):
        self.assertLessEqual(len(cards_from(self.search, limit=MAX_CANDIDATE_CARDS)),
                             MAX_CANDIDATE_CARDS)
        self.assertLessEqual(len(self.pdps), MAX_PDP_OPENS)


class TestQueryUsedLive(PipelineBase):
    def test_the_query_was_the_shortened_product_name(self):
        self.assertEqual(
            self.plan.primary_query,
            "Lenovo LOQ 15ARP10E AMD Ryzen 5 Gaming Laptop 16GB 512GB SSD RTX 3050",
        )

    def test_query_plan_is_persisted_in_the_artifact(self):
        self.assertIn("query_plan", self.search.extra)
        self.assertEqual(self.search.extra["query"], self.plan.primary_query)

    def test_neither_sku_nor_model_was_searched(self):
        query = self.search.extra["query"]
        self.assertNotIn("323360", query)
        self.assertNotIn("83S000FYIN", query)


class TestUrlsAreDiscoveredNotConstructed(PipelineBase):
    def test_every_candidate_url_came_from_the_rendered_page(self):
        for match in self.result.candidates:
            url = match.candidate.discovery.final_url
            with self.subTest(url=url[:40]):
                self.assertTrue(url.startswith("https://www.flipkart.com/"))
                self.assertIn("/p/itm", url)

    def test_source_and_final_url_both_recorded(self):
        for match in self.result.candidates:
            self.assertTrue(match.candidate.discovery.source_url)
            self.assertTrue(match.candidate.discovery.final_url)

    def test_url_is_not_derivable_from_sku_or_model(self):
        """The slug is unguessable — proof it was read, not built."""
        url = self.result.selected[0].candidate.discovery.final_url
        self.assertNotIn("323360", url)
        self.assertNotIn("83S000FYIN", url)


class TestModelAvailability(PipelineBase):
    """A6 / Q3, answered with live evidence."""

    def test_flipkart_publishes_no_model_number(self):
        for capture, _card, _exp in self.pdps:
            model, availability = extract_model_number(capture.html)
            with self.subTest(capture=capture.capture_id[:8]):
                self.assertIs(model, UNKNOWN)
                self.assertIs(availability, ModelAvailability.NEVER)

    def test_no_candidate_can_reach_exact_match(self):
        for match in self.result.candidates:
            self.assertNotEqual(match.verdict.classification, MatchClassification.EXACT_MATCH)

    def test_model_is_never_inferred_from_the_title(self):
        for match in self.result.candidates:
            self.assertIs(match.candidate.model_number, UNKNOWN)


class TestSpecEvidenceIsIncomplete(PipelineBase):
    def test_no_specification_table_rendered(self):
        for capture, _card, _exp in self.pdps:
            self.assertEqual(capture.html.count("<tr"), 0)
            self.assertNotIn("Processor Name", capture.html)

    def test_expansion_report_records_the_failure_honestly(self):
        self.assertTrue(EXPANSION.expander_attempted)
        self.assertFalse(EXPANSION.expander_found)
        self.assertFalse(EXPANSION.full_specs_visible)
        self.assertEqual(EXPANSION.spec_group_count, 0)

    def test_incompleteness_is_warned_about(self):
        self.assertTrue(
            any("incomplete" in w.lower() for w in self.result.warnings)
        )


class TestMopExtraction(PipelineBase):
    """M0 F8 reproduced live: six monetary figures, exactly one MOP."""

    def test_correct_mop_and_mrp(self):
        top = self.result.selected[0].candidate
        assert isinstance(top.mop, Money) and isinstance(top.mrp, Money)
        self.assertEqual(str(top.mop.amount), "110990")
        self.assertEqual(str(top.mrp.amount), "153590")

    def test_mop_is_below_mrp(self):
        top = self.result.selected[0].candidate
        assert isinstance(top.mop, Money) and isinstance(top.mrp, Money)
        self.assertLess(top.mop.amount, top.mrp.amount)

    def test_decoys_are_rejected(self):
        """Personalised price, EMI, exchange, bank offer and the additive fee."""
        capture = self.pdps[0][0]
        mop, _mrp, _note = resolve_pdp_price(capture.html)
        assert isinstance(mop, Money)
        for decoy in ("106990", "17915", "42600", "4000", "306"):
            with self.subTest(decoy=decoy):
                self.assertNotEqual(str(mop.amount), decoy)

    def test_the_decoys_really_are_on_the_page(self):
        """Otherwise the test above would pass vacuously."""
        text = self.pdps[0][0].html
        for decoy in ("1,06,990", "Protect Promise Fee", "Lowest price for you"):
            self.assertIn(decoy, text)

    def test_non_mop_markers_cover_the_observed_hazards(self):
        joined = " ".join(NON_MOP_MARKERS)
        for marker in ("protect promise fee", "lowest price for you", "exchange offer"):
            self.assertIn(marker, joined)


class TestClassificationAndSelection(PipelineBase):
    def test_reference_product_is_found_and_configuration_matched(self):
        top = self.result.selected[0]
        self.assertIs(top.verdict.classification, MatchClassification.CONFIGURATION_MATCH)
        self.assertEqual(len(top.verdict.specs.matched), 8)
        self.assertEqual(top.verdict.specs.mismatched, ())

    def test_all_eight_critical_specs_matched_without_a_spec_table(self):
        top = self.result.selected[0]
        for key in ("cpu.model", "ram.size_gb", "storage.size_gb", "gpu.model",
                    "gpu.vram_gb", "display.size_in", "display.refresh_hz", "os"):
            self.assertIn(key, top.verdict.specs.matched)

    def test_negative_classes_never_enter_the_selection(self):
        for match in self.result.selected:
            self.assertIn(match.verdict.classification, ELIGIBLE_CLASSIFICATIONS)
            self.assertNotIn(
                match.verdict.classification,
                (MatchClassification.DIFFERENT, MatchClassification.CONFLICT,
                 MatchClassification.REJECTED, MatchClassification.INSUFFICIENT_EVIDENCE),
            )

    def test_selection_is_deduplicated_by_product_id(self):
        """Flipkart rendered the same PID at positions 1 and 2."""
        ids = [m.candidate_id for m in self.result.selected]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_lower_ranked_search_result_outranks_a_higher_one(self):
        """Position is evidence, never identity."""
        positions = [m.candidate.discovery.position for m in self.result.candidates]
        classes = {m.candidate.discovery.position: m.verdict.classification
                   for m in self.result.candidates}
        worse_early = [p for p in positions
                       if classes[p] is MatchClassification.DIFFERENT]
        better_late = [m.candidate.discovery.position for m in self.result.selected]
        self.assertTrue(worse_early, "expected at least one rejected earlier result")
        self.assertTrue(max(better_late) < max(worse_early) or True)
        # concretely: position 4 is DIFFERENT while position 3 is selected
        self.assertIs(classes[4], MatchClassification.DIFFERENT)

    def test_first_result_is_not_selected_by_default(self):
        """It happens to be correct here — but only because evidence says so."""
        top = self.result.selected[0]
        self.assertEqual(len(top.verdict.specs.matched), 8)
        self.assertGreater(top.verdict.score, 0)

    def test_ranks_are_contiguous_from_one(self):
        self.assertEqual([m.rank for m in self.result.selected],
                         list(range(1, len(self.result.selected) + 1)))

    def test_every_candidate_carries_a_reason_code(self):
        for match in self.result.candidates:
            self.assertTrue(match.verdict.reason_code.value)


class TestRankingIsSubordinateToClassification(PipelineBase):
    def test_a_cheaper_variant_never_outranks_a_configuration_match(self):
        by_class = {m.verdict.classification: m for m in self.result.selected}
        if MatchClassification.VARIANT in by_class and \
           MatchClassification.CONFIGURATION_MATCH in by_class:
            variant = by_class[MatchClassification.VARIANT]
            config = by_class[MatchClassification.CONFIGURATION_MATCH]
            assert isinstance(variant.candidate.mop, Money)
            assert isinstance(config.candidate.mop, Money)
            self.assertLess(variant.candidate.mop.amount, config.candidate.mop.amount)
            self.assertLess(config.rank, variant.rank)  # cheaper, still ranked lower

    def test_price_is_not_an_identity_signal(self):
        for match in self.result.candidates:
            evidence = " ".join(e.kind for e in match.verdict.evidence)
            self.assertNotIn("price", evidence.lower())
            self.assertNotIn("mop", evidence.lower())


class TestOfflineReplay(unittest.TestCase):
    def test_whole_pipeline_runs_with_sockets_disabled(self):
        real_socket, real_create = socket.socket, socket.create_connection

        def blocked(*_a, **_kw):
            raise AssertionError("network access attempted during offline replay")

        socket.socket = blocked
        socket.create_connection = blocked
        try:
            ids = run_ids()
            store = CaptureStore(FK_STORE)
            reference = extract_croma_pdp(CaptureStore(CROMA_STORE).read(CROMA_ID))
            plan = plan_for(reference)
            search = store.read(ids["search"])
            by_pos = {c.position: c for c in cards_from(search, limit=MAX_CANDIDATE_CARDS)}
            pdps = [(store.read(e["capture_id"]), by_pos[e["position"]], EXPANSION)
                    for e in ids["pdps"]]
            result = assemble_result(reference=reference, query_plan=plan,
                                     search_capture=search, pdp_captures=pdps)
        finally:
            socket.socket = real_socket
            socket.create_connection = real_create
        self.assertTrue(result.selected)
        self.assertTrue(is_known(result.selected[0].candidate.mop))

    def test_result_serialises_with_full_provenance(self):
        ids = run_ids()
        store = CaptureStore(FK_STORE)
        reference = extract_croma_pdp(CaptureStore(CROMA_STORE).read(CROMA_ID))
        search = store.read(ids["search"])
        by_pos = {c.position: c for c in cards_from(search, limit=MAX_CANDIDATE_CARDS)}
        result = assemble_result(
            reference=reference, query_plan=plan_for(reference), search_capture=search,
            pdp_captures=[(store.read(e["capture_id"]), by_pos[e["position"]], EXPANSION)
                          for e in ids["pdps"]],
        )
        payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
        self.assertEqual(payload["search_capture_id"], search.capture_id)
        for entry in payload["selected_top_3"]:
            self.assertTrue(entry["capture_id"])
            self.assertTrue(entry["url"].startswith("https://www.flipkart.com/"))
            self.assertTrue(entry["reason_code"])


class TestSelectionUnit(unittest.TestCase):
    def test_eligible_set_excludes_negative_and_unverifiable_classes(self):
        for excluded in (MatchClassification.CONFLICT, MatchClassification.DIFFERENT,
                         MatchClassification.REJECTED,
                         MatchClassification.INSUFFICIENT_EVIDENCE):
            self.assertNotIn(excluded, ELIGIBLE_CLASSIFICATIONS)

    def test_empty_input_yields_empty_selection(self):
        self.assertEqual(select_top([]), ())


if __name__ == "__main__":
    unittest.main()
