"""Deterministic CAPTCHA / bot-wall / access-denied detection.

M0 established the requirement: a blocked page must fail as a *capture* failure and
must never flow downstream as "a page with no content". M0 also recorded the
converse requirement — normal Croma content must not be classified as a block.

Design: weighted signals with an explicit threshold, plus *decisive* signals that
alone force a BLOCKED verdict. Weak signals require corroboration. Every verdict
carries its evidence, including clean verdicts.

Stdlib only. Pure. No network, no Playwright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..domain.capture import BlockEvidence

# --------------------------------------------------------------------------- signals


@dataclass(frozen=True)
class Signal:
    name: str
    weight: float
    decisive: bool = False


# Decisive: these alone mean blocked. Chosen because each is unambiguous — no
# legitimate product page returns 403, or titles itself "Access Denied".
DECISIVE_HTTP_STATUSES: Mapping[int, str] = {
    403: "HTTP_403_FORBIDDEN",
    429: "HTTP_429_TOO_MANY_REQUESTS",
    503: "HTTP_503_SERVICE_UNAVAILABLE",
}

# Title patterns. Matched case-insensitively against the <title> only, which keeps
# them from firing on body prose that happens to discuss the same words.
DECISIVE_TITLE_PATTERNS: Sequence[tuple[str, str]] = (
    (r"\baccess denied\b", "TITLE_ACCESS_DENIED"),
    (r"\battention required\b", "TITLE_ATTENTION_REQUIRED"),
    (r"\bjust a moment\b", "TITLE_JUST_A_MOMENT"),
    (r"\brobot check\b", "TITLE_ROBOT_CHECK"),
    (r"\bare you a human\b", "TITLE_ARE_YOU_HUMAN"),
    (r"\bsecurity check\b", "TITLE_SECURITY_CHECK"),
    (r"\bblocked\b", "TITLE_BLOCKED"),
    (r"\b403 forbidden\b", "TITLE_403"),
)

# Body markers. Deliberately specific strings from real block pages rather than bare
# words like "captcha", which appear in legitimate help/FAQ content.
BODY_MARKERS: Sequence[tuple[str, str, float]] = (
    (r"/cdn-cgi/challenge-platform", "CF_CHALLENGE_PLATFORM", 1.0),
    (r"\bcf-browser-verification\b", "CF_BROWSER_VERIFICATION", 1.0),
    (r"\bcf_chl_opt\b", "CF_CHALLENGE_OPT", 1.0),
    (r"\bg-recaptcha\b", "RECAPTCHA_WIDGET", 1.0),
    (r"\bh-captcha\b", "HCAPTCHA_WIDGET", 1.0),
    (r"www\.google\.com/recaptcha/api\.js", "RECAPTCHA_SCRIPT", 1.0),
    (r"\bpx-captcha\b", "PERIMETERX_CAPTCHA", 1.0),
    (r"\b_Incapsula_Resource\b", "INCAPSULA", 1.0),
    (r"enter the characters you see below", "AMAZON_ROBOT_CHECK", 1.0),
    (r"to discuss automated access to amazon data", "AMAZON_AUTOMATED_ACCESS", 1.0),
    # Observed live on croma.com 2026-08-13: the 2nd and 3rd requests within minutes
    # returned HTTP 403 with this Akamai error page. Decisive on its own.
    (r"errors\.edgesuite\.net", "AKAMAI_EDGESUITE_DENY", 1.0),
    (r"you don't have permission to access", "PERMISSION_DENIED_TEXT", 1.0),
    (r"\bakamai reference\b", "AKAMAI_REF", 0.7),
    (r"\breference #\d+\.[0-9a-f]+\.\d+\.[0-9a-f]+", "AKAMAI_REF_ID", 0.7),
    (r"\brequest unsuccessful\b", "REQUEST_UNSUCCESSFUL", 0.5),
    (r"\bunusual traffic\b", "UNUSUAL_TRAFFIC", 0.7),
    (r"\byour request has been blocked\b", "REQUEST_BLOCKED", 1.0),
    (r"\bverify you are (a )?human\b", "VERIFY_HUMAN", 1.0),
)

# A page this small is not a real PDP. On its own it is only suggestive — a genuine
# error page can also be small — so it needs corroboration to reach the threshold.
TINY_BODY_BYTES = 2048
SMALL_BODY_BYTES = 20_000

DEFAULT_THRESHOLD = 1.0


class BlockDetector:
    """Assess whether a fetched page is a bot wall rather than real content.

    ``content_markers`` are strings whose presence indicates *legitimate* content for
    the platform. They suppress weak size-based signals, which is what keeps a slim
    but genuine page from being misread as a block.
    """

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        content_markers: Sequence[str] = (),
    ) -> None:
        self.threshold = threshold
        self.content_markers = tuple(m.lower() for m in content_markers)

    def assess(
        self,
        *,
        html: str,
        title: str = "",
        http_status: int | None = None,
    ) -> BlockEvidence:
        signals: list[str] = []
        score = 0.0
        decisive: str | None = None
        details: list[str] = []

        html_l = (html or "").lower()
        title_l = (title or "").lower()

        # --- decisive: HTTP status
        if http_status is not None and http_status in DECISIVE_HTTP_STATUSES:
            name = DECISIVE_HTTP_STATUSES[http_status]
            signals.append(name)
            decisive = decisive or name
            score += 10.0
            details.append(f"http_status={http_status}")

        # --- decisive: title
        for pattern, name in DECISIVE_TITLE_PATTERNS:
            if re.search(pattern, title_l):
                signals.append(name)
                decisive = decisive or name
                score += 10.0
                details.append(f"title matched /{pattern}/")

        # --- weighted: body markers
        for pattern, name, weight in BODY_MARKERS:
            if re.search(pattern, html_l):
                signals.append(name)
                score += weight
                if weight >= 1.0 and decisive is None:
                    decisive = name
                details.append(f"body matched /{pattern}/ (+{weight})")

        # --- weak: body size, suppressed by legitimate content markers
        has_content_marker = any(m in html_l for m in self.content_markers)
        body_len = len(html or "")
        if not has_content_marker:
            if body_len < TINY_BODY_BYTES:
                signals.append("BODY_TINY")
                score += 0.8
                details.append(f"body_len={body_len} < {TINY_BODY_BYTES}")
            elif body_len < SMALL_BODY_BYTES:
                signals.append("BODY_SMALL")
                score += 0.3
                details.append(f"body_len={body_len} < {SMALL_BODY_BYTES}")
        elif body_len < TINY_BODY_BYTES:
            # Content marker present but the page is minuscule — still worth noting,
            # but not scored, because the marker is positive evidence of real content.
            details.append(f"body_len={body_len} but content marker present; size signal suppressed")

        is_blocked = decisive is not None or score >= self.threshold

        return BlockEvidence(
            is_blocked=is_blocked,
            score=round(score, 3),
            threshold=self.threshold,
            decisive_signal=decisive,
            signals=tuple(signals),
            detail="; ".join(details) if details else "no block signals detected",
        )


def is_empty_shell(html: str, *, content_markers: Sequence[str], min_length: int = 5000) -> bool:
    """Detect the M0 F3 case: HTTP 200 with no meaningful content (Reliance Digital).

    Distinct from a block — the server answered normally, it just rendered nothing
    server-side. Treating it as success would produce a phantom "product with no data".
    """
    if not html:
        return True
    html_l = html.lower()
    if any(m.lower() in html_l for m in content_markers):
        return False
    return len(html) < min_length
