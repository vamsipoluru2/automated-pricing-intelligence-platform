"""Top-N selection — ranking, strictly after classification.

Why ranking lives here and not in ``matching/``
-----------------------------------------------
M3a's architecture guard asserts that ``matching/`` contains no ``sorted(``,
``.sort(`` or ``rank`` function. That guard is deliberate: it makes "gates and
classification never depend on ordering" a structural property rather than a promise.
Ranking therefore lives in ``pipeline/``, downstream of the classifier, and the guard
stays intact.

The ordering rule
-----------------
Classification dominates absolutely. :data:`SELECTION_ORDER` is a fixed hierarchy of
classes; ``MatchVerdict.score`` only breaks ties *inside* one class. A cheaper or
better-corroborated candidate can never climb into a higher class — M3a already
enforces that arithmetically (class ranks are ≥20 apart, the maximum spec bonus is 8)
and a test re-asserts it here.

Eligibility
-----------
:data:`ELIGIBLE_CLASSIFICATIONS` excludes CONFLICT, DIFFERENT, INSUFFICIENT_EVIDENCE
and REJECTED. INSUFFICIENT_EVIDENCE is excluded by explicit decision (2026-08-27):
rule 3 says missing information never produces a result, so a candidate nobody could
verify does not belong in a top 3 even to fill it out. **Returning fewer than three
is a valid outcome** and is preferred to padding.

Pure. No I/O, no network.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.matching import MatchClassification
from ..domain.result import MatchedCandidate

#: Class hierarchy, strongest first. Mirrors M3a's ``_CLASSIFICATION_RANK`` — the
#: two must not disagree, and a test asserts they do not.
SELECTION_ORDER: tuple[MatchClassification, ...] = (
    MatchClassification.EXACT_MATCH,
    MatchClassification.CONFIGURATION_MATCH,
    MatchClassification.EXACT_MATCH_UNVERIFIED,
    MatchClassification.VARIANT,
)

#: Only these may be selected. Everything else is evidence, not a result.
ELIGIBLE_CLASSIFICATIONS: frozenset[MatchClassification] = frozenset(SELECTION_ORDER)

DEFAULT_TOP_N: int = 3


def is_eligible(match: MatchedCandidate) -> bool:
    return match.verdict.classification in ELIGIBLE_CLASSIFICATIONS


def select_top(
    candidates: Sequence[MatchedCandidate], *, top_n: int = DEFAULT_TOP_N
) -> tuple[MatchedCandidate, ...]:
    """Rank eligible candidates and return the best *top_n*.

    Sort key, in order: class position in :data:`SELECTION_ORDER`, then descending
    score, then ascending search position. The last is a deterministic tie-break, not
    a preference — it only decides between candidates that are otherwise identical,
    and it can never lift a lower class above a higher one.
    """
    # Flipkart can render the same product twice on one results page (observed live:
    # PID COMHNDGUAUBQJMKW at positions 1 and 2). Without de-duplication a top 3
    # silently becomes a top 2 with one entry repeated. Keyed on the platform product
    # id, keeping the earliest search position.
    seen: dict[str, MatchedCandidate] = {}
    for match in candidates:
        if not is_eligible(match):
            continue
        key = str(match.candidate.platform_product_id)
        existing = seen.get(key)
        if existing is None or match.candidate.discovery.position < existing.candidate.discovery.position:
            seen[key] = match
    eligible = list(seen.values())
    ordered = sorted(
        eligible,
        key=lambda m: (
            SELECTION_ORDER.index(m.verdict.classification),
            -m.verdict.score,
            m.candidate.discovery.position,
        ),
    )
    return tuple(
        MatchedCandidate(candidate=m.candidate, verdict=m.verdict, rank=index + 1)
        for index, m in enumerate(ordered[:top_n])
    )


def selection_warnings(
    candidates: Sequence[MatchedCandidate], selected: Sequence[MatchedCandidate]
) -> tuple[str, ...]:
    """Honest accounting of what the selection could not deliver."""
    warnings: list[str] = []
    if not selected:
        warnings.append(
            "no candidate reached an eligible classification; returning an empty "
            "selection rather than padding with unverified results"
        )
    elif len(selected) < DEFAULT_TOP_N:
        warnings.append(
            f"only {len(selected)} of {DEFAULT_TOP_N} requested candidates were "
            "eligible; fewer results is preferred to padding"
        )
    review = [m for m in selected if m.verdict.requires_review]
    if review:
        warnings.append(
            f"{len(review)} selected candidate(s) require review: "
            + ", ".join(f"{m.candidate_id}={m.verdict.classification.value}" for m in review)
        )
    return tuple(warnings)


__all__ = [
    "DEFAULT_TOP_N",
    "ELIGIBLE_CLASSIFICATIONS",
    "SELECTION_ORDER",
    "is_eligible",
    "select_top",
    "selection_warnings",
]
