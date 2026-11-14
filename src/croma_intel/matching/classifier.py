"""The decision table.

Classification is a **lookup over two discrete axes**, not a weighted score:

    (ModelComparison, SpecState) -> (MatchClassification, ReasonCode)

:data:`DECISION_TABLE` is a literal dict. Every reachable combination has an explicit
entry, a test asserts the table is total, and there is no threshold anywhere in this
module. That is rule 8 made structural: a weighted scorer can be nudged until a
borderline candidate flips class, and nobody notices which knob did it.

``score`` exists only to **order candidates already sharing a classification**. It
cannot move a candidate between classes — a test enforces that by construction.

Reading the table
-----------------
*Decisive model* (EXACT / SUFFIX_EQUIVALENT / EMBEDDED) means the model code
identifies the product, per M0 §10.3. Specifications then corroborate or contradict:

    decisive + ALL_MATCH      -> EXACT_MATCH
    decisive + PARTIAL        -> EXACT_MATCH            (model carries identity)
    decisive + INSUFFICIENT   -> EXACT_MATCH_UNVERIFIED (nothing corroborates it)
    decisive + ANY_MISMATCH   -> CONFLICT               (evidence disagrees)

*UNAVAILABLE* is the Flipkart path (F1): the platform never publishes a model code,
so specifications are the only identity evidence and must therefore be **complete**.

    UNAVAILABLE + ALL_MATCH   -> CONFIGURATION_MATCH
    UNAVAILABLE + PARTIAL     -> INSUFFICIENT_EVIDENCE  (rule 3)
    UNAVAILABLE + ANY_MISMATCH-> DIFFERENT

That PARTIAL row is deliberate and consequential. Flipkart's initial payload carries
only summary specs (M0 §3.6), which would land here — so a Flipkart candidate cannot
reach CONFIGURATION_MATCH until M3b-1 captures the full specification table. The
design refuses to match on partial evidence rather than lowering the bar to make the
first platform work.

Stdlib only. Pure. No I/O, no network, no LLM.
"""

from __future__ import annotations

from ..domain.candidate import CandidateProduct
from ..domain.matching import (
    DECISIVE_MODEL_COMPARISONS,
    MatchClassification,
    MatchEvidence,
    MatchVerdict,
    ModelComparison,
    ReasonCode,
    SpecComparison,
    SpecVerdict,
)
from ..domain.product import is_known
from ..normalization.model_code import compare_models, normalize_model
from .gates import first_failure, run_gates
from .profiles import resolve_product_category
from .spec_compare import SpecState, compare_specs, spec_state

#: The decision table. Total over ``ModelComparison × SpecState``.
DECISION_TABLE: dict[
    tuple[ModelComparison, SpecState], tuple[MatchClassification, ReasonCode]
] = {}


def _fill_decisive(comparison: ModelComparison) -> None:
    DECISION_TABLE[(comparison, SpecState.ALL_MATCH)] = (
        MatchClassification.EXACT_MATCH,
        ReasonCode.MODEL_EXACT_SPECS_CORROBORATE,
    )
    DECISION_TABLE[(comparison, SpecState.PARTIAL)] = (
        MatchClassification.EXACT_MATCH,
        ReasonCode.MODEL_EXACT_SPECS_PARTIAL,
    )
    DECISION_TABLE[(comparison, SpecState.INSUFFICIENT)] = (
        MatchClassification.EXACT_MATCH_UNVERIFIED,
        ReasonCode.MODEL_EXACT_SPECS_UNVERIFIED,
    )
    DECISION_TABLE[(comparison, SpecState.ANY_MISMATCH)] = (
        MatchClassification.CONFLICT,
        ReasonCode.MODEL_EXACT_SPEC_CONTRADICTION,
    )


for _decisive in DECISIVE_MODEL_COMPARISONS:
    _fill_decisive(_decisive)

# --- the specification-only path (Flipkart, F1) -----------------------------
DECISION_TABLE[(ModelComparison.UNAVAILABLE, SpecState.ALL_MATCH)] = (
    MatchClassification.CONFIGURATION_MATCH,
    ReasonCode.SPECS_MATCH_MODEL_UNAVAILABLE,
)
DECISION_TABLE[(ModelComparison.UNAVAILABLE, SpecState.PARTIAL)] = (
    MatchClassification.INSUFFICIENT_EVIDENCE,
    ReasonCode.SPECS_PARTIAL_MODEL_UNAVAILABLE,
)
DECISION_TABLE[(ModelComparison.UNAVAILABLE, SpecState.INSUFFICIENT)] = (
    MatchClassification.INSUFFICIENT_EVIDENCE,
    ReasonCode.INSUFFICIENT_CRITICAL_SPECS,
)
DECISION_TABLE[(ModelComparison.UNAVAILABLE, SpecState.ANY_MISMATCH)] = (
    MatchClassification.DIFFERENT,
    ReasonCode.SPECS_MISMATCH_MODEL_UNAVAILABLE,
)

# --- the model was expected and is missing ----------------------------------
DECISION_TABLE[(ModelComparison.UNKNOWN, SpecState.ANY_MISMATCH)] = (
    MatchClassification.DIFFERENT,
    ReasonCode.MODEL_MISSING_SPECS_MISMATCH,
)
for _state in (SpecState.ALL_MATCH, SpecState.PARTIAL, SpecState.INSUFFICIENT):
    DECISION_TABLE[(ModelComparison.UNKNOWN, _state)] = (
        MatchClassification.INSUFFICIENT_EVIDENCE,
        ReasonCode.MODEL_MISSING_INSUFFICIENT,
    )

# --- both models known and different ----------------------------------------
#
# A different model code with **every** CRITICAL specification verified equal is the
# same configuration sold under another SKU - which is what CONFIGURATION_MATCH
# means. Approved 2026-08-27.
#
# This does not reopen the F9 hazard. F9 was about matching on model *similarity*
# without checking specifications: 83DV018NIN and 83DV018QIN are one character apart
# and differ in RAM and GPU. Here the specifications have already been compared and
# agree on all eight, so the difference F9 warned about cannot be present.
#
# The residual risk is real and narrower: two genuine SKUs can share all eight
# CRITICAL specs and differ outside that set (colour, keyboard, warranty tier). The
# verdict therefore still records model_comparison=DIFFERENT, so a consumer can see
# the configuration matched while the model did not.
DECISION_TABLE[(ModelComparison.DIFFERENT, SpecState.ALL_MATCH)] = (
    MatchClassification.CONFIGURATION_MATCH,
    ReasonCode.SPECS_MATCH_MODEL_DIFFERENT,
)
for _state in (SpecState.PARTIAL, SpecState.INSUFFICIENT, SpecState.ANY_MISMATCH):
    DECISION_TABLE[(ModelComparison.DIFFERENT, _state)] = (
        MatchClassification.DIFFERENT,
        ReasonCode.MODEL_DIFFERENT,
    )


# ------------------------------------------------------------------- scoring

#: Ordering weights. These order candidates *within* one classification and are
#: never consulted to choose a classification. Deliberately small integers rather
#: than tuned floats, so nobody mistakes this for a scoring model.
_CLASSIFICATION_RANK: dict[MatchClassification, int] = {
    MatchClassification.EXACT_MATCH: 100,
    MatchClassification.CONFIGURATION_MATCH: 80,
    MatchClassification.EXACT_MATCH_UNVERIFIED: 60,
    MatchClassification.VARIANT: 40,
    MatchClassification.CONFLICT: 20,
    MatchClassification.INSUFFICIENT_EVIDENCE: 10,
    MatchClassification.DIFFERENT: 0,
    MatchClassification.REJECTED: 0,
}


def order_score(classification: MatchClassification, specs: SpecComparison) -> int:
    """Within-class ordering only.

    The classification's own rank dominates by construction: corroborating specs can
    add at most ``profiles.MAX_CRITICAL_SPECS`` (9), and the gaps between adjacent
    ranks are 10 or more. A candidate
    can therefore never out-score its way into another class.
    """
    return _CLASSIFICATION_RANK[classification] + len(specs.matched)


# ------------------------------------------------------------------- classify


def classify(
    reference: CandidateProduct,
    candidate: CandidateProduct,
    *,
    assume_new_when_unmarked: bool = True,
) -> MatchVerdict:
    """Classify one candidate against the reference product.

    Order of operations is fixed and non-negotiable (rule 5):

    1. gates — accessory, category, condition, identity redirect, bundle;
    2. model comparison;
    3. specification comparison;
    4. table lookup;
    5. ordering score, last, and only within the resulting class.

    The CRITICAL set and its minimum-known threshold come from the reference's
    category profile. A reference whose category resolves to no profile cannot be
    judged, and is reported INSUFFICIENT_EVIDENCE rather than compared on another
    category's specifications.

    A gate failure short-circuits to REJECTED before any identity work happens, so a
    laptop bag never reaches the comparison stage at all.
    """
    gates = run_gates(reference, candidate, assume_new_when_unmarked=assume_new_when_unmarked)
    failure = first_failure(gates)
    if failure is not None:
        return MatchVerdict(
            classification=MatchClassification.REJECTED,
            model_comparison=ModelComparison.UNKNOWN,
            specs=SpecComparison(),
            reason_code=failure.reason_code or ReasonCode.GATE_ACCESSORY,
            gates=gates,
            evidence=(
                MatchEvidence(
                    kind=f"GATE:{failure.kind.value}",
                    detail=failure.detail,
                    verdict=SpecVerdict.MISMATCH,
                ),
            ),
            score=0,
            reason=f"rejected at the {failure.kind.value} gate: {failure.detail}",
        )

    if not is_known(reference.model_number) and not reference.specs:
        return MatchVerdict(
            classification=MatchClassification.INSUFFICIENT_EVIDENCE,
            model_comparison=ModelComparison.UNKNOWN,
            specs=SpecComparison(),
            reason_code=ReasonCode.NO_REFERENCE_IDENTITY,
            gates=gates,
            score=0,
            reason="the reference product carries neither a model number nor specifications",
        )

    profile = resolve_product_category(reference)
    if profile is None:
        return MatchVerdict(
            classification=MatchClassification.INSUFFICIENT_EVIDENCE,
            model_comparison=ModelComparison.UNKNOWN,
            specs=SpecComparison(),
            reason_code=ReasonCode.REFERENCE_CATEGORY_UNRESOLVED,
            gates=gates,
            score=0,
            reason=(
                f"reference category {' > '.join(reference.category_path)!r} resolves to "
                "no registered category profile; no CRITICAL specification set applies"
            ),
        )

    model_comparison, model_detail = compare_models(
        str(reference.model_number) if is_known(reference.model_number) else None,
        str(candidate.model_number) if is_known(candidate.model_number) else None,
        candidate_text=candidate.searchable_text,
        availability=candidate.model_availability,
    )
    specs = compare_specs(reference, candidate, critical=profile.critical_specs)
    state = spec_state(specs, minimum_known=profile.min_known_critical_specs)

    classification, reason_code = DECISION_TABLE[(model_comparison, state)]

    # A different model within the same series is a variant, not an unrelated
    # product. This refines DIFFERENT only; it can never produce a positive match.
    if classification is MatchClassification.DIFFERENT and _same_series(reference, candidate):
        classification = MatchClassification.VARIANT
        reason_code = ReasonCode.MODEL_DIFFERENT_SAME_SERIES

    evidence = (
        MatchEvidence(
            kind="MODEL",
            detail=model_detail,
            reference_value=str(reference.model_number),
            candidate_value=str(candidate.model_number),
            verdict=(
                SpecVerdict.MATCH
                if model_comparison in DECISIVE_MODEL_COMPARISONS
                else SpecVerdict.MISMATCH
                if model_comparison is ModelComparison.DIFFERENT
                else SpecVerdict.UNKNOWN
            ),
        ),
        *specs.evidence,
    )

    return MatchVerdict(
        classification=classification,
        model_comparison=model_comparison,
        specs=specs,
        reason_code=reason_code,
        gates=gates,
        evidence=evidence,
        score=order_score(classification, specs),
        reason=(
            f"model={model_comparison.value}, specs={state.value} "
            f"({len(specs.matched)} match, {len(specs.mismatched)} mismatch, "
            f"{len(specs.unknown)} unknown) -> {classification.value}"
        ),
        notes=tuple(g.detail for g in gates if g.flagged),
        extra={"spec_state": state.value, "category": profile.category.value},
    )


def _same_series(reference: CandidateProduct, candidate: CandidateProduct) -> bool:
    if not is_known(reference.model_series) or not is_known(candidate.model_series):
        return False
    return normalize_model(str(reference.model_series)) == normalize_model(
        str(candidate.model_series)
    )


def classify_all(
    reference: CandidateProduct,
    candidates: tuple[CandidateProduct, ...],
    **kwargs: object,
) -> tuple[MatchVerdict, ...]:
    """Classify many candidates. Order of input is preserved.

    Deliberately does **not** sort: ranking is M4. Returning verdicts in input order
    keeps this function free of any ordering opinion.
    """
    return tuple(classify(reference, c, **kwargs) for c in candidates)  # type: ignore[arg-type]


__all__ = [
    "DECISION_TABLE",
    "classify",
    "classify_all",
    "order_score",
]
