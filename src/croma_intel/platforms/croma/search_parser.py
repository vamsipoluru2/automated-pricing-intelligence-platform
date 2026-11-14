"""Pure HTML parsing of a Croma search page into candidate results.

Deliberately implemented over ``html.parser`` (stdlib) rather than via live
Playwright selectors. Two reasons, both architectural:

  * it is a **pure function of stored HTML**, so it satisfies the M0 replay
    guarantee — search results can be re-derived offline from a capture with the
    network unavailable;
  * it is testable without a browser, which is the only way it could be verified
    at all in the current dev sandbox.

The live driver may still use CSS selectors for richer extraction; this parser is
the dependency-free floor that always works, keyed on the one thing M0 actually
confirmed: Croma PDP URLs contain ``/p/{numeric-id}``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from .result_identifier import SearchCandidate, pdp_id_from_url

_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", (text or "")).strip()


class _AnchorHarvester(HTMLParser):
    """Collect every <a href> plus its inner text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._depth = 0
        self._current: dict[str, str] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            if self._current is not None:
                self._depth += 1
            return
        if self._current is not None:  # nested anchor: close the outer one first
            self._flush()
        attr = {k: (v or "") for k, v in attrs}
        self._current = {
            "href": attr.get("href", ""),
            "title": attr.get("title", ""),
            "aria_label": attr.get("aria-label", ""),
            "data_testid": attr.get("data-testid", ""),
        }
        self._buf = []
        self._depth = 0

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._flush()
        elif self._current is not None and self._depth > 0:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._current is None:
            return
        self._current["text"] = _clean("".join(self._buf))
        self.anchors.append(self._current)
        self._current = None
        self._buf = []

    def close(self) -> None:  # noqa: D102
        super().close()
        self._flush()


def extract_search_candidates(
    html: str,
    *,
    base_url: str = "https://www.croma.com",
    limit: int = 40,
) -> list[SearchCandidate]:
    """Extract PDP candidates from Croma search HTML.

    Deduplicates by PDP id, keeping the first occurrence and preferring the richest
    title text found for that id. Position reflects document order, which is what a
    "first result" heuristic *would* have used — recorded so tests can prove the
    identifier does not rely on it.
    """
    parser = _AnchorHarvester()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup must degrade, not explode
        pass

    by_id: dict[str, SearchCandidate] = {}
    order: list[str] = []

    for anchor in parser.anchors:
        href = anchor.get("href", "")
        if not href:
            continue
        absolute = urljoin(base_url, href)
        pdp_id = pdp_id_from_url(absolute)
        if pdp_id is None:
            continue

        title = anchor.get("text") or anchor.get("title") or anchor.get("aria_label") or ""
        title = _clean(title)

        if pdp_id in by_id:
            # Prefer the longer/more descriptive title for the same product.
            existing = by_id[pdp_id]
            if len(title) > len(existing.title):
                by_id[pdp_id] = SearchCandidate(
                    position=existing.position,
                    title=title,
                    url=existing.url,
                    sku_text=existing.sku_text,
                    category=existing.category,
                )
            continue

        order.append(pdp_id)
        by_id[pdp_id] = SearchCandidate(
            position=len(order),
            title=title,
            url=absolute,
            sku_text="",
            category="",
        )

    return [by_id[pid] for pid in order[:limit]]


def extract_page_title(html: str) -> str:
    """Best-effort <title> extraction. Used when a browser title is unavailable."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL)
    return _clean(match.group(1)) if match else ""
