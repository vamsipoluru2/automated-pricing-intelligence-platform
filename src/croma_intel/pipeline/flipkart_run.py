"""Flipkart run orchestration.

Split along the architectural seam (rule 1): :func:`capture_flipkart` is the only
part that touches a browser, and :func:`assemble_result` is a pure function over
stored artifacts. Everything interesting — extraction, classification, ranking — is
in the pure half, so a run can be re-derived from its captures forever.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.candidate import candidate_from_croma
from ..domain.capture import RawCapture
from ..domain.matching import MatchClassification
from ..domain.product import UNKNOWN, ExtractedProduct, is_known
from ..domain.result import (
    CompetitorMatchResult,
    MatchedCandidate,
    ReferenceSummary,
    summarise,
)
from ..matching.classifier import classify
from ..platforms.flipkart.pdp_extractor import (
    SpecExpansionReport,
    extract_flipkart_pdp,
)
from ..platforms.flipkart.query_builder import FlipkartQueryPlan, build_query_plan
from ..platforms.flipkart.search import FlipkartCard, harvest_cards, result_total
from .selection import select_top, selection_warnings

PLATFORM = "flipkart"


def plan_for(reference: ExtractedProduct) -> FlipkartQueryPlan:
    """Build the Flipkart query plan from an M2 Croma extraction."""
    neutral = candidate_from_croma(reference)
    title = str(reference.identity.name.value) if reference.identity.name.known else ""
    # Croma's labelled Model Number is a manufacturer part number (BV2V9PA,
    # 90NB10N2-M021T0) that no retailer publishes. The *series* is the searchable
    # identity (15-fc0499AU, X1605VA-MB1628WS), so it is prepended to the title to
    # guarantee the query keeps a discriminator even when the title parser drops it.
    if reference.identity.model_series.known:
        series = str(reference.identity.model_series.value)
        if series.lower() not in title.lower():
            title = f"{series} {title}"
    return build_query_plan(
        title,
        sku=str(reference.identity.sku.value) if reference.identity.sku.known else "",
        model=(
            reference.identity.model_number.value
            if reference.identity.model_number.known
            else UNKNOWN
        ),
        specs=neutral.specs,
    )


def assemble_result(
    *,
    reference: ExtractedProduct,
    query_plan: FlipkartQueryPlan,
    search_capture: RawCapture,
    pdp_captures: Sequence[tuple[RawCapture, FlipkartCard, SpecExpansionReport]],
) -> CompetitorMatchResult:
    """Extract, classify, rank and select — entirely offline.

    Order is fixed: every candidate is classified (which runs the gates first) before
    anything is ranked. Selection sees only verdicts, never raw search positions.
    """
    reference_candidate = candidate_from_croma(reference)
    shown, total = result_total(search_capture.html)

    assessed: list[MatchedCandidate] = []
    warnings: list[str] = []
    incomplete = 0

    for capture, card, expansion in pdp_captures:
        candidate = extract_flipkart_pdp(
            capture,
            position=card.position,
            query=query_plan.primary_query,
            query_strategy=query_plan.strategy,
            card_bullets=card.summary_specs,
            expansion=expansion,
        )
        # The card's reconciled price is preferred when the PDP could not resolve one:
        # both are rendered-DOM evidence, and the card triple is independently checked.
        if not is_known(candidate.mop) and is_known(card.mop):
            candidate = _with_card_price(candidate, card)
        if not expansion.full_specs_visible:
            incomplete += 1
        assessed.append(
            MatchedCandidate(candidate=candidate, verdict=classify(reference_candidate, candidate))
        )

    selected = select_top(assessed)
    warnings.extend(selection_warnings(assessed, selected))

    if incomplete:
        warnings.append(
            f"{incomplete} candidate(s) had no structured specification table; "
            "specification evidence is incomplete (Flipkart publishes none — M3b-1 finding)"
        )
    if any(m.candidate.model_availability.value == "NEVER" for m in assessed):
        warnings.append(
            "Flipkart published no model number for any candidate; identity rests on "
            "specifications alone (F1 upheld, A6 resolved)"
        )
    missing_price = [m for m in selected if not is_known(m.candidate.mop)]
    if missing_price:
        warnings.append(f"{len(missing_price)} selected candidate(s) have MOP UNKNOWN")

    return CompetitorMatchResult(
        reference=ReferenceSummary(
            sku=str(reference.identity.sku.value),
            title=str(reference.identity.name.value),
            model=(
                reference.identity.model_number.value
                if reference.identity.model_number.known
                else UNKNOWN
            ),
            capture_id=reference.provenance.capture_id,
        ),
        platform=PLATFORM,
        query_plan=query_plan.to_dict(),
        candidates=tuple(assessed),
        selected=selected,
        search_capture_id=search_capture.capture_id,
        warnings=tuple(warnings),
        stats={
            "cards_shown": "UNKNOWN" if shown is UNKNOWN else shown,
            "results_reported_by_flipkart": "UNKNOWN" if total is UNKNOWN else total,
            "pdps_opened": len(pdp_captures),
            "classifications": summarise(assessed),
            "rejected": sum(
                1 for m in assessed
                if m.verdict.classification is MatchClassification.REJECTED
            ),
        },
    )


def _with_card_price(candidate, card: FlipkartCard):
    from dataclasses import replace

    return replace(
        candidate,
        mop=card.mop,
        mrp=card.mrp,
        notes=(*candidate.notes, f"price taken from search card: {card.price_note}"),
    )


def cards_from(search_capture: RawCapture, *, limit: int) -> tuple[FlipkartCard, ...]:
    """Harvest result cards from a stored search capture. Pure."""
    return harvest_cards(search_capture.html, limit=limit)


__all__ = ["PLATFORM", "assemble_result", "cards_from", "plan_for"]
