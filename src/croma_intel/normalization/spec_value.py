"""Specification value normalization and compound-row splitting.

Scope change S1, approved 2026-08-26. It is unavoidable: the two platforms cannot
otherwise be compared at all.

    Croma        ``Processor Variant`` = ``7535HS``          (already split)
    Vijay Sales  ``Processor`` = ``AMD Ryzen 7 7435HS (8C/16T, 3.1/4.5GHz, 4MB L2/16MB L3)``

M0 §9 found compound rows are *the norm, not the exception* — one Vijay Sales row
yields four canonical specs. The architecture's implicit one-row-one-spec assumption
needed this stage; §21.7 asked for it explicitly.

Two invariants carry over from :mod:`model_code` and are equally load-bearing here:

* **Never strip a variant suffix.** ``RTX 3050A`` is a genuinely distinct lower-TDP
  part from ``RTX 3050`` (M0 §9 finding 3). Normalising the ``A`` away merges two
  different products exactly as stripping ``IN`` from an MTM would.
* **UNKNOWN over a guess.** Every parser returns UNKNOWN rather than a default when
  the text does not clearly carry the value.

Dimensional tolerance
---------------------
Display size is the one field where exact comparison is *wrong*. M0 §9 finding 2
recorded the same physical panel published as ``39.62 cm`` and ``39.6 cm`` —
15.598 in and 15.590 in. An exact comparator returns MISMATCH on identical hardware.
:data:`DISPLAY_SIZE_TOLERANCE_IN` exists for that and nothing else.

Stdlib only. Pure. No I/O.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import Enum

from ..domain.product import UNKNOWN, Unknown

# --------------------------------------------------------------- canonical keys


class SpecKey(str, Enum):
    """Canonical specification identities, platform-neutral."""

    CPU_BRAND = "cpu.brand"
    CPU_FAMILY = "cpu.family"
    CPU_MODEL = "cpu.model"
    CPU_CORES = "cpu.cores"
    CPU_THREADS = "cpu.threads"

    RAM_GB = "ram.size_gb"
    RAM_TYPE = "ram.type"
    RAM_FORM_FACTOR = "ram.form_factor"

    STORAGE_GB = "storage.size_gb"
    STORAGE_TYPE = "storage.type"

    GPU_BRAND = "gpu.brand"
    GPU_MODEL = "gpu.model"
    GPU_VRAM_GB = "gpu.vram_gb"

    DISPLAY_SIZE_IN = "display.size_in"
    DISPLAY_REFRESH_HZ = "display.refresh_hz"
    DISPLAY_RESOLUTION = "display.resolution"
    DISPLAY_PANEL = "display.panel"

    OS = "os"


#: Specs that decide identity. A mismatch on any of these means a different product;
#: too many UNKNOWN means INSUFFICIENT_EVIDENCE. Chosen because all eight are present
#: in Croma's 111-feature table *and* derivable from competitor rows observed in M0.
CRITICAL_SPECS: tuple[SpecKey, ...] = (
    SpecKey.CPU_MODEL,
    SpecKey.RAM_GB,
    SpecKey.STORAGE_GB,
    SpecKey.GPU_MODEL,
    SpecKey.GPU_VRAM_GB,
    SpecKey.DISPLAY_SIZE_IN,
    SpecKey.DISPLAY_REFRESH_HZ,
    SpecKey.OS,
)

#: M0 §9 finding 2. 39.6 cm vs 39.62 cm is 0.008 in apart; a real size difference
#: between laptop panels (15.6 vs 16.0) is 0.4 in. The band separates them cleanly.
DISPLAY_SIZE_TOLERANCE_IN: Decimal = Decimal("0.15")

_CM_PER_INCH = Decimal("2.54")


# ------------------------------------------------------------------- primitives


def _decimal(text: str) -> Decimal | Unknown:
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return UNKNOWN


_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(TB|GB|MB)\b", re.IGNORECASE)


def parse_capacity_gb(text: str | None) -> int | Unknown:
    """Capacity in whole GB. ``"512GB"``, ``"1 TB"``, ``"24GB"`` → 512, 1024, 24.

    Returns the **largest** capacity found, because storage rows routinely mention a
    cache or a secondary figure alongside the headline capacity. Returns UNKNOWN when
    no capacity token is present — never 0, which would compare as a real value.
    """
    if not text:
        return UNKNOWN
    best: int | Unknown = UNKNOWN
    for match in _CAPACITY_RE.finditer(str(text)):
        value = _decimal(match.group(1))
        if value is UNKNOWN:
            continue
        assert isinstance(value, Decimal)
        unit = match.group(2).upper()
        gb = value * 1024 if unit == "TB" else value / 1024 if unit == "MB" else value
        as_int = int(gb)
        if best is UNKNOWN or as_int > best:  # type: ignore[operator]
            best = as_int
    return best


_INCH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\"|''|inch|inches|in\b)", re.IGNORECASE)
_CM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:cm|cms|centimet(?:re|er)s?)\b", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def parse_display_inches(text: str | None) -> Decimal | Unknown:
    """Display diagonal in inches.

    Handles every shape observed in M0: a bare number from Croma's labelled field
    (``15.6``), an inch token (``15.6 Inch``), a centimetre token (``39.62 cm``), and
    the dual-unit forms Reliance Digital and Vijay Sales publish
    (``39.62 cm - 15.6 inch``, ``15.6 Inch (39.6 cm)``).

    Inches win when both units are present — they are the figure retailers quote
    deliberately, whereas the centimetre value is a rounded conversion.
    """
    if text is None:
        return UNKNOWN
    raw = str(text).strip()
    if not raw:
        return UNKNOWN

    inch_match = _INCH_RE.search(raw)
    if inch_match:
        return _decimal(inch_match.group(1))

    cm_match = _CM_RE.search(raw)
    if cm_match:
        centimetres = _decimal(cm_match.group(1))
        if centimetres is UNKNOWN:
            return UNKNOWN
        assert isinstance(centimetres, Decimal)
        return (centimetres / _CM_PER_INCH).quantize(Decimal("0.001"))

    bare = _BARE_NUMBER_RE.match(raw)
    if bare:
        return _decimal(bare.group(1))
    return UNKNOWN


def display_sizes_equal(
    a: Decimal | Unknown, b: Decimal | Unknown, tolerance: Decimal = DISPLAY_SIZE_TOLERANCE_IN
) -> bool | Unknown:
    """Tolerant display-size comparison. UNKNOWN if either side is unknown."""
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    assert isinstance(a, Decimal) and isinstance(b, Decimal)
    return abs(a - b) <= tolerance


_HZ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*hz\b", re.IGNORECASE)


def parse_refresh_hz(text: str | None) -> int | Unknown:
    """Refresh rate in Hz. Accepts ``"144"`` (Croma's labelled field) or ``"144Hz"``."""
    if text is None:
        return UNKNOWN
    raw = str(text).strip()
    if not raw:
        return UNKNOWN
    match = _HZ_RE.search(raw)
    if match:
        value = _decimal(match.group(1))
        return int(value) if isinstance(value, Decimal) else UNKNOWN
    bare = _BARE_NUMBER_RE.match(raw)
    if bare:
        value = _decimal(bare.group(1))
        return int(value) if isinstance(value, Decimal) else UNKNOWN
    return UNKNOWN


# ------------------------------------------------------------------------- gpu

_GPU_BRANDS = ("NVIDIA", "AMD", "INTEL")
#: ``([A-Z]*)`` at the end is deliberate and load-bearing: it captures the variant
#: suffix in ``RTX 3050A`` so it can be *preserved*, not discarded.
_GPU_MODEL_RE = re.compile(
    r"\b(RTX|GTX|GT|RX|ARC)\s*(\d{3,4})\s*([A-Z]{0,3})\b", re.IGNORECASE
)


def normalize_gpu_model(text: str | None) -> str | Unknown:
    """Canonical GPU model, **variant suffix preserved**.

    ``"NVIDIA GeForce RTX 3050"`` → ``"RTX 3050"``
    ``"NVIDIA GeForce RTX 3050A 4GB GDDR6"`` → ``"RTX 3050A"``

    The ``A`` denotes a distinct lower-TDP part. M0 §9 finding 3 records it as a real
    product difference, so stripping it would merge two different laptops — the
    never-strip invariant applying to GPUs rather than to MTMs.
    """
    if not text:
        return UNKNOWN
    match = _GPU_MODEL_RE.search(str(text))
    if not match:
        return UNKNOWN
    family, number, suffix = match.group(1).upper(), match.group(2), match.group(3).upper()
    return f"{family} {number}{suffix}"


def parse_gpu_brand(text: str | None) -> str | Unknown:
    if not text:
        return UNKNOWN
    upper = str(text).upper()
    for brand in _GPU_BRANDS:
        if brand in upper:
            return brand
    return UNKNOWN


# ------------------------------------------------------------------------- cpu

#: Intel ``13450HX`` / ``12450HX``, AMD ``7535HS`` / ``7435HS`` / ``5600H``.
_CPU_MODEL_RE = re.compile(r"\b(\d{4,5}[A-Z]{0,3})\b")
_CPU_BRANDS = {"AMD": "AMD", "INTEL": "INTEL"}
_CPU_FAMILIES = (
    "RYZEN 9", "RYZEN 7", "RYZEN 5", "RYZEN 3",
    "CORE ULTRA 9", "CORE ULTRA 7", "CORE ULTRA 5",
    "CORE I9", "CORE I7", "CORE I5", "CORE I3",
)
_CORES_RE = re.compile(r"\b(\d+)\s*C\s*/\s*(\d+)\s*T\b", re.IGNORECASE)
_CORES_WORD_RE = re.compile(r"\b(\d+)[\s-]*cores?\b", re.IGNORECASE)


def normalize_cpu_model(text: str | None) -> str | Unknown:
    """CPU model number. ``"AMD Ryzen 5 7535HS"`` → ``"7535HS"``; ``"7535HS"`` → itself.

    Suffixes are preserved for the same reason as GPUs: ``13450HX`` and ``13450H``
    are different parts.
    """
    if not text:
        return UNKNOWN
    candidates = _CPU_MODEL_RE.findall(str(text).upper())
    if not candidates:
        return UNKNOWN
    # Prefer a token carrying a letter suffix — that is the model, not a clock or year.
    for token in candidates:
        if any(ch.isalpha() for ch in token):
            return token
    return candidates[0]


def parse_cpu_family(text: str | None) -> str | Unknown:
    if not text:
        return UNKNOWN
    upper = re.sub(r"\s+", " ", str(text).upper())
    for family in _CPU_FAMILIES:
        if family in upper:
            return family
    return UNKNOWN


def parse_cpu_brand(text: str | None) -> str | Unknown:
    if not text:
        return UNKNOWN
    upper = str(text).upper()
    for token, brand in _CPU_BRANDS.items():
        if token in upper:
            return brand
    if "RYZEN" in upper:
        return "AMD"
    if "CORE I" in upper or "CORE ULTRA" in upper:
        return "INTEL"
    return UNKNOWN


# -------------------------------------------------------------- compound rows


def split_processor_row(value: str) -> dict[SpecKey, str]:
    """``"AMD Ryzen 7 7435HS (8C/16T, 3.1/4.5GHz, 4MB L2/16MB L3)"`` → four specs.

    The exact row shape M0 §6.6 recorded verbatim from Vijay Sales.
    """
    out: dict[SpecKey, str] = {}
    for key, parsed in (
        (SpecKey.CPU_BRAND, parse_cpu_brand(value)),
        (SpecKey.CPU_FAMILY, parse_cpu_family(value)),
        (SpecKey.CPU_MODEL, normalize_cpu_model(value)),
    ):
        if parsed is not UNKNOWN:
            out[key] = str(parsed)
    cores = _CORES_RE.search(value or "")
    if cores:
        out[SpecKey.CPU_CORES] = cores.group(1)
        out[SpecKey.CPU_THREADS] = cores.group(2)
    else:
        word = _CORES_WORD_RE.search(value or "")
        if word:
            out[SpecKey.CPU_CORES] = word.group(1)
    return out


_RESOLUTION_RE = re.compile(r"\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", re.IGNORECASE)
_PANEL_TOKENS = ("IPS", "OLED", "TN", "VA", "LED", "LCD")


def split_display_row(value: str) -> dict[SpecKey, str]:
    """``'15.6" FHD (1920x1080) IPS, 300nits, 144Hz, G-SYNC'`` → four specs."""
    out: dict[SpecKey, str] = {}
    size = parse_display_inches(value)
    if size is not UNKNOWN:
        out[SpecKey.DISPLAY_SIZE_IN] = str(size)
    refresh = parse_refresh_hz(value)
    if refresh is not UNKNOWN:
        out[SpecKey.DISPLAY_REFRESH_HZ] = str(refresh)
    resolution = _RESOLUTION_RE.search(value or "")
    if resolution:
        out[SpecKey.DISPLAY_RESOLUTION] = f"{resolution.group(1)}x{resolution.group(2)}"
    upper = (value or "").upper()
    for token in _PANEL_TOKENS:
        if re.search(rf"\b{token}\b", upper):
            out[SpecKey.DISPLAY_PANEL] = token
            break
    return out


#: ``(?:LP)?`` — an optional *group*, not ``LP?``. The latter reads as "L, then an
#: optional P" and so requires the L, silently matching LPDDR5 while missing plain
#: DDR5 — the more common value of the two.
_RAM_TYPE_RE = re.compile(r"\b((?:LP)?DDR\d[A-Z0-9]*)\b", re.IGNORECASE)
_FORM_FACTOR_RE = re.compile(r"\b(SO-?DIMM|DIMM|SOLDERED|ON-?BOARD)\b", re.IGNORECASE)


def split_memory_row(value: str) -> dict[SpecKey, str]:
    """``"24GB SO-DIMM DDR5-4800"`` → capacity, form factor, type."""
    out: dict[SpecKey, str] = {}
    capacity = parse_capacity_gb(value)
    if capacity is not UNKNOWN:
        out[SpecKey.RAM_GB] = str(capacity)
    ram_type = _RAM_TYPE_RE.search(value or "")
    if ram_type:
        out[SpecKey.RAM_TYPE] = ram_type.group(1).upper()
    form = _FORM_FACTOR_RE.search(value or "")
    if form:
        out[SpecKey.RAM_FORM_FACTOR] = form.group(1).upper().replace("SODIMM", "SO-DIMM")
    return out


_STORAGE_TYPE_RE = re.compile(r"\b(SSD|HDD|EMMC|UFS)\b", re.IGNORECASE)


def split_storage_row(value: str) -> dict[SpecKey, str]:
    """``"512GB SSD M.2 PCIe 4.0x4 NVMe"`` → capacity and type."""
    out: dict[SpecKey, str] = {}
    capacity = parse_capacity_gb(value)
    if capacity is not UNKNOWN:
        out[SpecKey.STORAGE_GB] = str(capacity)
    storage_type = _STORAGE_TYPE_RE.search(value or "")
    if storage_type:
        out[SpecKey.STORAGE_TYPE] = storage_type.group(1).upper()
    return out


def split_graphics_row(value: str) -> dict[SpecKey, str]:
    """``"NVIDIA GeForce RTX 3050A 4GB GDDR6"`` → brand, model (suffix kept), VRAM."""
    out: dict[SpecKey, str] = {}
    brand = parse_gpu_brand(value)
    if brand is not UNKNOWN:
        out[SpecKey.GPU_BRAND] = str(brand)
    model = normalize_gpu_model(value)
    if model is not UNKNOWN:
        out[SpecKey.GPU_MODEL] = str(model)
    vram = parse_capacity_gb(value)
    if vram is not UNKNOWN:
        out[SpecKey.GPU_VRAM_GB] = str(vram)
    return out


#: Row-label prefixes → splitter. Matched case-insensitively on a normalised label,
#: longest first, so ``Graphics Card`` and ``Graphics`` both route correctly.
_ROW_SPLITTERS: tuple[tuple[str, object], ...] = (
    ("processor", split_processor_row),
    ("cpu", split_processor_row),
    ("display", split_display_row),
    ("screen", split_display_row),
    ("memory", split_memory_row),
    ("ram", split_memory_row),
    ("storage", split_storage_row),
    ("hard drive", split_storage_row),
    ("graphics", split_graphics_row),
    ("gpu", split_graphics_row),
)


def split_compound_row(label: str, value: str) -> dict[SpecKey, str]:
    """Split one labelled row into canonical specs.

    Returns ``{}`` for a row this stage does not understand — an unrecognised row is
    not an error, it simply contributes nothing. Silence beats invention.
    """
    normalized_label = re.sub(r"\s+", " ", (label or "")).strip().lower()
    if not normalized_label or not value:
        return {}
    for prefix, splitter in sorted(_ROW_SPLITTERS, key=lambda p: -len(p[0])):
        if normalized_label.startswith(prefix):
            return splitter(value)  # type: ignore[operator]
    return {}


__all__ = [
    "CRITICAL_SPECS",
    "DISPLAY_SIZE_TOLERANCE_IN",
    "SpecKey",
    "display_sizes_equal",
    "normalize_cpu_model",
    "normalize_gpu_model",
    "parse_capacity_gb",
    "parse_display_inches",
    "parse_refresh_hz",
    "split_compound_row",
    "split_display_row",
    "split_graphics_row",
    "split_memory_row",
    "split_processor_row",
    "split_storage_row",
]
