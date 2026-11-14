"""Platform adapter protocols.

Every competitor platform implements these two roles and nothing else. The contract
is deliberately narrow so that Amazon, Reliance Digital and Vijay Sales can be added
later **without touching** ``domain/``, ``normalization/`` or ``matching/``.

The one hard rule: an adapter's output is a platform-neutral
:class:`~croma_intel.domain.candidate.CandidateProduct`. No Flipkart PID, no Croma
reducer path, no Vijay Sales URL quirk may leak past this boundary. Everything
downstream compares candidates without knowing where they came from.

Protocols rather than base classes: adapters differ enormously in mechanics (Vijay
Sales is static HTML, Reliance Digital is an SPA, Flipkart needs expander clicks) and
inheritance would force a shape none of them share.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from ..domain.candidate import CandidateProduct
from ..domain.capture import RawCapture


@runtime_checkable
class PlatformSearchAdapter(Protocol):
    """Drives a platform's visible search UI and harvests result cards.

    Implementations must use the rendered UI. Constructing a search URL or calling an
    internal JSON API is forbidden — M1 established on Croma that a constructed search
    URL can render zero products while the real UI renders them immediately, and an
    undocumented API is a dependency nobody agreed to.
    """

    platform: str

    def search(self, query: str) -> RawCapture:
        """Run *query* through the visible search UI. Returns the results capture."""
        ...

    def harvest(self, capture: RawCapture, *, limit: int) -> Sequence[Any]:
        """Read result cards from a stored capture. Pure — no network."""
        ...


@runtime_checkable
class PlatformPDPExtractor(Protocol):
    """Turns a captured product page into a platform-neutral candidate.

    Must be a pure function of the capture, so that every candidate is reproducible
    offline from its artifact (rule 1).
    """

    platform: str

    def extract(self, capture: RawCapture, **context: Any) -> CandidateProduct:
        """Extract a candidate from a stored PDP capture. Pure — no network."""
        ...


__all__ = ["PlatformPDPExtractor", "PlatformSearchAdapter"]
