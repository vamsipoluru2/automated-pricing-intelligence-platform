"""Matching vocabulary — the types every M3 decision is expressed in.

Pure, stdlib-only. No comparison logic lives here; this module exists so that
``normalization``, ``matching`` and the platform adapters all speak the same
language, and so that the decision table in ``matching/classifier.py`` is a lookup
over named states rather than a pile of booleans.

Two rules shape everything below:

* **Rule 3 — missing information never produces MATCH.** Every enum here carries an
  explicit UNKNOWN/INSUFFICIENT state that is a real outcome, not a null.
* **Rule 8 — classification is a decision table.** Score orders candidates *within*
  a classification and can never move one between classifications.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ------------------------------------------------------------------ capability


class ModelAvailability(str, Enum):
    """Whether a platform exposes a manufacturer model code at all.

    M0 §21.2. Declared per platform rather than inferred per listing, because the
    difference between "this listing happens to omit the MTM" and "this platform
    never publishes one" changes the achievable classification. F1 established
    Flipkart as NEVER — pending Q3, which M3b-1 must answer.
    """

    ALWAYS = "ALWAYS"
    SOMETIMES = "SOMETIMES"
    NEVER = "NEVER"
    UNKNOWN = "UNKNOWN"


class ModelLocation(str, Enum):
    TITLE = "TITLE"
    SPEC_TABLE = "SPEC_TABLE"
    URL_SLUG = "URL_SLUG"
    JSONLD = "JSONLD"


# -------------------------------------------------------------------- outcomes


class ModelComparison(str, Enum):
    """Result of comparing two model codes.

    The three positive tiers are ordered by strength and named after M0 §15.1's
    taxonomy. There is deliberately no FUZZY tier: F9 proved ``83DV018NIN`` and
    ``83DV018QIN`` are edit-distance 1 and are different products with different RAM
    and different GPUs, so approximate model matching is banned outright.
    """

    EXACT = "EXACT"                          # T0 — identical after safe normalization
    SUFFIX_EQUIVALENT = "SUFFIX_EQUIVALENT"  # T2 — differ only by a delimited suffix
    EMBEDDED = "EMBEDDED"                    # T3 — one appears as a bounded token in the other's text
    DIFFERENT = "DIFFERENT"                  # both known, and not equal
    UNAVAILABLE = "UNAVAILABLE"              # platform never publishes a model code
    UNKNOWN = "UNKNOWN"                      # should be present, but was not found


#: The comparisons that constitute identity. Ordered strongest first.
DECISIVE_MODEL_COMPARISONS: tuple[ModelComparison, ...] = (
    ModelComparison.EXACT,
    ModelComparison.SUFFIX_EQUIVALENT,
    ModelComparison.EMBEDDED,
)


class SpecVerdict(str, Enum):
    """Three-valued specification comparison. UNKNOWN never counts as agreement."""

    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class MatchClassification(str, Enum):
    """The output of the decision table.

    ``CONFLICT`` is the load-bearing one. It means **the evidence contradicts
    itself**: a decisive model match alongside a mismatched CRITICAL specification.

    It deliberately asserts nothing about *why*. Any of these produce it, and this
    layer cannot distinguish them:

    * either side was mis-extracted;
    * a retailer published an incorrect specification (M2 found exactly that — the
      Croma description claims LPDDR5 where the labelled table says DDR5/SO-DIMM);
    * a manufacturer shipped multiple configurations under one model code;
    * a retailer relabelled or reused a model code.

    Reporting it as a match would be the silent-wrong case rule 10 exists to prevent;
    reporting it as DIFFERENT would bury a contradiction worth seeing. It is neither.
    It is a signal that the evidence disagrees and a human must adjudicate.
    """

    EXACT_MATCH = "EXACT_MATCH"
    EXACT_MATCH_UNVERIFIED = "EXACT_MATCH_UNVERIFIED"
    CONFIGURATION_MATCH = "CONFIGURATION_MATCH"
    VARIANT = "VARIANT"
    CONFLICT = "CONFLICT"
    DIFFERENT = "DIFFERENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REJECTED = "REJECTED"  # failed a gate; never scored


#: Classifications a consumer may treat as "the same product".
POSITIVE_CLASSIFICATIONS: frozenset[MatchClassification] = frozenset(
    {MatchClassification.EXACT_MATCH, MatchClassification.CONFIGURATION_MATCH}
)

#: Classifications that require a human before use.
REVIEW_CLASSIFICATIONS: frozenset[MatchClassification] = frozenset(
    {
        MatchClassification.CONFLICT,
        MatchClassification.EXACT_MATCH_UNVERIFIED,
        MatchClassification.INSUFFICIENT_EVIDENCE,
    }
)


# ----------------------------------------------------------------- reason codes


class ReasonCode(str, Enum):
    """Stable, machine-readable reasons for every gate rejection and classification.

    Every verdict carries exactly one. They are a closed vocabulary so that outcomes
    can be counted, filtered and regression-tested without parsing prose — a
    human-readable ``detail`` string always accompanies the code, but no consumer
    should ever have to read it to know what happened.

    These strings are part of the output contract. Renaming one is a breaking change.
    """

    # --- gate rejections (evaluated before any identity work) -----------------
    GATE_ACCESSORY = "GATE_ACCESSORY"
    GATE_CATEGORY_MISMATCH = "GATE_CATEGORY_MISMATCH"
    GATE_CONDITION_NOT_NEW = "GATE_CONDITION_NOT_NEW"
    GATE_CONDITION_UNKNOWN = "GATE_CONDITION_UNKNOWN"
    GATE_IDENTITY_REDIRECT = "GATE_IDENTITY_REDIRECT"

    # --- non-rejecting flags --------------------------------------------------
    FLAG_BUNDLE = "FLAG_BUNDLE"

    # --- decisive model present ----------------------------------------------
    MODEL_EXACT_SPECS_CORROBORATE = "MODEL_EXACT_SPECS_CORROBORATE"
    MODEL_EXACT_SPECS_PARTIAL = "MODEL_EXACT_SPECS_PARTIAL"
    MODEL_EXACT_SPECS_UNVERIFIED = "MODEL_EXACT_SPECS_UNVERIFIED"
    MODEL_EXACT_SPEC_CONTRADICTION = "MODEL_EXACT_SPEC_CONTRADICTION"

    # --- no model available (the specification-only path) ---------------------
    SPECS_MATCH_MODEL_UNAVAILABLE = "SPECS_MATCH_MODEL_UNAVAILABLE"
    SPECS_MISMATCH_MODEL_UNAVAILABLE = "SPECS_MISMATCH_MODEL_UNAVAILABLE"
    SPECS_PARTIAL_MODEL_UNAVAILABLE = "SPECS_PARTIAL_MODEL_UNAVAILABLE"

    # --- model expected but absent -------------------------------------------
    MODEL_MISSING_SPECS_MISMATCH = "MODEL_MISSING_SPECS_MISMATCH"
    MODEL_MISSING_INSUFFICIENT = "MODEL_MISSING_INSUFFICIENT"

    # --- model present and different -----------------------------------------
    MODEL_DIFFERENT = "MODEL_DIFFERENT"
    SPECS_MATCH_MODEL_DIFFERENT = "SPECS_MATCH_MODEL_DIFFERENT"
    MODEL_DIFFERENT_SAME_SERIES = "MODEL_DIFFERENT_SAME_SERIES"

    # --- insufficient evidence ------------------------------------------------
    INSUFFICIENT_CRITICAL_SPECS = "INSUFFICIENT_CRITICAL_SPECS"
    NO_REFERENCE_IDENTITY = "NO_REFERENCE_IDENTITY"
    #: The reference's category resolves to no registered profile, so there is no
    #: CRITICAL set to judge identity by. Comparing on another category's specs would
    #: be a guess (rule 10).
    REFERENCE_CATEGORY_UNRESOLVED = "REFERENCE_CATEGORY_UNRESOLVED"


#: Reason codes that reject a candidate at a gate, before identity work begins.
GATE_REJECTION_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.GATE_ACCESSORY,
        ReasonCode.GATE_CATEGORY_MISMATCH,
        ReasonCode.GATE_CONDITION_NOT_NEW,
        ReasonCode.GATE_CONDITION_UNKNOWN,
        ReasonCode.GATE_IDENTITY_REDIRECT,
    }
)


# ----------------------------------------------------------------------- gates


class ListingCondition(str, Enum):
    """Condition of the physical item being sold.

    ``STORE_DISPLAY_UNIT`` is F7 — discovered live on Vijay Sales as a *title
    prefix*, not a badge. A floor unit at a 30% discount is plausible enough not to
    trip a price anomaly, matches on model and specs perfectly, and is not new retail
    stock. Without this value it passes every other gate silently.
    """

    NEW = "NEW"
    STORE_DISPLAY_UNIT = "STORE_DISPLAY_UNIT"
    OPEN_BOX = "OPEN_BOX"
    REFURBISHED = "REFURBISHED"
    RENEWED = "RENEWED"
    USED = "USED"
    UNKNOWN = "UNKNOWN"


#: Only these may be compared as retail offers.
COMPARABLE_CONDITIONS: frozenset[ListingCondition] = frozenset({ListingCondition.NEW})


class GateKind(str, Enum):
    CONDITION = "CONDITION"
    ACCESSORY = "ACCESSORY"
    CATEGORY = "CATEGORY"
    BUNDLE = "BUNDLE"
    IDENTITY_REDIRECT = "IDENTITY_REDIRECT"


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict. ``passed=False`` rejects before any scoring (rule 5).

    ``flagged`` is separate from ``passed``: the bundle gate flags a value-added
    free gift without rejecting the listing, because rejecting would discard a
    legitimate competitor price (M0 §6.5).
    """

    kind: GateKind
    passed: bool
    flagged: bool = False
    reason_code: ReasonCode | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "passed": self.passed,
            "flagged": self.flagged,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "detail": self.detail,
        }


# -------------------------------------------------------------------- evidence


@dataclass(frozen=True)
class MatchEvidence:
    """One named signal behind a verdict, with the values it compared.

    Every verdict must be reconstructible from its evidence. A classification with
    no evidence is a bug, not a confident answer.
    """

    kind: str
    detail: str
    reference_value: str = ""
    candidate_value: str = ""
    verdict: SpecVerdict = SpecVerdict.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "reference_value": self.reference_value,
            "candidate_value": self.candidate_value,
            "verdict": self.verdict.value,
        }


@dataclass(frozen=True)
class SpecComparison:
    """The aggregate three-valued verdict across the CRITICAL specification set."""

    matched: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    evidence: tuple[MatchEvidence, ...] = ()

    @property
    def known_count(self) -> int:
        return len(self.matched) + len(self.mismatched)

    @property
    def any_mismatch(self) -> bool:
        return bool(self.mismatched)

    @property
    def all_known_match(self) -> bool:
        return not self.mismatched and bool(self.matched)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": list(self.matched),
            "mismatched": list(self.mismatched),
            "unknown": list(self.unknown),
            "known_count": self.known_count,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class MatchVerdict:
    """The complete, auditable outcome of comparing one candidate to the reference.

    ``score`` orders candidates *within* a classification and is never consulted to
    choose one. ``requires_review`` is derived, not set by hand, so it cannot drift
    away from the classification it describes.
    """

    classification: MatchClassification
    model_comparison: ModelComparison
    specs: SpecComparison
    reason_code: ReasonCode
    gates: tuple[GateResult, ...] = ()
    evidence: tuple[MatchEvidence, ...] = ()
    score: int = 0
    reason: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_positive(self) -> bool:
        return self.classification in POSITIVE_CLASSIFICATIONS

    @property
    def requires_review(self) -> bool:
        return self.classification in REVIEW_CLASSIFICATIONS

    @property
    def rejected_by(self) -> tuple[GateResult, ...]:
        return tuple(g for g in self.gates if not g.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "model_comparison": self.model_comparison.value,
            "reason_code": self.reason_code.value,
            "specs": self.specs.to_dict(),
            "gates": [g.to_dict() for g in self.gates],
            "evidence": [e.to_dict() for e in self.evidence],
            "score": self.score,
            "reason": self.reason,
            "is_positive": self.is_positive,
            "requires_review": self.requires_review,
            "notes": list(self.notes),
            "extra": dict(self.extra),
        }
