"""Stdlib test runner for the offline suite.

Runs without pytest and without any third-party package, so the offline core stays
verifiable in an environment with no package installer.

    python -m tests.run_offline
    python -m tests.run_offline -v

Live Croma tests live in tests/integration and are NEVER collected here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OFFLINE_DIRS = ("tests/unit", "tests/replay")


def build_suite() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for rel in OFFLINE_DIRS:
        path = ROOT / rel
        if path.exists():
            suite.addTests(loader.discover(str(path), pattern="test_*.py", top_level_dir=str(ROOT)))
    return suite


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    verbosity = 2 if ("-v" in argv or "--verbose" in argv) else 1

    print("=" * 70)
    print("CROMA-INTEL — OFFLINE TEST SUITE (no network, no third-party deps)")
    print("=" * 70)
    print(f"python : {sys.version.split()[0]}")
    print(f"root   : {ROOT}")
    print(f"scope  : {', '.join(OFFLINE_DIRS)}")
    print("-" * 70)

    result = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stdout).run(build_suite())

    print("-" * 70)
    print(
        f"run={result.testsRun} "
        f"failures={len(result.failures)} "
        f"errors={len(result.errors)} "
        f"skipped={len(result.skipped)}"
    )
    print("RESULT:", "PASS" if result.wasSuccessful() else "FAIL")
    print("=" * 70)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
