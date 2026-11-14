"""Executable guards on the ten architectural rules.

These are cheap to run and catch the class of drift that documentation cannot.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "croma_intel"


def _py_files():
    return [p for p in SRC.rglob("*.py") if p.name != "__init__.py"]


class TestEupAbsent(unittest.TestCase):
    """Rule 7: EUP does not exist in this codebase.

    Checked at the AST level — no EUP *identifier* may exist (no field, attribute,
    function, class, or variable). Prose in a docstring declaring EUP out of scope
    is documentation, not an implementation, and is deliberately permitted: banning
    the word outright would discourage stating the boundary explicitly.
    """

    def _identifiers(self, tree):
        import ast

        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.arg) or isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # String *values* are data (e.g. a column name) and do count.
                names.add(node.value)
        return names

    def test_no_eup_identifier_in_source(self):
        import ast
        import re

        pattern = re.compile(r"(^|[^a-z])eup([^a-z]|$)", re.IGNORECASE)
        offenders = []
        for path in _py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc:
                        docstrings.add(doc)
            for name in self._identifiers(tree):
                if name in docstrings:
                    continue  # docstring prose, not an identifier
                if pattern.search(name):
                    offenders.append(f"{path.relative_to(SRC)}: {name!r}")
        self.assertEqual(
            offenders, [], "EUP identifiers must not exist:\n" + "\n".join(offenders)
        )


class TestOfflineCoreHasNoPlaywright(unittest.TestCase):
    """Rule 1: only the driver may import Playwright.

    If any other module imported it, the offline core would stop being importable
    without a browser — and the replay guarantee would become untestable.
    """

    ALLOWED = {"playwright_driver.py"}

    def test_playwright_confined_to_driver(self):
        offenders = []
        for path in _py_files():
            if path.name in self.ALLOWED:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if s.startswith(("import playwright", "from playwright")):
                    offenders.append(f"{path.relative_to(SRC)}:{lineno}")
        self.assertEqual(offenders, [], "playwright imported outside the driver:\n" + "\n".join(offenders))


class TestOfflineCoreIsStdlibOnly(unittest.TestCase):
    """The offline core must import nothing outside the stdlib.

    This is what makes it verifiable in an environment with no package installer.
    """

    OFFLINE_MODULES = (
        "domain/capture.py",
        "domain/errors.py",
        "domain/product.py",
        "browser/block_detector.py",
        "browser/artifacts.py",
        "browser/dom_probe.py",
        "config/settings.py",
        "extraction/js_object.py",
        "extraction/initial_data.py",
        "domain/matching.py",
        "domain/candidate.py",
        "normalization/model_code.py",
        "normalization/spec_value.py",
        "matching/gates.py",
        "matching/spec_compare.py",
        "matching/classifier.py",
        "matching/profiles.py",
        "platforms/accessory_filter.py",
        "platforms/croma/result_identifier.py",
        "platforms/croma/search_parser.py",
        "platforms/croma/selectors.py",
        "platforms/croma/offer_parser.py",
        "platforms/croma/pdp_extractor.py",
    )
    THIRD_PARTY = ("pydantic", "playwright", "yaml", "requests", "httpx", "bs4", "lxml", "pandas")

    def test_no_third_party_imports(self):
        offenders = []
        for rel in self.OFFLINE_MODULES:
            path = SRC / rel
            self.assertTrue(path.exists(), f"expected offline module missing: {rel}")
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if not (s.startswith("import ") or s.startswith("from ")):
                    continue
                for pkg in self.THIRD_PARTY:
                    if s.startswith(f"import {pkg}") or s.startswith(f"from {pkg}"):
                        offenders.append(f"{rel}:{lineno}: {s}")
        self.assertEqual(offenders, [], "third-party import in offline core:\n" + "\n".join(offenders))

    def test_offline_modules_import_cleanly(self):
        """Import every offline module. Fails loudly if a dependency crept in."""
        import importlib

        for rel in self.OFFLINE_MODULES:
            module = "croma_intel." + rel[:-3].replace("/", ".")
            importlib.import_module(module)


class TestExtractionIsPure(unittest.TestCase):
    """Rule 1: extraction is a pure function over a capture.

    Checked by inspection because the property is architectural, not behavioural:
    a module that merely *can* open a socket has already broken the guarantee that
    a stored capture is replayable forever, whether or not a given test exercises it.
    """

    EXTRACTION_MODULES = (
        "extraction/js_object.py",
        "extraction/initial_data.py",
        "platforms/croma/offer_parser.py",
        "platforms/croma/pdp_extractor.py",
        "normalization/model_code.py",
        "normalization/spec_value.py",
        "matching/gates.py",
        "matching/spec_compare.py",
        "matching/classifier.py",
        "matching/profiles.py",
    )
    FORBIDDEN = ("socket", "urllib", "http", "requests", "httpx", "playwright", "subprocess")

    def test_no_network_or_process_imports(self):
        import ast

        offenders = []
        for rel in self.EXTRACTION_MODULES:
            tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    root = name.split(".")[0]
                    if root in self.FORBIDDEN:
                        offenders.append(f"{rel}:{node.lineno}: {name}")
        self.assertEqual(offenders, [], "extraction must not import I/O:\n" + "\n".join(offenders))

    def test_extraction_does_no_file_io(self):
        """``open`` in the extraction layer would mean a source outside the capture."""
        import ast

        offenders = []
        for rel in self.EXTRACTION_MODULES:
            tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in ("open", "eval", "exec", "compile")
                ):
                    offenders.append(f"{rel}:{node.lineno}: {node.func.id}()")
        self.assertEqual(offenders, [], "\n".join(offenders))


class TestMopIsSourced(unittest.TestCase):
    """Rule 6: a figure becomes MOP only via an allow-listed source path."""

    def test_allowlist_is_a_specific_price_path(self):
        from croma_intel.platforms.croma.pdp_extractor import MOP_PATHS, MRP_PATHS

        self.assertEqual(MOP_PATHS, ("pdpPriceReducer.pdpPriceData.sellingPrice.value",))
        self.assertEqual(MRP_PATHS, ("pdpPriceReducer.pdpPriceData.mrp.value",))

    def test_allowlist_excludes_conditional_prices(self):
        from croma_intel.platforms.croma.pdp_extractor import MOP_PATHS

        joined = " ".join(MOP_PATHS).lower()
        for forbidden in ("emi", "exchange", "offer", "coupon", "bank", "fee", "personal"):
            self.assertNotIn(forbidden, joined)


class TestScopeDiscipline(unittest.TestCase):
    """The milestone boundary, as it stands after M3a.

    **Amended deliberately for M3 (scope change S2, approved 2026-08-26).** This class
    was ``TestM1ScopeDiscipline`` and asserted that ``matching`` did not exist and that
    ``platforms/`` contained only ``croma``. M3a legitimately introduces the matching
    package, so the first assertion had to change.

    It is amended rather than deleted. The point of the guard is to make each
    milestone's boundary executable, and a boundary that moves silently is no boundary
    at all — so the list below states exactly what is permitted *now* and what remains
    out of scope until its own milestone.
    """

    #: Permitted as of M3a.
    ALLOWED_PACKAGES = frozenset(
        {"browser", "cli", "config", "domain", "extraction", "gate", "matching",
         "normalization", "pipeline", "platforms"}
    )

    #: Still forbidden. Each belongs to a later milestone and must not appear early.
    FORBIDDEN_MODULES = ("ranking", "excel", "reporting", "llm", "derivative", "scoring")

    def test_no_out_of_scope_packages(self):
        present = {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}
        overlap = present.intersection(self.FORBIDDEN_MODULES)
        self.assertEqual(overlap, set(), f"out of scope for M3a: {overlap}")

    def test_no_unexpected_packages_appeared(self):
        present = {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}
        unexpected = present - self.ALLOWED_PACKAGES
        self.assertEqual(
            unexpected, set(), f"undeclared package(s) {unexpected}; amend ALLOWED_PACKAGES deliberately"
        )

    #: Adapters that have passed their own M3b gate. Amended per milestone, never
    #: widened speculatively: an adapter may only appear here once a real capture of
    #: that platform exists in tests/fixtures.
    GATED_PLATFORMS = frozenset({"croma", "flipkart"})

    def test_only_gated_platform_adapters_exist(self):
        """M3b-1 admitted Flipkart. Amazon, Reliance Digital and Vijay Sales must not
        appear until each passes its own gate — writing an adapter against a page
        nobody has captured is the failure mode M1 rev. 2-6 demonstrated."""
        platforms_dir = SRC / "platforms"
        subdirs = {p.name for p in platforms_dir.iterdir() if p.is_dir() and p.name != "__pycache__"}
        self.assertEqual(subdirs, set(self.GATED_PLATFORMS), f"ungated adapter: {subdirs}")

    def test_future_platforms_are_not_implemented(self):
        platforms_dir = SRC / "platforms"
        present = {p.name for p in platforms_dir.iterdir() if p.is_dir()}
        for future in ("amazon", "amazon_in", "reliance_digital", "vijay_sales"):
            self.assertNotIn(future, present, f"{future} is out of scope for M3b-1")

    def test_no_platform_specific_leakage_into_the_core(self):
        """No platform *parsing detail* may appear in domain/, normalization/ or matching/.

        Deliberately scoped to markup specifics (data-id, srno=, /p/itm), not to the
        platform name. ``Platform.FLIPKART`` is a legitimate enum member, and a
        docstring explaining why the specification-only path exists is documentation.
        Banning the word would punish prose that declares the boundary - the same trap
        the EUP guard was rewritten to avoid.
        """
        import re

        leaks = []
        for package in ("domain", "normalization", "matching"):
            for path in (SRC / package).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                code = chr(10).join(
                    line for line in text.splitlines()
                    if not line.strip().startswith(("#", chr(34)*3, "*"))
                )
                for token in ("data-id", "srno=", "/p/itm", "COMH"):
                    if re.search(re.escape(token), code, re.IGNORECASE):
                        leaks.append(f"{path.relative_to(SRC)}: {token}")
        self.assertEqual(
            leaks, [], "platform detail leaked into the core: " + "; ".join(leaks)
        )

    def test_no_ranking_in_the_matching_layer(self):
        """Constraints 6 and 7: classification only, no final ranking, no derivatives."""
        matching_dir = SRC / "matching"
        for path in matching_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in ("def rank(", "def rank_", "sorted(", ".sort("):
                with self.subTest(file=path.name, banned=banned):
                    self.assertNotIn(banned, text, f"{path.name} must not rank ({banned})")


class TestNoLlmDecisionAuthority(unittest.TestCase):
    """Rule 8 / constraint 10, checked structurally across the whole source tree."""

    CLIENTS = ("anthropic", "openai", "langchain", "litellm", "llama_cpp", "transformers")

    def test_no_llm_client_is_imported_anywhere(self):
        import ast

        offenders = []
        for path in _py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    if name.split(".")[0].lower() in self.CLIENTS:
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno}: {name}")
        self.assertEqual(offenders, [], "no LLM client may be imported:\n" + "\n".join(offenders))


class TestUnknownIsFirstClass(unittest.TestCase):
    """Rule 2 / rule 3, checked behaviourally rather than by inspection."""

    def test_absent_evidence_never_yields_a_match(self):
        from croma_intel.platforms.croma.result_identifier import (
            Confidence,
            ResultIdentifier,
            SearchCandidate,
        )

        report = ResultIdentifier().identify(
            sku="323360",
            model=None,  # unknown model
            candidates=[SearchCandidate(position=1, title="", url="https://www.croma.com/a/p/999")],
        )
        self.assertNotEqual(report.confidence, Confidence.CONFIDENT.value)
        self.assertIsNone(report.chosen)


if __name__ == "__main__":
    unittest.main()
