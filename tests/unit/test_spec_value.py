"""Specification normalization and compound-row splitting.

Every compound row asserted here is quoted verbatim from M0 §6.6 — real Vijay Sales
spec rows — or from M2's real Croma capture. The two shapes must reduce to the same
canonical specs, which is the entire justification for scope change S1.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from croma_intel.domain.product import UNKNOWN
from croma_intel.normalization.spec_value import (
    CRITICAL_SPECS,
    DISPLAY_SIZE_TOLERANCE_IN,
    SpecKey,
    display_sizes_equal,
    normalize_cpu_model,
    normalize_gpu_model,
    parse_capacity_gb,
    parse_display_inches,
    parse_refresh_hz,
    split_compound_row,
)


class TestCapacity(unittest.TestCase):
    def test_gigabytes(self):
        self.assertEqual(parse_capacity_gb("512GB"), 512)
        self.assertEqual(parse_capacity_gb("16 GB"), 16)
        self.assertEqual(parse_capacity_gb("24GB"), 24)

    def test_terabytes_convert(self):
        self.assertEqual(parse_capacity_gb("1 TB"), 1024)
        self.assertEqual(parse_capacity_gb("2TB SSD"), 2048)

    def test_largest_capacity_wins(self):
        """Storage rows mention cache and secondary figures alongside the headline."""
        self.assertEqual(parse_capacity_gb("512GB SSD + 8GB cache"), 512)

    def test_absent_is_unknown_not_zero(self):
        self.assertIs(parse_capacity_gb("SSD"), UNKNOWN)
        self.assertIs(parse_capacity_gb(""), UNKNOWN)
        self.assertIs(parse_capacity_gb(None), UNKNOWN)


class TestDisplaySize(unittest.TestCase):
    def test_croma_bare_number(self):
        self.assertEqual(parse_display_inches("15.6"), Decimal("15.6"))

    def test_inch_token(self):
        self.assertEqual(parse_display_inches("15.6 Inch"), Decimal("15.6"))
        self.assertEqual(parse_display_inches('15.6"'), Decimal("15.6"))

    def test_centimetres_convert(self):
        """Croma publishes 39.62 cm as a separate labelled field."""
        self.assertEqual(parse_display_inches("39.62 cm"), Decimal("15.598"))

    def test_dual_unit_reliance_digital_shape(self):
        """'39.62 cm - 15.6 inch' — inches win, they are the deliberate figure."""
        self.assertEqual(parse_display_inches("39.62 cm - 15.6 inch"), Decimal("15.6"))

    def test_dual_unit_vijay_sales_shape(self):
        self.assertEqual(parse_display_inches("15.6 Inch (39.6 cm)"), Decimal("15.6"))

    def test_absent_is_unknown(self):
        self.assertIs(parse_display_inches("FHD IPS"), UNKNOWN)
        self.assertIs(parse_display_inches(None), UNKNOWN)


class TestDisplayTolerance(unittest.TestCase):
    """M0 §9 finding 2: the same panel published two ways."""

    def test_the_rounding_pair_compares_equal(self):
        a = parse_display_inches("39.62 cm")   # 15.598
        b = parse_display_inches("39.6 cm")    # 15.590
        self.assertIs(display_sizes_equal(a, b), True)

    def test_an_exact_comparator_would_have_failed(self):
        self.assertNotEqual(parse_display_inches("39.62 cm"), parse_display_inches("39.6 cm"))

    def test_a_real_size_difference_is_still_a_mismatch(self):
        self.assertIs(
            display_sizes_equal(Decimal("15.6"), Decimal("16.0")), False
        )

    def test_tolerance_is_tight_enough_to_separate_panel_sizes(self):
        self.assertLess(DISPLAY_SIZE_TOLERANCE_IN, Decimal("0.4"))

    def test_unknown_propagates(self):
        self.assertIs(display_sizes_equal(UNKNOWN, Decimal("15.6")), UNKNOWN)
        self.assertIs(display_sizes_equal(Decimal("15.6"), UNKNOWN), UNKNOWN)


class TestRefreshRate(unittest.TestCase):
    def test_croma_bare_number(self):
        self.assertEqual(parse_refresh_hz("144"), 144)

    def test_with_unit(self):
        self.assertEqual(parse_refresh_hz("144Hz"), 144)
        self.assertEqual(parse_refresh_hz("165 Hz"), 165)

    def test_absent_is_unknown(self):
        self.assertIs(parse_refresh_hz("IPS"), UNKNOWN)


class TestGpuNeverStripsVariantSuffix(unittest.TestCase):
    """The never-strip invariant, applied to GPUs (M0 §9 finding 3)."""

    def test_plain_model(self):
        self.assertEqual(normalize_gpu_model("NVIDIA GeForce RTX 3050"), "RTX 3050")

    def test_variant_suffix_is_preserved(self):
        self.assertEqual(
            normalize_gpu_model("NVIDIA GeForce RTX 3050A 4GB GDDR6"), "RTX 3050A"
        )

    def test_the_two_do_not_compare_equal(self):
        """3050A is a distinct lower-TDP part. Merging them merges two products."""
        self.assertNotEqual(
            normalize_gpu_model("NVIDIA GeForce RTX 3050A"),
            normalize_gpu_model("NVIDIA GeForce RTX 3050"),
        )

    def test_other_families(self):
        self.assertEqual(normalize_gpu_model("NVIDIA GeForce RTX 4050"), "RTX 4050")
        self.assertEqual(normalize_gpu_model("AMD Radeon RX 6600"), "RX 6600")

    def test_absent_is_unknown(self):
        self.assertIs(normalize_gpu_model("Integrated Graphics"), UNKNOWN)


class TestCpuModel(unittest.TestCase):
    def test_extracts_from_a_full_name(self):
        self.assertEqual(normalize_cpu_model("AMD Ryzen 5 7535HS"), "7535HS")
        self.assertEqual(normalize_cpu_model("Intel Core i5-13450HX"), "13450HX")

    def test_croma_already_split_value_passes_through(self):
        self.assertEqual(normalize_cpu_model("7535HS"), "7535HS")

    def test_suffix_is_preserved(self):
        self.assertEqual(normalize_cpu_model("13450HX"), "13450HX")
        self.assertNotEqual(normalize_cpu_model("13450HX"), normalize_cpu_model("13450H"))

    def test_absent_is_unknown(self):
        self.assertIs(normalize_cpu_model("AMD Ryzen"), UNKNOWN)


class TestCompoundRowSplitting(unittest.TestCase):
    """The rows M0 §6.6 recorded verbatim from Vijay Sales."""

    def test_processor_row_yields_four_specs(self):
        out = split_compound_row(
            "Processor", "AMD Ryzen 7 7435HS (8C/16T, 3.1/4.5GHz, 4MB L2/16MB L3)"
        )
        self.assertEqual(out[SpecKey.CPU_BRAND], "AMD")
        self.assertEqual(out[SpecKey.CPU_FAMILY], "RYZEN 7")
        self.assertEqual(out[SpecKey.CPU_MODEL], "7435HS")
        self.assertEqual(out[SpecKey.CPU_CORES], "8")
        self.assertEqual(out[SpecKey.CPU_THREADS], "16")

    def test_display_row(self):
        out = split_compound_row(
            "Display", '15.6" FHD (1920x1080) IPS, 300nits, 144Hz, G-SYNC'
        )
        self.assertEqual(out[SpecKey.DISPLAY_SIZE_IN], "15.6")
        self.assertEqual(out[SpecKey.DISPLAY_REFRESH_HZ], "144")
        self.assertEqual(out[SpecKey.DISPLAY_RESOLUTION], "1920x1080")
        self.assertEqual(out[SpecKey.DISPLAY_PANEL], "IPS")

    def test_memory_row(self):
        out = split_compound_row("RAM", "24GB SO-DIMM DDR5-4800")
        self.assertEqual(out[SpecKey.RAM_GB], "24")
        self.assertEqual(out[SpecKey.RAM_TYPE], "DDR5")
        self.assertEqual(out[SpecKey.RAM_FORM_FACTOR], "SO-DIMM")

    def test_storage_row(self):
        out = split_compound_row("Storage", "512GB SSD M.2 PCIe 4.0x4 NVMe")
        self.assertEqual(out[SpecKey.STORAGE_GB], "512")
        self.assertEqual(out[SpecKey.STORAGE_TYPE], "SSD")

    def test_graphics_row_keeps_the_variant_suffix(self):
        out = split_compound_row("Graphics", "NVIDIA GeForce RTX 3050A 4GB GDDR6")
        self.assertEqual(out[SpecKey.GPU_BRAND], "NVIDIA")
        self.assertEqual(out[SpecKey.GPU_MODEL], "RTX 3050A")
        self.assertEqual(out[SpecKey.GPU_VRAM_GB], "4")

    def test_unrecognised_row_contributes_nothing(self):
        self.assertEqual(split_compound_row("Warranty Type", "Carry-In"), {})

    def test_empty_inputs(self):
        self.assertEqual(split_compound_row("", "x"), {})
        self.assertEqual(split_compound_row("Processor", ""), {})


class TestCromaAndVijaySalesReduceToTheSameSpecs(unittest.TestCase):
    """The justification for S1, stated as a test.

    Croma pre-splits its specification table; Vijay Sales ships compound rows. If
    normalization works, both reduce to identical canonical values for the same part.
    """

    def test_processor_agrees_across_platforms(self):
        croma = normalize_cpu_model("7435HS")  # Croma "Processor Variant"
        vijay = split_compound_row(
            "Processor", "AMD Ryzen 7 7435HS (8C/16T, 3.1/4.5GHz, 4MB L2/16MB L3)"
        )[SpecKey.CPU_MODEL]
        self.assertEqual(croma, vijay)

    def test_memory_agrees_across_platforms(self):
        croma = parse_capacity_gb("24GB")  # Croma "RAM"
        vijay = int(split_compound_row("RAM", "24GB SO-DIMM DDR5-4800")[SpecKey.RAM_GB])
        self.assertEqual(croma, vijay)

    def test_display_agrees_across_platforms_within_tolerance(self):
        croma = parse_display_inches("15.6")            # Croma labelled inches
        vijay = parse_display_inches("15.6 Inch (39.6 cm)")
        self.assertIs(display_sizes_equal(croma, vijay), True)

    def test_gpu_agrees_across_platforms(self):
        croma = normalize_gpu_model("NVIDIA GeForce RTX 3050")
        vijay = split_compound_row("Graphics", "NVIDIA GeForce RTX 3050 6GB GDDR6")[
            SpecKey.GPU_MODEL
        ]
        self.assertEqual(croma, vijay)


class TestCriticalSpecSet(unittest.TestCase):
    def test_the_critical_set_is_the_documented_eight(self):
        self.assertEqual(len(CRITICAL_SPECS), 8)
        for key in (
            SpecKey.CPU_MODEL, SpecKey.RAM_GB, SpecKey.STORAGE_GB, SpecKey.GPU_MODEL,
            SpecKey.GPU_VRAM_GB, SpecKey.DISPLAY_SIZE_IN, SpecKey.DISPLAY_REFRESH_HZ,
            SpecKey.OS,
        ):
            self.assertIn(key, CRITICAL_SPECS)

    def test_price_is_not_a_critical_spec(self):
        """M0 §16: price is never an identity signal."""
        joined = " ".join(k.value for k in CRITICAL_SPECS)
        for banned in ("price", "mop", "mrp", "discount"):
            self.assertNotIn(banned, joined)


if __name__ == "__main__":
    unittest.main()
