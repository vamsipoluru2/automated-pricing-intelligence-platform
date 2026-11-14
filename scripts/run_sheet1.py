"""Fill Sheet1 of the derivative mapping workbook.

Two independent jobs so they can run in parallel:

    python scripts/run_sheet1.py flipkart
    python scripts/run_sheet1.py croma

Each writes its own JSON under artifacts/reports/. A third step merges them into
the workbook, so neither job holds the file open and a Croma block never costs the
Flipkart results.

Model-code extraction is the part that actually gated throughput. Croma material
descriptions use at least four shapes, and three of them defeat a naive
``\\b<letters><digits><letters>\\b`` pattern:

    ASUS      MB1628WS BQ224WS HN007WS      2 letters + 3-4 digits + 2 letters
    HP spaced HE00015QU FC0499AU           2 letters + 4-5 digits + 2 letters
    HP fused  HP15VICTUSFA2197TX14GI5      no delimiters at all
    Dell      DC15255 DC15250              2 letters + 5 digits, no trailing letters

Lenovo is deliberately excluded from code matching: Croma stores only the MTM
*tail* (``LOQE FPIN`` is the last four characters of ``83xxxxFPIN``, M0 §10.5).
A four-character fragment is far too weak to assert identity with, so those SKUs
take the specification-only path instead of a substring gamble.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import openpyxl  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from croma_intel.platforms.flipkart.pdp_extractor import resolve_pdp_price  # noqa: E402
from croma_intel.platforms.flipkart.search import FlipkartCard, harvest_cards  # noqa: E402

WORKBOOK = (pathlib.Path(__file__).resolve().parents[2]
            / "V3" / "price-scraper" / "derivative mapping.xlsx")
REPORTS = pathlib.Path(__file__).resolve().parents[1] / "artifacts" / "reports"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

#: Ordered longest-first so a 5-digit Dell code is not truncated to a 4-digit match.
CODE_PATTERNS: tuple[str, ...] = (
    r"[A-Z]{2}\d{5}[A-Z]{2}",   # HE00015QU
    r"[A-Z]{2}\d{4}[A-Z]{2}",   # FC0499AU, FA2197TX, MB1628WS
    r"[A-Z]{2}\d{3}[A-Z]{2}",   # BQ224WS, HN007WS, LY060WS
    r"[A-Z]{2}\d{5}",           # DC15255
)
#: Tokens that look like a code but are units or Croma's own encoding.
_CODE_NOISE = re.compile(r"^(GB|TB|SSD|HDD|MRP)")

CPU_TOKENS: tuple[tuple[str, str], ...] = (
    ("CU7", "Intel Core Ultra 7"), ("CU5", "Intel Core Ultra 5"),
    ("7KR7", "AMD Ryzen 7"), ("7KR5", "AMD Ryzen 5"), ("7KR3", "AMD Ryzen 3"),
    ("A7R7", "AMD Ryzen 7"), ("13GI7", "Intel Core i7"), ("13GI5", "Intel Core i5"),
    ("13GI3", "Intel Core i3"), ("GI7", "Intel Core i7"), ("GI5", "Intel Core i5"),
    ("GI3", "Intel Core i3"), ("C5", "Intel Core i5"), ("C3", "Intel Core i3"),
    ("R7", "AMD Ryzen 7"), ("R5", "AMD Ryzen 5"),
)


def extract_code(description: str) -> str | None:
    """The manufacturer model code, or None when the description carries only a tail."""
    text = description.upper()
    if "LENOVO" in text:
        return None  # Croma stores an MTM tail only; too weak to match on
    for pattern in CODE_PATTERNS:
        for match in re.finditer(pattern, text):
            token = match.group(0)
            if not _CODE_NOISE.match(token):
                return token
    return None


def card_carries_code(card: FlipkartCard, code: str | None) -> bool:
    """True when the listing itself carries *code* — in its URL path or its title.

    Never the full href: Flipkart card links carry ``?q=<search text>``, so a search
    for the code makes *every* card "contain" it. Observed 2026-09-16 — searching
    ``FX608JMI-TU251WS`` "matched" mobile skins and toner cartridges.
    """
    if not code:
        return False
    path = card.url.split("?", 1)[0]
    return code.lower() in f"{path} {card.title}".lower()


def build_query(description: str, brand: str, category: str) -> str:
    text = description.upper()
    if category != "Laptops":
        # Strip Croma's internal encoding; the plain words are the searchable part.
        words = [w for w in re.split(r"[\s\-()]+", description)
                 if w and not re.fullmatch(r"[A-Z0-9]{6,}", w.upper())]
        return " ".join([brand] + words[:7])
    cpu = next((v for k, v in CPU_TOKENS if k in text), "")
    ram = re.search(r"(\d{1,2})GB", text)
    size = re.search(r"\b(1[3-7])\b", text)
    storage = "1TB SSD" if "1TB" in text else "512GB SSD" if "512" in text else ""
    series = next((s for s in ("VICTUS", "TUF", "VIVOBOOK", "IDEAPAD", "LOQ", "LEGION",
                               "PAVILION", "INSPIRON", "OMEN") if s in text), "")
    return " ".join(x for x in [brand, series.title(), size.group(1) if size else "",
                                cpu, f"{ram.group(1)}GB" if ram else "", storage,
                                "Laptop"] if x)


def load_rows() -> list[tuple[str, str, str, str]]:
    wb = openpyxl.load_workbook(WORKBOOK, data_only=True)
    return [(str(r[0]).strip(), str(r[1]), str(r[2]), str(r[3]).strip())
            for r in wb["Sheet1"].iter_rows(min_row=2, values_only=True) if r[0]]


# ----------------------------------------------------------------- flipkart


def run_flipkart() -> None:
    out_path = REPORTS / "fkt_sheet1.json"
    out = json.loads(out_path.read_text()) if out_path.exists() else {}
    rows = [r for r in load_rows() if r[0] not in out]
    print(f"flipkart: {len(rows)} SKUs to attempt", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        ctx = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata",
                                  viewport={"width": 1440, "height": 900}, user_agent=UA)
        page = ctx.new_page()
        for sku, desc, brand, category in rows:
            code = extract_code(desc)
            query = build_query(desc, brand, category)
            try:
                page.goto("https://www.flipkart.com/", wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(1300)
                page.keyboard.press("Escape")
                box = page.locator("input[name='q']").first
                box.fill(query)
                page.wait_for_timeout(200)
                box.press("Enter")
                try:
                    page.wait_for_url("**/search**", timeout=20000)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(2800)
                cards = harvest_cards(page.content(), limit=20)
                hits = [c for c in cards if card_carries_code(c, code)]
                if not hits:
                    print(f"  {sku} {str(code):10} none  cards={len(cards)}", flush=True)
                    continue
                card = hits[0]
                page.goto(card.url, wait_until="domcontentloaded", timeout=50000)
                page.wait_for_timeout(2200)
                mop, _mrp, _note = resolve_pdp_price(page.content())
                out[sku] = {"code": code, "fkt_link": page.url.split("?")[0],
                            "fkt_mop": str(getattr(mop, "amount", "")) or None,
                            "match": "exact_model_code"}
                print(f"  {sku} {code:10} EXACT {str(out[sku]['fkt_mop']):>9}", flush=True)
                out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                print(f"  {sku} FAILED {type(exc).__name__}", flush=True)
        browser.close()
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"flipkart done: {len(out)} matched", flush=True)


# -------------------------------------------------------------------- croma


def run_croma() -> None:
    """Croma via the proven M1 discovery flow, stopping immediately on a block."""
    from croma_intel.browser.playwright_driver import PlaywrightDriver
    from croma_intel.config.settings import Settings
    from croma_intel.platforms.croma.discovery import CromaDiscovery, DiscoveryResult

    out_path = REPORTS / "croma_sheet1.json"
    out = json.loads(out_path.read_text()) if out_path.exists() else {}
    rows = [r for r in load_rows() if r[0] not in out]
    settings = Settings()
    object.__setattr__(settings.rate_limit, "max_requests_per_session", 200)
    object.__setattr__(settings.rate_limit, "min_seconds_between_requests", 3.0)

    # Headed, always: headless is refused by Akamai on the first homepage load
    # (HTTP 403, 2026-09-16), while headed passes the full M1 chain.
    driver = PlaywrightDriver(settings, headless=False)
    driver.start()
    try:
        disc = CromaDiscovery(driver, settings)
        result = DiscoveryResult()
        if not disc.open_homepage(result):
            print(f"croma: BLOCKED at homepage - {result.steps[0].detail}", flush=True)
            return
        disc.handle_pincode(result, settings.croma.test_pincode)
        print(f"croma: homepage ok, {len(rows)} SKUs to attempt", flush=True)
        for sku, _desc, _brand, _cat in rows:
            step = DiscoveryResult()
            try:
                if not disc.search_via_ui(step, sku):
                    print(f"  {sku} search failed", flush=True)
                    continue
                cands = disc.wait_for_rendered_results(step)
                report = disc.identify(step, cands, sku, None)
                if report.ok and report.chosen:
                    price = re.search(r"₹\s*([\d,]+)", report.chosen.title)
                    out[sku] = {"croma_link": report.chosen.url,
                                "croma_mop": price.group(1).replace(",", "") if price else None}
                    print(f"  {sku} OK {str(out[sku]['croma_mop']):>9}", flush=True)
                    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
                else:
                    print(f"  {sku} {report.confidence}", flush=True)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                print(f"  {sku} FAILED {type(exc).__name__}", flush=True)
                if "403" in msg or "Access Denied" in msg:
                    print("  *** blocked - stopping ***", flush=True)
                    break
    finally:
        driver.close()
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"croma done: {len(out)} resolved", flush=True)


if __name__ == "__main__":
    REPORTS.mkdir(parents=True, exist_ok=True)
    job = sys.argv[1] if len(sys.argv) > 1 else ""
    if job == "flipkart":
        run_flipkart()
    elif job == "croma":
        run_croma()
    else:
        raise SystemExit("usage: run_sheet1.py [flipkart|croma]")
