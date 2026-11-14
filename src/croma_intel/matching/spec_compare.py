"""Three-valued CRITICAL specification comparison.

Every comparison yields MATCH, MISMATCH or UNKNOWN, and **UNKNOWN never counts as
agreement** (rules 2 and 3). That single property is what keeps a candidate with six
unknown specs from looking like a candidate with six matching ones.

Comparison is per-canonical-key and mostly exact, with two deliberate exceptions,
both evidence-backed:

* **Display size** uses ±0.15 in, because M0 §9 recorded the same panel published as
  ``39.62 cm`` and ``39.6 cm``. An exact comparator returns MISMATCH on identical
  hardware.
* **Capacities** compare as integers, so ``"512"`` and ``"512GB"`` agree once
  normalized while ``512`` and ``1024`` do not.

Everything else compares exactly, and exactness here is a safety property rather
than laziness: ``RTX 3050A`` must not equal ``RTX 3050``, and ``13450HX`` must not
equal ``13450H``.

Stdlib only. Pure. No I/O.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum

from ..domain.candidate import CandidateProduct
from ..domain.matching import MatchEvidence, SpecComparison, SpecVerdict
from ..domain.product import UNKNOWN, Unknown
from ..normalization.spec_value import (
    CRITICAL_SPECS,
    SpecKey,
    display_sizes_equal,
)

#: How many CRITICAL specs must be *known on both sides* before a specification-only
#: verdict may be reached at all. Below this, the comparison is INSUFFICIENT.
#:
#: Four of eight is a judgement, not a finding — see docs/MATCHING_RULES.md
#: §Assumptions. It is set here as a single named constant precisely so it can be
#: revised against real data once M3b delivers competitor captures.
MIN_KNOWN_CRITICAL_SPECS: int = 4

#: Keys whose values are integer quantities.
_INTEGER_KEYS: frozenset[str] = frozenset(
    {SpecKey.RAM_GB.value, SpecKey.STORAGE_GB.value, SpecKey.GPU_VRAM_GB.value,
     SpecKey.DISPLAY_REFRESH_HZ.value}
)

#: Keys compared with a numeric tolerance rather than for equality.
_TOLERANT_KEYS: frozenset[str] = frozenset({SpecKey.DISPLAY_SIZE_IN.value})


def _as_decimal(text: str) -> Decimal | Unknown:
    try:
        return Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        return UNKNOWN


def compare_spec(key: str, reference: str | Unknown, candidate: str | Unknown) -> SpecVerdict:
    """Compare one canonical spec. UNKNOWN on either side yields UNKNOWN."""
    if reference is UNKNOWN or candidate is UNKNOWN:
        return SpecVerdict.UNKNOWN

    ref_text = str(reference).strip()
    cand_text = str(candidate).strip()
    if not ref_text or not cand_text:
        return SpecVerdict.UNKNOWN

    if key in _TOLERANT_KEYS:
        equal = display_sizes_equal(_as_decimal(ref_text), _as_decimal(cand_text))
        if equal is UNKNOWN:
            return SpecVerdict.UNKNOWN
        return SpecVerdict.MATCH if equal else SpecVerdict.MISMATCH

    if key in _INTEGER_KEYS:
        ref_value = _as_decimal(ref_text)
        cand_value = _as_decimal(cand_text)
        if ref_value is UNKNOWN or cand_value is UNKNOWN:
            return SpecVerdict.UNKNOWN
        return SpecVerdict.MATCH if ref_value == cand_value else SpecVerdict.MISMATCH

    # Exact, case-insensitive. Deliberately not fuzzy: RTX 3050A != RTX 3050.
    return (
        SpecVerdict.MATCH
        if ref_text.casefold() == cand_text.casefold()
        else SpecVerdict.MISMATCH
    )


def compare_specs(
    reference: CandidateProduct,
    candidate: CandidateProduct,
    *,
    critical: tuple[SpecKey, ...] = CRITICAL_SPECS,
) -> SpecComparison:
    """Compare the CRITICAL specification set, producing per-key evidence.

    Every key in *critical* is reported in exactly one bucket, so
    ``matched + mismatched + unknown`` always accounts for the whole set. A key that
    silently vanished would make an incomplete comparison look complete.
    """
    matched: list[str] = []
    mismatched: list[str] = []
    unknown: list[str] = []
    evidence: list[MatchEvidence] = []

    for spec_key in critical:
        key = spec_key.value
        ref_value = reference.spec(key)
        cand_value = candidate.spec(key)
        verdict = compare_spec(key, ref_value, cand_value)

        if verdict is SpecVerdict.MATCH:
            matched.append(key)
        elif verdict is SpecVerdict.MISMATCH:
            mismatched.append(key)
        else:
            unknown.append(key)

        evidence.append(
            MatchEvidence(
                kind=f"SPEC:{key}",
                detail=_detail_for(key, ref_value, cand_value, verdict),
                reference_value="UNKNOWN" if ref_value is UNKNOWN else str(ref_value),
                candidate_value="UNKNOWN" if cand_value is UNKNOWN else str(cand_value),
                verdict=verdict,
            )
        )

    return SpecComparison(
        matched=tuple(matched),
        mismatched=tuple(mismatched),
        unknown=tuple(unknown),
        evidence=tuple(evidence),
    )


def _detail_for(
    key: str, reference: str | Unknown, candidate: str | Unknown, verdict: SpecVerdict
) -> str:
    if verdict is SpecVerdict.UNKNOWN:
        missing = []
        if reference is UNKNOWN:
            missing.append("reference")
        if candidate is UNKNOWN:
            missing.append("candidate")
        return f"{key}: not comparable ({' and '.join(missing) or 'empty value'} unknown)"
    if key in _TOLERANT_KEYS and verdict is SpecVerdict.MATCH:
        return f"{key}: {reference} ≈ {candidate} (within tolerance)"
    symbol = "==" if verdict is SpecVerdict.MATCH else "!="
    return f"{key}: {reference} {symbol} {candidate}"


class SpecState(str, Enum):
    """Aggregate specification state — the classifier's second table axis.

    Four states, not a percentage. A decision table needs discrete inputs; a score
    would reintroduce exactly the threshold tuning rule 8 forbids.
    """

    ALL_MATCH = "ALL_MATCH"        # every CRITICAL spec known on both sides, all agree
    PARTIAL = "PARTIAL"            # no mismatch, enough known, some unknown
    ANY_MISMATCH = "ANY_MISMATCH"  # at least one CRITICAL spec disagrees
    INSUFFICIENT = "INSUFFICIENT"  # too few known to say anything


def spec_state(
    comparison: SpecComparison, *, minimum_known: int = MIN_KNOWN_CRITICAL_SPECS
) -> SpecState:
    """Reduce a comparison to one of four discrete states.

    MISMATCH dominates: one disagreeing CRITICAL spec is decisive regardless of how
    many others agree, because these are the specs that define the configuration.
    """
    if comparison.mismatched:
        return SpecState.ANY_MISMATCH
    if not comparison.unknown and comparison.matched:
        return SpecState.ALL_MATCH
    if comparison.known_count >= minimum_known:
        return SpecState.PARTIAL
    return SpecState.INSUFFICIENT


__all__ = [
    "MIN_KNOWN_CRITICAL_SPECS",
    "SpecState",
    "compare_spec",
    "compare_specs",
    "spec_state",
]
