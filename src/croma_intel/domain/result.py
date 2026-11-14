"""Competitor match result schema.

Platform-neutral despite being introduced for Flipkart: Amazon, Reliance Digital and
Vijay Sales will produce the same shape. Nothing here names a Flipkart concept.

Every claim carries its capture id, so any figure in a report can be traced to an
immutable artifact and re-derived offline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .candidate import CandidateProduct
from .matching import MatchVerdict
from .product import UNKNOWN, Money, Unknown


@dataclass(frozen=True)
class MatchedCandidate:
    """One assessed competitor listing, with its verdict and provenance."""

    candidate: CandidateProduct
    verdict: MatchVerdict
    rank: int = 0

    @property
    def candidate_id(self) -> str:
        return str(self.candidate.platform_product_id)

    def to_dict(self) -> dict[str, Any]:
        c, v = self.candidate, self.verdict
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "position": c.discovery.position,
            "title": str(c.title),
            "url": c.discovery.final_url or c.discovery.source_url,
            "source_url": c.discovery.source_url,
            "final_url": c.discovery.final_url,
            "platform": c.platform,
            "platform_product_id": str(c.platform_product_id),
            "model": str(c.model_number),
            "model_series": str(c.model_series),
            "model_availability": c.model_availability.value,
            "classification": v.classification.value,
            "reason_code": v.reason_code.value,
            "spec_state": v.extra.get("spec_state", "UNKNOWN"),
            "matched_specs": list(v.specs.matched),
            "mismatched_specs": list(v.specs.mismatched),
            "unknown_specs": list(v.specs.unknown),
            "condition": c.condition.value,
            "category": list(c.category_path),
            "mop": c.mop.to_dict() if isinstance(c.mop, Money) else "UNKNOWN",
            "mrp": c.mrp.to_dict() if isinstance(c.mrp, Money) else "UNKNOWN",
            "score": v.score,
            "requires_review": v.requires_review,
            "capture_id": c.discovery.capture_id,
            "notes": list(c.notes),
        }


@dataclass(frozen=True)
class ReferenceSummary:
    sku: str
    title: str
    model: str | Unknown = UNKNOWN
    capture_id: str = ""
    platform: str = "croma"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "title": self.title,
            "model": "UNKNOWN" if self.model is UNKNOWN else str(self.model),
            "capture_id": self.capture_id,
            "platform": self.platform,
        }


@dataclass(frozen=True)
class CompetitorMatchResult:
    """The complete outcome of one competitor discovery-and-match run."""

    reference: ReferenceSummary
    platform: str
    query_plan: Mapping[str, Any]
    candidates: tuple[MatchedCandidate, ...] = ()
    selected: tuple[MatchedCandidate, ...] = ()
    search_capture_id: str = ""
    warnings: tuple[str, ...] = ()
    stats: Mapping[str, Any] = field(default_factory=dict)

    @property
    def rejected(self) -> tuple[MatchedCandidate, ...]:
        from .matching import MatchClassification

        return tuple(
            m for m in self.candidates
            if m.verdict.classification is MatchClassification.REJECTED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference.to_dict(),
            "platform": self.platform,
            "query_plan": dict(self.query_plan),
            "search_capture_id": self.search_capture_id,
            "candidates": [m.to_dict() for m in self.candidates],
            "selected_top_3": [m.to_dict() for m in self.selected],
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
        }


def summarise(candidates: Sequence[MatchedCandidate]) -> dict[str, int]:
    """Count candidates by classification. Used for the run's stats block."""
    counts: dict[str, int] = {}
    for match in candidates:
        key = match.verdict.classification.value
        counts[key] = counts.get(key, 0) + 1
    return counts


__all__ = [
    "CompetitorMatchResult",
    "MatchedCandidate",
    "ReferenceSummary",
    "summarise",
]
