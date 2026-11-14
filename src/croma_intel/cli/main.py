"""M1 CLI. Stdlib argparse — no third-party dependency in the entry point.

Commands:
    m1-gate   run the browser access gate (REQUIRES network + playwright)
    replay    read a stored capture offline (never touches the network)
    captures  list / verify stored captures
    doctor    report environment readiness
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..browser.artifacts import CaptureStore
from ..browser.playwright_driver import playwright_available
from ..config.settings import Settings
from ..domain.errors import CaptureError


def _load_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.load(getattr(args, "config", None))
    return settings


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    ok, detail = playwright_available()
    store = CaptureStore(settings.storage.captures_dir)
    print("croma-intel doctor")
    print(f"  python            : {sys.version.split()[0]}")
    print(f"  playwright        : {'OK' if ok else 'MISSING'} ({detail})")
    print(f"  captures dir      : {settings.storage.captures_dir}")
    print(f"  stored captures   : {len(store.list_capture_ids())}")
    print(f"  croma base url    : {settings.croma.base_url}")
    print(f"  headless default  : {settings.browser.headless}")
    if not ok:
        print("\n  To enable the live gate:")
        print("    pip install playwright")
        print("    playwright install chromium")
    return 0 if ok else 1


def cmd_m1_gate(args: argparse.Namespace) -> int:
    from ..gate.m1_gate import GateOutcome, run_m1_gate

    settings = _load_settings(args)
    headless = None
    if args.headed:
        headless = False
    elif args.headless:
        headless = True

    if getattr(args, "cdp", None):
        from dataclasses import replace as _replace

        settings = _replace(
            settings, browser=_replace(settings.browser, cdp_endpoint=args.cdp)
        )
        print(f"attached mode: driving the Chrome already running at {args.cdp}")

    if getattr(args, "fresh", False):
        # A clean access test must not inherit cookies from an earlier session.
        # Reused storage state can carry the very signature that got the previous
        # session blocked, which would make the test measure history rather than
        # current access. Disabling persistence also stops this run overwriting a
        # stored state with its own.
        from dataclasses import replace

        settings = replace(
            settings, browser=replace(settings.browser, persist_storage_state=False)
        )
        print("fresh session: storage state disabled (no cookie or context reuse)")

    report = run_m1_gate(
        sku=args.sku,
        model=args.model,
        settings=settings,
        headless=headless,
    )

    out_path = Path(settings.storage.reports_dir) / f"m1_gate_{args.sku}.json"
    report.write(out_path)

    print(f"\nM1 GATE: {report.outcome}")
    print(f"  sku            : {report.sku}")
    print(f"  model          : {report.model}")
    print(f"  pincode        : {report.pincode}")
    print(f"  discovered PDP : {report.pdp_url or '(none — not fabricated)'}")
    print(f"  report         : {out_path}")
    print("\n  steps:")
    for step in report.steps:
        mark = "PASS" if step.ok else "FAIL"
        print(f"    [{mark}] {step.name}: {step.detail[:160]}")

    disc = report.discovery or {}
    if disc:
        print("\n  discovery evidence:")
        print(f"    pincode detected  : {disc.get('pincode_detected')} (reused={disc.get('pincode_already_set')})")
        print(f"    search input used : {disc.get('search_input_selector')!r}")
        print(f"    submitted text    : {disc.get('search_submitted_text')!r}")
        print(f"    results rendered  : {disc.get('results_rendered')}")
        sel = disc.get("selected")
        if sel:
            print(f"    selected          : pos={sel.get('position')} id={sel.get('sku_text')}")
            print(f"                        {str(sel.get('title'))[:100]!r}")
        if disc.get("pdp_verification"):
            print(f"    pdp verification  : {disc['pdp_verification']}")
    if report.root_cause:
        print(f"\n  ROOT CAUSE: {report.root_cause}")
    print(f"\n  {report.summary}\n")

    if report.outcome == GateOutcome.GO:
        return 0
    if report.outcome == GateOutcome.INCONCLUSIVE:
        return 2
    return 1


def cmd_diagnose(args: argparse.Namespace) -> int:
    """One-shot live evidence capture for debugging the search/identification layer.

    Navigates, waits for results, snapshots everything (HTML, visible text,
    screenshot, input values, navigation status), then runs broad DOM discovery and
    prints a root-cause statement. Deliberately does NOT guess selectors — it reports
    what exists so selectors can be written from evidence.
    """
    from ..browser.dom_probe import diagnose_zero_results, probe_html
    from ..browser.playwright_driver import PlaywrightDriver
    from ..domain.capture import PageKind

    settings = _load_settings(args)
    ok, detail = playwright_available()
    if not ok:
        print(f"Playwright unavailable: {detail}", file=sys.stderr)
        print("Run: pip install -e '.[browser]' && playwright install chromium", file=sys.stderr)
        return 2

    store = CaptureStore(settings.storage.captures_dir)
    driver = PlaywrightDriver(settings, headless=not args.headed)
    try:
        driver.start()
        url = settings.search_url(args.sku)
        print(f"navigating: {url}")
        nav = driver.goto(url)
        wait = driver.wait_for_results()

        # Decisive experiment, same page load: does satisfying the location gate
        # make products appear? A second navigation would be blocked (Croma allows
        # ~one per window), so this must happen without navigating again.
        location = None
        if args.set_pincode:
            print(f"attempting location gate with pincode {args.set_pincode} (no re-navigation)...")
            location = driver.set_location(args.set_pincode)
            print(f"  field_found={location.field_found} "
                  f"pdp_anchors {location.pdp_anchors_before} -> {location.pdp_anchors_after}")
            for step in location.steps:
                print(f"    {step}")

        scroll = driver.scroll_until_stable()
        capture = driver.capture(
            page_kind=PageKind.SEARCH,
            requested_url=url,
            nav=nav,
            input_sku=str(args.sku),
            scroll=scroll,
            results_wait=wait,
            screenshot_label=f"diagnose_{args.sku}",
            extra={
                "stage": "diagnose",
                "search_inputs": driver.input_values(),
                "location_attempt": location.to_dict() if location else None,
                "network": driver.network_log(),
            },
        )
        store.write(capture)
    finally:
        driver.close()

    tokens = [str(args.sku)] + ([args.model] if args.model else [])
    probe = probe_html(capture.html, tokens=tokens, visible_text=capture.visible_text or None)

    print("\n" + "=" * 68)
    print("LIVE DIAGNOSTIC — Croma search")
    print("=" * 68)
    print(f"requested url   : {capture.requested_url}")
    print(f"final url       : {capture.final_url}")
    print(f"redirected      : {capture.redirected}")
    print(f"http status     : {capture.http_status}")
    print(f"capture status  : {capture.status}")
    print(f"page title      : {capture.page_title!r}")
    print(f"capture_id      : {capture.capture_id}")
    print(f"screenshot      : {capture.screenshot_path}")
    print(f"block verdict   : blocked={capture.block.is_blocked} ({capture.block.detail})")
    print(f"results wait    : {capture.results_wait.to_dict()}")
    print(f"scroll          : {capture.scroll.to_dict()}")
    print(f"search inputs   : {capture.extra.get('search_inputs')}")
    print("-" * 68)
    for line in probe.summary_lines():
        print(line)
    print("-" * 68)
    print("SAMPLE ANCHORS (first 25 with href):")
    for a in probe.sample_anchors:
        print(f"  {a['href'][:95]}  | {a['text'][:55]!r}")
    print("-" * 68)
    print("VISIBLE TEXT (first 1200 chars):")
    print((capture.visible_text or "(none captured)")[:1200])
    print("-" * 68)
    net = capture.extra.get("network") or []
    print(f"NETWORK (XHR/fetch): {len(net)} recorded")
    interesting = [
        n for n in net
        if any(k in n["url"].lower() for k in ("search", "product", "plp", "catalog", "occ", "query"))
    ]
    if interesting:
        print("  candidate results-API calls:")
        for n in interesting[:12]:
            print(f"    [{n['status']}] {n['method']} {n['url'][:110]}")
            if n.get("body_length") is not None:
                print(f"        body_length={n['body_length']} snippet={str(n.get('body_snippet'))[:150]!r}")
    else:
        print("  NO results-API call was made at all — the app never requested products.")
    non_200 = [n for n in net if n["status"] >= 400]
    if non_200:
        print(f"  !! {len(non_200)} XHR responses with status >= 400:")
        for n in non_200[:8]:
            print(f"    [{n['status']}] {n['url'][:110]}")
    print("-" * 68)
    if location is not None:
        print("LOCATION-GATE EXPERIMENT:")
        print(f"  pincode field found : {location.field_found}")
        print(f"  pdp anchors before  : {location.pdp_anchors_before}")
        print(f"  pdp anchors after   : {location.pdp_anchors_after}")
        print(f"  products appeared   : {location.products_appeared}")
        print("  VERDICT: " + (
            "LOCATION GATE CONFIRMED — products render once a location is set."
            if location.products_appeared else
            "location gate NOT confirmed — setting a pincode did not produce products; "
            "a degraded anti-bot response remains the competing explanation."
        ))
        print("-" * 68)
    print("ROOT CAUSE:", diagnose_zero_results(probe, sku=str(args.sku), model=args.model))
    print("=" * 68)
    print(f"\nFull HTML stored offline. Re-inspect any time with:")
    print(f"  python -m croma_intel.cli.main probe --capture-id {capture.capture_id} "
          f"--sku {args.sku}" + (f" --model {args.model}" if args.model else ""))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Run broad DOM discovery over a STORED capture. Never touches the network."""
    from ..browser.dom_probe import diagnose_zero_results, probe_html

    settings = _load_settings(args)
    store = CaptureStore(settings.storage.captures_dir)
    try:
        capture = store.read(args.capture_id, verify=not args.no_verify)
    except CaptureError as exc:
        print(f"PROBE FAILED: {exc}", file=sys.stderr)
        return 1

    tokens = ([str(args.sku)] if args.sku else []) + ([args.model] if args.model else [])
    probe = probe_html(capture.html, tokens=tokens, visible_text=capture.visible_text or None)

    print(f"capture   : {capture.capture_id}")
    print(f"final url : {capture.final_url}")
    print(f"status    : {capture.status}  http={capture.http_status}")
    print(f"title     : {capture.page_title!r}")
    print("-" * 68)
    for line in probe.summary_lines():
        print(line)
    if args.sku:
        print("-" * 68)
        print("ROOT CAUSE:", diagnose_zero_results(probe, sku=str(args.sku), model=args.model))
    if args.json:
        print("-" * 68)
        print(json.dumps(probe.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Read a capture with the network unavailable. Proves the M0 replay guarantee."""
    settings = _load_settings(args)
    store = CaptureStore(settings.storage.captures_dir)
    try:
        capture = store.read(args.capture_id, verify=not args.no_verify)
    except CaptureError as exc:
        print(f"REPLAY FAILED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(capture.to_manifest(include_html=False), indent=2, sort_keys=True))
    print(f"\nhtml bytes loaded offline: {len(capture.html)}")
    ok, reason = capture.verify_integrity()
    print(f"integrity: {'OK' if ok else 'FAILED'} ({reason})")
    return 0 if ok else 1


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract the authoritative product record from a stored PDP capture.

    Offline by construction — it reads one artifact and touches nothing else.
    """
    from ..domain.errors import ExtractionError
    from ..platforms.croma.pdp_extractor import extract_croma_pdp, iter_sourced_fields

    settings = _load_settings(args)
    store = CaptureStore(args.store or settings.storage.captures_dir)
    try:
        capture = store.read(args.capture_id, verify=not args.no_verify)
    except CaptureError as exc:
        print(f"EXTRACT FAILED: {exc}", file=sys.stderr)
        return 1

    try:
        product = extract_croma_pdp(capture)
    except ExtractionError as exc:
        print(f"EXTRACT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(product.to_dict(), indent=2, sort_keys=True))
        return 0

    print(f"capture   : {product.provenance.capture_id[:16]}…")
    print(f"pincode   : {product.provenance.pincode.value}  ({product.provenance.pincode.path})")
    print(f"url       : {product.provenance.final_url}")
    print("\n--- identity / pricing ---")
    for name, field in iter_sourced_fields(product):
        value = field.value
        value = getattr(value, "raw", value)
        print(f"  {name:28} = {str(value)[:46]:48} <- {field.path}")
    print(f"\nprice status : {product.pricing.status.value}")
    print(
        f"specs        : {product.specifications.group_count} groups, "
        f"{product.specifications.feature_count} features"
    )
    print(
        f"offers       : {product.offers.extracted_count} extracted, "
        f"declared {product.offers.declared_count}"
    )
    print(f"unknown      : {list(product.unknown_fields) or 'none'}")
    if product.warnings:
        print("warnings     :")
        for warning in product.warnings:
            print(f"  - {warning}")
    return 0


def cmd_captures(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    store = CaptureStore(settings.storage.captures_dir)
    ids = store.list_capture_ids()
    if not ids:
        print("no captures stored")
        return 0
    if args.verify:
        failures = 0
        for capture_id, ok, reason in store.verify_all():
            print(f"[{'OK  ' if ok else 'FAIL'}] {capture_id} {reason}")
            failures += 0 if ok else 1
        print(f"\n{len(ids)} captures, {failures} failures")
        return 1 if failures else 0
    for capture_id in ids:
        print(capture_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="croma-intel",
        description="Croma competitive intelligence — M1 browser access gate",
    )
    parser.add_argument("--config", help="path to a JSON settings file", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    gate = sub.add_parser("m1-gate", help="run the M1 browser access gate")
    gate.add_argument("--sku", required=True, help="Croma SKU, e.g. 323360")
    gate.add_argument("--model", default=None, help="model number, e.g. 83S000FYIN")
    gate.add_argument(
        "--cdp", default=None, metavar="URL",
        help="attach to a running Chrome (e.g. http://127.0.0.1:9222) instead of "
             "launching an automated browser; avoids navigator.webdriver detection",
    )
    gate.add_argument(
        "--fresh", action="store_true",
        help="start a clean browser context: no cookie, storage-state or session reuse",
    )
    mode = gate.add_mutually_exclusive_group()
    mode.add_argument("--headed", action="store_true", help="run with a visible browser")
    mode.add_argument("--headless", action="store_true", help="force headless")
    gate.set_defaults(func=cmd_m1_gate)

    diag = sub.add_parser("diagnose", help="LIVE: capture full evidence from Croma search and report root cause")
    diag.add_argument("--sku", required=True)
    diag.add_argument("--model", default=None)
    diag.add_argument("--headed", action="store_true", help="run with a visible browser")
    diag.add_argument(
        "--set-pincode",
        default=None,
        metavar="PIN",
        help="after loading, try to satisfy the location gate with this pincode "
             "WITHOUT re-navigating, and report whether products then appear",
    )
    diag.set_defaults(func=cmd_diagnose)

    probe = sub.add_parser("probe", help="OFFLINE: broad DOM discovery over a stored capture")
    probe.add_argument("--capture-id", required=True)
    probe.add_argument("--sku", default=None)
    probe.add_argument("--model", default=None)
    probe.add_argument("--json", action="store_true", help="also emit the full probe report as JSON")
    probe.add_argument("--no-verify", action="store_true")
    probe.set_defaults(func=cmd_probe)

    replay = sub.add_parser("replay", help="load a stored capture offline")
    replay.add_argument("--capture-id", required=True)
    replay.add_argument("--no-verify", action="store_true")
    replay.set_defaults(func=cmd_replay)

    extract = sub.add_parser(
        "extract", help="OFFLINE: extract the product record from a stored PDP capture"
    )
    extract.add_argument("--capture-id", required=True, help="capture id to extract")
    extract.add_argument("--store", default=None, help="capture store root (default: configured)")
    extract.add_argument("--json", action="store_true", help="emit the full record as JSON")
    extract.add_argument("--no-verify", action="store_true", help="skip integrity verification")
    extract.set_defaults(func=cmd_extract)

    caps = sub.add_parser("captures", help="list or verify stored captures")
    caps.add_argument("--verify", action="store_true")
    caps.set_defaults(func=cmd_captures)

    doctor = sub.add_parser("doctor", help="report environment readiness")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
