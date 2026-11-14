"""Hard gates — evaluated before any identity or ranking work (rule 5).

A gate answers "is this listing eligible to be compared at all?". None of them look
at how *similar* a candidate is; that question is not asked until every gate has
passed. Ordering is fixed and documented in :data:`GATE_ORDER` so that the rejection
reason for a given candidate is deterministic rather than dependent on evaluation
order.

Why each gate exists, from live evidence:

* **Accessory** — M0 §12.2. A search that surfaces a laptop also surfaces bags and
  chargers, and those carry their own model codes *and* the laptop's size tokens. A
  ₹548 sleeve scoring as spec-adjacent to a ₹1,08,990 laptop is the worst kind of
  silent-wrong: real URL, real price, wrong product.
* **Category** — M0 §12.1 / F11. Croma's numeric category IDs cleanly separate
  products from accessories and are the strongest structural signal available.
* **Condition** — F7. ``"Store Display Unit - "`` is a *title prefix* on Vijay Sales,
  not a badge. A floor unit at 30% off is plausible enough not to trip a price
  anomaly and matches on model and specs perfectly.
* **Bundle** — M0 §6.5. A free soundbar does not change identity, so this gate
  **flags and passes**. Rejecting would discard a legitimate competitor price.
* **Identity redirect** — F10. A Vijay Sales URL for ``230914`` served ``243423``.

Stdlib only. Pure. No I/O, no network.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..domain.candidate import CandidateProduct
from ..domain.matching import (
    COMPARABLE_CONDITIONS,
    GateKind,
    GateResult,
    ListingCondition,
    ReasonCode,
)
from ..domain.product import UNKNOWN, is_known
from ..platforms import accessory_filter
from .profiles import (
    CategoryProfile,
    category_crumbs,
    mentions_category,
    resolve_product_category,
)

#: Fixed evaluation order. Accessory first because it is the cheapest decisive
#: rejection and the one M0 proved most dangerous to miss.
GATE_ORDER: tuple[GateKind, ...] = (
    GateKind.ACCESSORY,
    GateKind.CATEGORY,
    GateKind.CONDITION,
    GateKind.IDENTITY_REDIRECT,
    GateKind.BUNDLE,
)

# ------------------------------------------------------------------- condition

#: Condition markers, matched against the *title*. F7 established these appear as a
#: prefix rather than as structured data, so the title is the only place to look.
_CONDITION_PATTERNS: tuple[tuple[ListingCondition, str], ...] = (
    (ListingCondition.STORE_DISPLAY_UNIT, r"\bstore\s+display\s+unit\b"),
    (ListingCondition.STORE_DISPLAY_UNIT, r"\bdisplay\s+unit\b"),
    (ListingCondition.STORE_DISPLAY_UNIT, r"\bdemo\s+(?:unit|piece)\b"),
    (ListingCondition.OPEN_BOX, r"\bopen[\s-]?box\b"),
    (ListingCondition.REFURBISHED, r"\brefurbished\b"),
    (ListingCondition.REFURBISHED, r"\brefurb\b"),
    (ListingCondition.RENEWED, r"\brenewed\b"),
    (ListingCondition.USED, r"\bpre[\s-]?owned\b"),
    (ListingCondition.USED, r"\bsecond[\s-]?hand\b"),
    (ListingCondition.USED, r"\bused\b"),
)


def detect_condition(title: str | None) -> ListingCondition:
    """Infer listing condition from a title. UNKNOWN when no marker is present.

    Returns UNKNOWN rather than assuming NEW: an unmarked title is *usually* new
    stock, but "usually" is exactly the assumption F7 punished. The condition gate
    decides what to do with UNKNOWN; this function only reports what it saw.
    """
    if not title:
        return ListingCondition.UNKNOWN
    text = re.sub(r"\s+", " ", str(title)).strip().lower()
    for condition, pattern in _CONDITION_PATTERNS:
        if re.search(pattern, text):
            return condition
    return ListingCondition.UNKNOWN


def condition_gate(
    candidate: CandidateProduct, *, assume_new_when_unmarked: bool = True
) -> GateResult:
    """Only new retail stock is comparable.

    ``assume_new_when_unmarked`` reflects an operational reality: no platform
    observed in M0 labels new stock as "New". Requiring an explicit marker would
    reject every legitimate listing. The assumption is therefore *stated and
    defaulted*, and the detected marker always wins over it.
    """
    title = str(candidate.title) if is_known(candidate.title) else ""
    detected = detect_condition(title)
    if detected is ListingCondition.UNKNOWN and candidate.condition is not ListingCondition.UNKNOWN:
        detected = candidate.condition

    if detected in COMPARABLE_CONDITIONS:
        return GateResult(GateKind.CONDITION, True, detail=f"condition {detected.value}")

    if detected is ListingCondition.UNKNOWN:
        if assume_new_when_unmarked:
            return GateResult(
                GateKind.CONDITION,
                True,
                detail="no condition marker present; treated as NEW (stated assumption)",
            )
        return GateResult(
            GateKind.CONDITION,
            False,
            reason_code=ReasonCode.GATE_CONDITION_UNKNOWN,
            detail="condition could not be established",
        )

    return GateResult(
        GateKind.CONDITION,
        False,
        reason_code=ReasonCode.GATE_CONDITION_NOT_NEW,
        detail=f"listing condition is {detected.value}, not new retail stock",
    )


# ------------------------------------------------------------------- accessory


def accessory_gate(
    candidate: CandidateProduct, profile: CategoryProfile | None = None
) -> GateResult:
    """Reject accessories before anything else looks at the listing.

    *profile* is the reference's category; its anchors decide what "the product
    itself" looks like. Without one, the filter's validated laptop anchors apply.
    """
    title = str(candidate.title) if is_known(candidate.title) else ""
    category = " > ".join(candidate.category_path)
    if profile is None:
        verdict = accessory_filter.classify(title, category=category)
    else:
        verdict = accessory_filter.classify(
            title, category=category, anchors=profile.product_anchors
        )
    if verdict.is_accessory:
        return GateResult(
            GateKind.ACCESSORY,
            False,
            reason_code=ReasonCode.GATE_ACCESSORY,
            detail=f"{verdict.detail} ({', '.join(verdict.reasons)})",
        )
    return GateResult(GateKind.ACCESSORY, True, detail=verdict.detail)


# -------------------------------------------------------------------- category

# Category vocabulary lives in the profiles (matching/profiles.py). Both a
# product-type leaf (Vijay Sales: ``Gaming Laptops``) and a brand leaf (Flipkart:
# ``Lenovo Laptops``) must resolve — M0 §6.7 and §3.3 recorded the two shapes.


def category_gate(
    reference: CandidateProduct, candidate: CandidateProduct
) -> GateResult:
    """Both sides must sit in a compatible product category.

    Passes when the candidate's category evidence is absent: rule 3 forbids missing
    information producing a *match*, but a gate that rejected on absence would
    discard every platform that does not publish a breadcrumb. Absence is passed
    through to the classifier, which cannot reach a positive classification on
    missing evidence anyway.

    Rejects when the candidate's most specific breadcrumb names a *different*
    registered category, or when no breadcrumb names the reference's category.
    """
    candidate_text = " ".join(candidate.category_path).lower()
    if not candidate_text:
        return GateResult(
            GateKind.CATEGORY, True, detail="candidate publishes no category; deferred to specs"
        )

    reference_profile = resolve_product_category(reference)
    if reference_profile is None:
        return GateResult(
            GateKind.CATEGORY,
            True,
            detail="reference category unresolved; deferred to the classifier",
        )

    expected = reference_profile.category.value
    candidate_profile = resolve_product_category(candidate)
    if candidate_profile is not None and candidate_profile is not reference_profile:
        return GateResult(
            GateKind.CATEGORY,
            False,
            reason_code=ReasonCode.GATE_CATEGORY_MISMATCH,
            detail=(
                f"reference is {expected}; candidate category {candidate_text!r} "
                f"is {candidate_profile.category.value}"
            ),
        )
    if not mentions_category(category_crumbs(candidate), reference_profile):
        return GateResult(
            GateKind.CATEGORY,
            False,
            reason_code=ReasonCode.GATE_CATEGORY_MISMATCH,
            detail=f"reference is {expected}; candidate category {candidate_text!r} is not",
        )
    return GateResult(GateKind.CATEGORY, True, detail=f"category {candidate_text!r} compatible")


# ----------------------------------------------------------- identity redirect


def identity_redirect_gate(candidate: CandidateProduct) -> GateResult:
    """F10: the id requested and the id served must agree.

    Rejects only on a *demonstrated* disagreement — both ids known and different.
    An unknown id is not evidence of a redirect.
    """
    url_id = candidate.url_derived_id
    product_id = candidate.platform_product_id
    if url_id is UNKNOWN or product_id is UNKNOWN:
        return GateResult(
            GateKind.IDENTITY_REDIRECT, True, detail="insufficient id evidence to compare"
        )
    if str(url_id).strip() != str(product_id).strip():
        return GateResult(
            GateKind.IDENTITY_REDIRECT,
            False,
            reason_code=ReasonCode.GATE_IDENTITY_REDIRECT,
            detail=(
                f"url id {url_id!r} != served product id {product_id!r}; "
                "the page served a different product than was requested (F10)"
            ),
        )
    return GateResult(GateKind.IDENTITY_REDIRECT, True, detail=f"id {product_id!r} consistent")


# ---------------------------------------------------------------------- bundle

_BUNDLE_PATTERNS: tuple[str, ...] = (
    r"\bfree\s+(?:gift|bundle)\b",
    r"\bworth\s+₹",
    r"\bcombo\b",
    r"\bbundle\b",
    r"\bwith\s+free\b",
)


def bundle_gate(candidate: CandidateProduct) -> GateResult:
    """Flag value-added bundles. **Never rejects** (M0 §6.5).

    A soundbar bundled with a laptop does not change what the laptop is; it does
    affect price comparability. Flagging preserves the listing while making the
    caveat visible.
    """
    title = str(candidate.title) if is_known(candidate.title) else ""
    haystack = f"{title} {' '.join(candidate.bundle_items)}".lower()
    flagged = candidate.is_bundle is True or any(
        re.search(p, haystack) for p in _BUNDLE_PATTERNS
    )
    if flagged:
        return GateResult(
            GateKind.BUNDLE,
            True,
            flagged=True,
            reason_code=ReasonCode.FLAG_BUNDLE,
            detail="listing appears to include a bundled item; price comparability affected",
        )
    return GateResult(GateKind.BUNDLE, True, detail="no bundle signal")


# ------------------------------------------------------------------ evaluation


def run_gates(
    reference: CandidateProduct,
    candidate: CandidateProduct,
    *,
    assume_new_when_unmarked: bool = True,
) -> tuple[GateResult, ...]:
    """Evaluate every gate in :data:`GATE_ORDER`.

    All gates run even after one fails, so a report shows every reason a listing was
    ineligible rather than only the first. The classifier keys on the earliest
    failure in :data:`GATE_ORDER`, which makes the headline reason deterministic.
    """
    evaluated: dict[GateKind, GateResult] = {
        GateKind.ACCESSORY: accessory_gate(candidate, resolve_product_category(reference)),
        GateKind.CATEGORY: category_gate(reference, candidate),
        GateKind.CONDITION: condition_gate(
            candidate, assume_new_when_unmarked=assume_new_when_unmarked
        ),
        GateKind.IDENTITY_REDIRECT: identity_redirect_gate(candidate),
        GateKind.BUNDLE: bundle_gate(candidate),
    }
    return tuple(evaluated[kind] for kind in GATE_ORDER)


def first_failure(gates: Sequence[GateResult]) -> GateResult | None:
    """The earliest failing gate in :data:`GATE_ORDER`, or None if all passed."""
    for kind in GATE_ORDER:
        for gate in gates:
            if gate.kind is kind and not gate.passed:
                return gate
    return None


__all__ = [
    "GATE_ORDER",
    "accessory_gate",
    "bundle_gate",
    "category_gate",
    "condition_gate",
    "detect_condition",
    "first_failure",
    "identity_redirect_gate",
    "run_gates",
]
