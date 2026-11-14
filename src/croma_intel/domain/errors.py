"""Typed errors. Every failure mode is named so it can be handled, logged, and tested.

Architectural rule 10: a wrong match is worse than no match. The corollary is that
failures must be loud and specific — a swallowed exception becomes a silent-wrong
result later.
"""

from __future__ import annotations


class CromaIntelError(Exception):
    """Base for every error this project raises."""


# --------------------------------------------------------------------- capture


class CaptureError(CromaIntelError):
    """Something went wrong producing or reading a capture artifact."""


class CaptureNotFoundError(CaptureError):
    """Requested capture id is not present in the store."""


class CaptureIntegrityError(CaptureError):
    """A stored capture failed hash/id verification — treat as corrupt, not as data."""


# --------------------------------------------------------------------- browser


class BrowserError(CromaIntelError):
    """Base for browser-layer failures."""


class BrowserUnavailableError(BrowserError):
    """Playwright (or its browser binary) is not installed or cannot launch.

    Distinct from a navigation failure: nothing was attempted against the network.
    """


class NavigationError(BrowserError):
    """Navigation did not complete successfully."""


class BotWallDetected(BrowserError):
    """A CAPTCHA / bot wall / access-denied page was served.

    Raised so that a block can never be mistaken for an empty product page.
    """

    def __init__(self, message: str, evidence: object | None = None) -> None:
        super().__init__(message)
        self.evidence = evidence


# ------------------------------------------------------------------ extraction


class ExtractionError(CromaIntelError):
    """Base for failures turning a capture into structured product data.

    Extraction is a pure function over a stored artifact, so every one of these is
    reproducible offline from the capture that caused it.
    """


class JsParseError(ExtractionError):
    """The embedded JavaScript object literal did not match the expected grammar.

    Carries the offset so the offending region of a 441 KB payload can be inspected
    directly rather than bisected by hand.
    """

    def __init__(self, message: str, position: int = -1) -> None:
        super().__init__(message)
        self.position = position


class StateBlobNotFoundError(ExtractionError):
    """The page carries no ``window.__INITIAL_DATA__`` assignment.

    Distinct from a parse failure: nothing was malformed, the expected state simply
    is not there — which usually means the page is not a PDP, or is a bot wall.
    """


class ReducerNotFoundError(ExtractionError):
    """A required top-level reducer is absent from the state blob."""


class StructureError(ExtractionError):
    """A payload parsed cleanly but does not have the shape the extractor requires.

    Raised rather than returning partial data: rule 10 prefers a loud refusal over a
    confident guess assembled from a structure nobody has verified.
    """


# ------------------------------------------------------------------- identity


class IdentificationError(CromaIntelError):
    """The correct product could not be confidently identified.

    Deliberately an error rather than a best-guess return value: M1 requires a
    structured failure over a guess.
    """

    def __init__(self, message: str, report: object | None = None) -> None:
        super().__init__(message)
        self.report = report
