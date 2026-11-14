"""Broad DOM discovery — evidence gathering, NOT extraction.

Purpose: when result identification finds nothing, answer *why* without guessing
selectors. This module inspects whatever HTML it is given and reports structure:
how many anchors exist, what href shapes are present, whether specific tokens
appear, which container class names look product-like.

It deliberately does **not** select or extract products. It produces evidence a
human reads to decide what the real selectors should be. Nothing here feeds the
identification path.

Pure, stdlib-only, and therefore runnable over a stored capture with the network
unavailable — the same replay guarantee the rest of the offline core has.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")

# Class/id fragments that commonly denote a result card. Used ONLY for reporting
# what exists — never to select anything.
PRODUCT_HINT_FRAGMENTS = (
    "product", "card", "tile", "plp", "listing", "result", "item", "grid",
)

# Fragments that commonly denote an interstitial covering the page.
OVERLAY_HINT_FRAGMENTS = (
    "modal", "overlay", "popup", "backdrop", "dialog", "consent", "cookie",
    "pincode", "location", "drawer", "interstitial",
)

# Markers that indicate a client-rendered app shell.
# data-react-helmet and div#root were added after the live 2026-08-13 capture, where
# Croma turned out to be a React app that none of the original markers matched.
SPA_MARKERS = (
    "__next_data__", "__nuxt__", "window.__initial_state__", "data-reactroot",
    "data-react-helmet", "ng-version", "id=\"root\"", "id='root'", "div#root",
    "id=\"app\"", "__apollo_state__",
)

# Text that indicates a location/pincode gate rather than a product listing.
# Observed verbatim in the live capture's visible text.
LOCATION_GATE_MARKERS = (
    "select your location",
    "check products & delivery options",
    "sign in to select address",
    "update pincode for best prices",
    "add pincode",
    "enter pincode",
)


@dataclass
class AnchorInfo:
    href: str
    text: str
    classes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DomProbeReport:
    """Structured evidence about a rendered page."""

    html_length: int = 0
    visible_text_length: int = 0

    total_anchors: int = 0
    anchors_with_href: int = 0
    pdp_anchors: int = 0
    pdp_ids: list[str] = field(default_factory=list)
    href_prefix_histogram: dict[str, int] = field(default_factory=dict)
    sample_anchors: list[dict[str, Any]] = field(default_factory=list)

    token_hits: dict[str, dict[str, Any]] = field(default_factory=dict)

    product_hint_classes: dict[str, int] = field(default_factory=dict)
    overlay_hint_classes: dict[str, int] = field(default_factory=dict)

    spa_markers_found: list[str] = field(default_factory=list)
    likely_client_rendered: bool = False
    location_gate_markers: list[str] = field(default_factory=list)
    likely_location_gated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        lines = [
            f"html_length          : {self.html_length}",
            f"visible_text_length  : {self.visible_text_length}",
            f"total_anchors        : {self.total_anchors} ({self.anchors_with_href} with href)",
            f"pdp_anchors (/p/id)  : {self.pdp_anchors}",
        ]
        if self.pdp_ids:
            shown = ", ".join(self.pdp_ids[:12])
            more = "" if len(self.pdp_ids) <= 12 else f" (+{len(self.pdp_ids) - 12} more)"
            lines.append(f"pdp_ids              : {shown}{more}")
        for token, hit in self.token_hits.items():
            state = "FOUND" if hit["present"] else "ABSENT"
            lines.append(
                f"token {token!r:<16}: {state}"
                + (f" x{hit['count']} (first ctx: {hit['contexts'][0][:90]!r})" if hit["present"] else "")
            )
        if self.href_prefix_histogram:
            top = sorted(self.href_prefix_histogram.items(), key=lambda kv: -kv[1])[:8]
            lines.append("href prefixes        : " + ", ".join(f"{k}={v}" for k, v in top))
        if self.product_hint_classes:
            top = sorted(self.product_hint_classes.items(), key=lambda kv: -kv[1])[:8]
            lines.append("product-ish classes  : " + ", ".join(f"{k}={v}" for k, v in top))
        if self.overlay_hint_classes:
            top = sorted(self.overlay_hint_classes.items(), key=lambda kv: -kv[1])[:8]
            lines.append("overlay-ish classes  : " + ", ".join(f"{k}={v}" for k, v in top))
        lines.append(f"spa markers          : {', '.join(self.spa_markers_found) or 'none'}")
        lines.append(f"likely client-rendered: {self.likely_client_rendered}")
        lines.append(f"location gate markers: {', '.join(self.location_gate_markers) or 'none'}")
        lines.append(f"likely location-gated: {self.likely_location_gated}")
        return lines


class _ElementHarvester(HTMLParser):
    """Collect anchors and class/id attributes across the whole document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[AnchorInfo] = []
        self.class_tokens: Counter[str] = Counter()
        self._open_anchor: dict[str, str] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}

        for key in ("class", "id", "data-testid"):
            value = attr.get(key, "")
            for token in re.split(r"[\s_\-]+", value):
                token = token.strip().lower()
                if token:
                    self.class_tokens[token] += 1

        if tag == "a":
            if self._open_anchor is not None:
                self._flush()
            self._open_anchor = {"href": attr.get("href", ""), "classes": attr.get("class", "")}
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._open_anchor is not None:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._open_anchor is None:
            return
        text = _WS_RE.sub(" ", "".join(self._buf)).strip()
        self.anchors.append(
            AnchorInfo(href=self._open_anchor["href"], text=text, classes=self._open_anchor["classes"])
        )
        self._open_anchor = None
        self._buf = []

    def close(self) -> None:  # noqa: D102
        super().close()
        self._flush()


def visible_text_from_html(html: str) -> str:
    """Approximate rendered text by stripping script/style then all tags."""
    without_code = _TAG_RE.sub(" ", html or "")
    text = _ANY_TAG_RE.sub(" ", without_code)
    return _WS_RE.sub(" ", text).strip()


# Regions whose token hits are NOT evidence that products were returned.
# Learned from the live 2026-08-13 capture: searching "323360" found ten hits, and
# every one was the echoed query in <meta>/<title>/analytics URLs. Counting those
# as product evidence produced a confidently wrong root cause.
_HEAD_RE = re.compile(r"<head\b.*?</head>", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b.*?</script>", re.IGNORECASE | re.DOTALL)
_NOISE_NEAR_RE = re.compile(
    r"<meta\b|<title\b|data-react-helmet|href=|src=|%2f|%3d|&amp;|utm_|analytics|gtm|hotjar",
    re.IGNORECASE,
)


def _classify_hit(html: str, idx: int, span_regions: Sequence[tuple[int, int]]) -> str:
    """Label where a token occurrence sits. Only BODY_CONTENT counts as evidence."""
    for lo, hi in span_regions:
        if lo <= idx < hi:
            return "HEAD_OR_SCRIPT"
    window = html[max(0, idx - 120) : idx + 120]
    if _NOISE_NEAR_RE.search(window):
        return "ATTRIBUTE_OR_URL"
    return "BODY_CONTENT"


def find_token(html: str, token: str, *, context_chars: int = 80, max_contexts: int = 5) -> dict[str, Any]:
    """Locate a token in the HTML, classifying WHERE each occurrence sits.

    Case-insensitive. Returns both the raw count and ``meaningful_count`` — hits in
    body content rather than in ``<head>``, ``<script>``, attributes or tracking
    URLs. ``present`` stays raw (useful for "is this string anywhere at all"), while
    ``meaningful`` is what callers should use to conclude that products were
    actually returned.
    """
    if not token:
        return {
            "present": False, "count": 0, "meaningful": False,
            "meaningful_count": 0, "contexts": [], "regions": {},
        }

    haystack = html or ""
    lowered = haystack.lower()
    needle = token.lower()

    excluded: list[tuple[int, int]] = []
    for pattern in (_HEAD_RE, _SCRIPT_RE):
        excluded.extend((m.start(), m.end()) for m in pattern.finditer(haystack))

    contexts: list[str] = []
    regions: Counter[str] = Counter()
    start = 0
    count = 0
    while True:
        idx = lowered.find(needle, start)
        if idx == -1:
            break
        count += 1
        regions[_classify_hit(haystack, idx, excluded)] += 1
        if len(contexts) < max_contexts:
            lo = max(0, idx - context_chars)
            hi = min(len(haystack), idx + len(token) + context_chars)
            contexts.append(_WS_RE.sub(" ", haystack[lo:hi]).strip())
        start = idx + len(needle)

    meaningful_count = regions.get("BODY_CONTENT", 0)
    return {
        "present": count > 0,
        "count": count,
        "meaningful": meaningful_count > 0,
        "meaningful_count": meaningful_count,
        "contexts": contexts,
        "regions": dict(regions),
    }


def _href_prefix(href: str) -> str:
    """Coarse shape of an href, for histogramming."""
    if not href:
        return "(empty)"
    if href.startswith(("http://", "https://")):
        rest = href.split("//", 1)[1]
        path = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    else:
        path = href
    if path.startswith("#"):
        return "#fragment"
    if path.startswith("javascript:"):
        return "javascript:"
    segments = [s for s in path.split("?", 1)[0].split("/") if s]
    if not segments:
        return "/"
    # Collapse a trailing numeric id so /a/p/123 and /b/p/456 histogram together.
    if len(segments) >= 2 and re.fullmatch(r"\d+", segments[-1]):
        prefix = ".../" if len(segments) > 2 else "/"
        return f"{prefix}{segments[-2]}/<numeric>"
    return "/" + segments[0]


def probe_html(
    html: str,
    *,
    tokens: Sequence[str] = (),
    visible_text: str | None = None,
    max_sample_anchors: int = 25,
) -> DomProbeReport:
    """Inspect a page and report its structure. Never selects or extracts products."""
    from ..platforms.croma.result_identifier import pdp_id_from_url

    report = DomProbeReport(html_length=len(html or ""))

    harvester = _ElementHarvester()
    try:
        harvester.feed(html or "")
        harvester.close()
    except Exception:  # noqa: BLE001 - malformed markup must degrade, not explode
        pass

    text = visible_text if visible_text is not None else visible_text_from_html(html)
    report.visible_text_length = len(text)

    report.total_anchors = len(harvester.anchors)
    report.anchors_with_href = sum(1 for a in harvester.anchors if a.href)

    prefixes: Counter[str] = Counter()
    pdp_ids: list[str] = []
    for anchor in harvester.anchors:
        if not anchor.href:
            continue
        prefixes[_href_prefix(anchor.href)] += 1
        pdp_id = pdp_id_from_url(anchor.href)
        if pdp_id is not None:
            pdp_ids.append(pdp_id)

    report.pdp_anchors = len(pdp_ids)
    report.pdp_ids = list(dict.fromkeys(pdp_ids))
    report.href_prefix_histogram = dict(prefixes)
    report.sample_anchors = [
        a.to_dict() for a in harvester.anchors[:max_sample_anchors] if a.href
    ]

    for token in tokens:
        report.token_hits[token] = find_token(html, token)

    lowered_html = (html or "").lower()
    for fragment in PRODUCT_HINT_FRAGMENTS:
        count = harvester.class_tokens.get(fragment, 0)
        if count:
            report.product_hint_classes[fragment] = count
    for fragment in OVERLAY_HINT_FRAGMENTS:
        count = harvester.class_tokens.get(fragment, 0)
        if count:
            report.overlay_hint_classes[fragment] = count

    report.spa_markers_found = [m for m in SPA_MARKERS if m in lowered_html]
    # "Client rendered" here means: the shell is present but the products are not
    # in the served markup. That is the signature of capturing before hydration.
    report.likely_client_rendered = bool(report.spa_markers_found) and report.pdp_anchors == 0

    # Location gate: judged from VISIBLE text, not raw HTML. The markup carries the
    # location widget on every page; what matters is whether it is what the user is
    # actually being shown instead of results.
    lowered_text = text.lower()
    report.location_gate_markers = [m for m in LOCATION_GATE_MARKERS if m in lowered_text]
    report.likely_location_gated = bool(report.location_gate_markers) and report.pdp_anchors == 0

    return report


def diagnose_zero_results(report: DomProbeReport, *, sku: str, model: str | None) -> str:
    """Turn a probe report into a root-cause statement. Reports; never decides.

    Distinguishing these cases matters: 'captured too early' is a bug in our code,
    while 'search genuinely matched nothing' is a finding about Croma. Conflating
    them would send the investigation in the wrong direction.
    """
    sku_hit = report.token_hits.get(str(sku), {})
    model_hit = report.token_hits.get(model or "", {}) if model else {}

    # Only body-content hits count as evidence that products came back. The echoed
    # search query in <meta>/<title>/analytics URLs is not a product.
    sku_meaningful = bool(sku_hit.get("meaningful"))
    model_meaningful = bool(model_hit.get("meaningful"))

    if report.pdp_anchors > 0:
        return (
            f"PDP anchors ARE present ({report.pdp_anchors}); zero-candidate failure is "
            "not a rendering problem. Inspect the identification step instead."
        )

    # Checked BEFORE token evidence: a location gate explains an empty grid on its
    # own, and the query token will always appear somewhere in a search page's head.
    if report.likely_location_gated:
        return (
            "LOCATION/PINCODE GATE. Visible text is the location selector "
            f"({'; '.join(report.location_gate_markers)}) with zero PDP anchors — the page is "
            "asking for a delivery location instead of showing results. Not a block and not an "
            "empty result set. NOTE: a soft/degraded anti-bot response can look identical; "
            "confirm by setting a location and re-running ONCE."
        )

    if report.likely_client_rendered:
        detail = f"SPA markers present ({', '.join(report.spa_markers_found)}) and zero PDP anchors"
        if sku_meaningful:
            detail += (
                f"; SKU {sku!r} appears in body content — results exist in a payload "
                "but were not rendered as links yet"
            )
        return (
            "LIKELY CAPTURED BEFORE HYDRATION. " + detail + ". "
            "This is a capture-timing defect in our driver, not a Croma block and not an empty result set."
        )

    if sku_meaningful or model_meaningful:
        return (
            f"Token present in BODY CONTENT (sku={sku_meaningful}, model={model_meaningful}) "
            "but no PDP anchors were found. Results are probably rendered without /p/ anchor hrefs "
            "(e.g. JS click handlers). The anchor-based parser needs evidence-driven revision."
        )

    if report.overlay_hint_classes:
        return (
            "No PDP anchors and no meaningful SKU/model tokens, but overlay-like containers are "
            f"present ({', '.join(report.overlay_hint_classes)}). An interstitial may be suppressing results."
        )

    noise = ""
    if sku_hit.get("present") and not sku_meaningful:
        noise = (
            f" (SKU {sku!r} occurs {sku_hit.get('count', 0)}x but only in head/script/URL context — "
            "that is the echoed query, not a product)"
        )
    return (
        "No PDP anchors and no product data in body content" + noise + ". "
        "Consistent with Croma genuinely returning no results for this query, OR with a "
        "degraded anti-bot response. Verify the search URL form and check for rate limiting "
        "before concluding."
    )
