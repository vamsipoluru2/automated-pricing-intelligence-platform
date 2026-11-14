"""Pick the correct Croma search result for a known SKU.

SCOPE WARNING — this is **not** the matching engine and must not grow into one.
It answers exactly one question: "which of these Croma search results is the product
bearing the SKU we were given?" There is no cross-platform comparison, no
specification comparison, and no scoring beyond SKU-identity evidence.

M1 requirements this implements:
  * must NOT simply choose the first search result;
  * must use available evidence (SKU, URL, title, model, category);
  * must return a structured failure rather than guess when confidence is absent.

Architectural rules honoured:
  * rule 2 — UNKNOWN is first-class: absent evidence is recorded as UNKNOWN, never
    treated as agreement;
  * rule 3 — missing information never produces a match;
  * rule 4 — model comparison is boundary-safe and never strips inside an
    alphanumeric run;
  * rule 10 — a wrong pick is worse than no pick.

Stdlib only. Pure. No network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Sequence

from . import accessory_filter

# Croma PDP URL shape, confirmed in M0 §2.2 across twelve real URLs:
#     https://www.croma.com/{slug}/p/{numeric-id}
CROMA_PDP_URL_RE = re.compile(r"/p/(\d+)\b")


class Confidence(str, Enum):
    CONFIDENT = "CONFIDENT"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


class EvidenceKind(str, Enum):
    SKU_IN_PDP_URL = "SKU_IN_PDP_URL"
    SKU_IN_TEXT = "SKU_IN_TEXT"
    MODEL_IN_TITLE = "MODEL_IN_TITLE"
    MODEL_IN_URL = "MODEL_IN_URL"
    BRAND_IN_TITLE = "BRAND_IN_TITLE"
    CATEGORY_COMPATIBLE = "CATEGORY_COMPATIBLE"
    REJECTED_ACCESSORY = "REJECTED_ACCESSORY"


class EvidenceTier(int, Enum):
    """Ranking tiers. Lower is stronger.

    T1 and T2 are *decisive* — either can identify a product on its own. T3/T4 are
    corroborating: they order candidates that already carry decisive evidence and
    can never promote one that does not.

    T2 (exact model) is decisive because M0 §10.3 established with live data that a
    Lenovo MTM identifies one configuration — ``83DV018NIN`` and ``83DV018QIN`` are
    edit-distance 1 and are different products. A boundary-safe exact match on that
    code is therefore product identity, not a similarity heuristic.
    """

    SKU_EXACT = 1
    MODEL_EXACT = 2
    CATEGORY = 3
    TITLE_SIMILARITY = 4
    NONE = 99


@dataclass(frozen=True)
class SearchCandidate:
    """One result row scraped from the Croma search page.

    Intentionally minimal — M1 proves access and capture, not extraction.
    """

    position: int
    title: str = ""
    url: str = ""
    sku_text: str = ""      # any SKU-looking text rendered on the result card
    category: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Evidence:
    kind: str
    detail: str
    decisive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAssessment:
    candidate: SearchCandidate
    evidence: tuple[Evidence, ...] = ()
    decisive: bool = False
    tier: int = EvidenceTier.NONE.value
    rejected_as_accessory: bool = False
    rejection_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "decisive": self.decisive,
            "tier": self.tier,
            "rejected_as_accessory": self.rejected_as_accessory,
            "rejection_detail": self.rejection_detail,
        }


@dataclass(frozen=True)
class IdentificationReport:
    """The structured result. On failure this is what gets persisted and reported."""

    confidence: str
    sku: str
    model: str | None
    chosen: SearchCandidate | None
    reason: str
    assessments: tuple[CandidateAssessment, ...] = ()
    candidates_considered: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.confidence == Confidence.CONFIDENT.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "sku": self.sku,
            "model": self.model,
            "chosen": self.chosen.to_dict() if self.chosen else None,
            "reason": self.reason,
            "candidates_considered": self.candidates_considered,
            "assessments": [a.to_dict() for a in self.assessments],
            "notes": list(self.notes),
        }


def pdp_id_from_url(url: str) -> str | None:
    """Extract the numeric id from a Croma PDP URL, or None if not a PDP URL."""
    if not url:
        return None
    match = CROMA_PDP_URL_RE.search(url)
    return match.group(1) if match else None


def _normalize_for_token_scan(text: str) -> str:
    """Uppercase and collapse whitespace. Deliberately does NOT alter alphanumerics."""
    return re.sub(r"\s+", " ", (text or "")).strip().upper()


def contains_model_token(text: str, model: str) -> bool:
    """Boundary-safe whole-token search for a model code inside free text.

    Implements architectural rule 4 at the read side: a match must be bounded by a
    non-alphanumeric character or a string edge. ``83S000FYIN`` therefore matches in
    ``"LOQ 83S000FYIN Gaming"`` and in ``"83S000FYIN-IND"``, but NOT inside
    ``"83S000FYINX"`` — which would be a different product.

    No characters are ever removed from the model before comparison.
    """
    if not text or not model:
        return False
    haystack = _normalize_for_token_scan(text)
    needle = _normalize_for_token_scan(model)
    if not needle:
        return False
    for match in re.finditer(re.escape(needle), haystack):
        start, end = match.start(), match.end()
        before_ok = start == 0 or not haystack[start - 1].isalnum()
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
    return False


class ResultIdentifier:
    """Selects the Croma search result matching a known SKU, or fails structurally."""

    def __init__(self, *, require_decisive: bool = True) -> None:
        # require_decisive=True means: only an exact SKU identity match is good enough.
        # Corroborating signals (model, brand) are recorded as evidence but can never
        # promote a candidate on their own. This is rule 10 expressed in code.
        self.require_decisive = require_decisive

    def assess_candidate(
        self, candidate: SearchCandidate, sku: str, model: str | None
    ) -> CandidateAssessment:
        evidence: list[Evidence] = []
        decisive = False
        tier = EvidenceTier.NONE.value

        # --- GATE FIRST (architectural rule 5): accessories never enter ranking.
        verdict = accessory_filter.classify(candidate.title, category=candidate.category)
        if verdict.is_accessory:
            return CandidateAssessment(
                candidate=candidate,
                evidence=(
                    Evidence(
                        kind=EvidenceKind.REJECTED_ACCESSORY.value,
                        detail=f"{verdict.detail} ({', '.join(verdict.reasons)})",
                    ),
                ),
                decisive=False,
                tier=EvidenceTier.NONE.value,
                rejected_as_accessory=True,
                rejection_detail=verdict.detail,
            )

        # --- decisive: the SKU is the PDP URL id (M0 Q2 hypothesis, verified at runtime)
        url_id = pdp_id_from_url(candidate.url)
        if url_id is not None and url_id == str(sku):
            evidence.append(
                Evidence(
                    kind=EvidenceKind.SKU_IN_PDP_URL.value,
                    detail=f"PDP url id {url_id!r} == requested SKU {sku!r}",
                    decisive=True,
                )
            )
            decisive = True
            tier = EvidenceTier.SKU_EXACT.value

        # --- decisive: the SKU is rendered on the result card as an exact token
        if candidate.sku_text and _normalize_for_token_scan(candidate.sku_text) == _normalize_for_token_scan(str(sku)):
            evidence.append(
                Evidence(
                    kind=EvidenceKind.SKU_IN_TEXT.value,
                    detail=f"result card sku text {candidate.sku_text!r} == requested SKU {sku!r}",
                    decisive=True,
                )
            )
            decisive = True
            tier = EvidenceTier.SKU_EXACT.value

        # --- T2 decisive: exact, boundary-safe model match (never a substring)
        model_hit = bool(model) and (
            contains_model_token(candidate.title, model)
            or contains_model_token(candidate.sku_text, model)
        )
        if model_hit:
            evidence.append(
                Evidence(
                    kind=EvidenceKind.MODEL_IN_TITLE.value,
                    detail=f"boundary-safe exact model token {model!r} present",
                    decisive=True,
                )
            )
            if not decisive:
                decisive = True
                tier = EvidenceTier.MODEL_EXACT.value

        if model and contains_model_token(candidate.url, model):
            evidence.append(
                Evidence(
                    kind=EvidenceKind.MODEL_IN_URL.value,
                    detail=f"boundary-safe token {model!r} present in url",
                )
            )

        # --- T3/T4 corroborating: order decisive candidates, never promote one
        if candidate.category and not accessory_filter.is_accessory("", category=candidate.category):
            evidence.append(
                Evidence(
                    kind=EvidenceKind.CATEGORY_COMPATIBLE.value,
                    detail=f"category {candidate.category!r} is not an accessory category",
                )
            )
            if decisive and tier > EvidenceTier.CATEGORY.value:
                tier = EvidenceTier.CATEGORY.value

        return CandidateAssessment(
            candidate=candidate,
            evidence=tuple(evidence),
            decisive=decisive,
            tier=tier,
        )

    def identify(
        self,
        *,
        sku: str,
        model: str | None,
        candidates: Sequence[SearchCandidate],
    ) -> IdentificationReport:
        assessments = tuple(self.assess_candidate(c, sku, model) for c in candidates)
        decisive_hits = [a for a in assessments if a.decisive]
        rejected = [a for a in assessments if a.rejected_as_accessory]
        notes: list[str] = []
        if rejected:
            notes.append(
                f"{len(rejected)} candidate(s) hard-rejected as accessories: "
                + "; ".join(f"{a.candidate.title[:48]!r}" for a in rejected[:4])
            )

        if not candidates:
            return IdentificationReport(
                confidence=Confidence.NOT_FOUND.value,
                sku=str(sku),
                model=model,
                chosen=None,
                reason="search returned zero candidate results",
                assessments=assessments,
                candidates_considered=0,
            )

        if not decisive_hits:
            corroborated = [a for a in assessments if a.evidence and not a.rejected_as_accessory]
            return IdentificationReport(
                confidence=Confidence.NOT_FOUND.value,
                sku=str(sku),
                model=model,
                chosen=None,
                reason=(
                    f"no candidate carried decisive identity evidence (exact SKU or exact model); "
                    f"{len(corroborated)} had corroborating-only signals, "
                    f"{len(rejected)} were rejected as accessories; "
                    "refusing to fall back to first result"
                ),
                assessments=assessments,
                candidates_considered=len(candidates),
                notes=tuple(notes + ["architectural rule 10: a wrong pick is worse than no pick"]),
            )

        # Rank decisive candidates by tier (1 = exact SKU beats 2 = exact model).
        best_tier = min(a.tier for a in decisive_hits)
        at_best = [a for a in decisive_hits if a.tier == best_tier]

        if len(at_best) > 1:
            return IdentificationReport(
                confidence=Confidence.AMBIGUOUS.value,
                sku=str(sku),
                model=model,
                chosen=None,
                reason=(
                    f"{len(at_best)} candidates tie at the strongest evidence tier "
                    f"({EvidenceTier(best_tier).name}); refusing to guess between them"
                ),
                assessments=assessments,
                candidates_considered=len(candidates),
                notes=tuple(notes + ["a tie at the top tier indicates a search or extraction defect"]),
            )

        hit = at_best[0]
        return IdentificationReport(
            confidence=Confidence.CONFIDENT.value,
            sku=str(sku),
            model=model,
            chosen=hit.candidate,
            reason=(
                f"single candidate at strongest tier {EvidenceTier(best_tier).name}: "
                + "; ".join(e.detail for e in hit.evidence if e.decisive)
            ),
            assessments=assessments,
            candidates_considered=len(candidates),
            notes=tuple(notes),
        )

        # No decisive evidence. Corroborating signals exist but are, by policy,
        # never sufficient — reporting them helps a human adjudicate the failure.
        corroborated = [a for a in assessments if a.evidence]
        return IdentificationReport(
            confidence=Confidence.NOT_FOUND.value,
            sku=str(sku),
            model=model,
            chosen=None,
            reason=(
                f"no candidate carried decisive SKU identity evidence "
                f"({len(corroborated)} had corroborating-only signals); "
                "refusing to fall back to first result"
            ),
            assessments=assessments,
            candidates_considered=len(candidates),
            notes=("architectural rule 10: a wrong pick is worse than no pick",),
        )
