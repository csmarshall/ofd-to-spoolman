"""Tests for the LEGACY pre-ADR-0009 OFD all.json mapping that tools/conflict_report.py still uses. No network."""

from __future__ import annotations

import copy
import logging
from typing import Any

import pytest

from tools import legacy_ofd_convert as legacy
from tools.legacy_ofd_convert import display_name, pick_temp


def by_name(entries: list[dict[str, Any]], manufacturer: str, name: str) -> dict[str, Any]:
    (match,) = [e for e in entries if e["manufacturer"] == manufacturer and e["name"] == name]
    return match


class TestDisplayName:
    def test_brand_prefix_stripped_and_bare_material_dropped(self) -> None:
        assert display_name("SUNLU PLA", "SUNLU", "PLA", "Black") == "Black"

    def test_brand_prefix_with_separator_stripped(self) -> None:
        assert display_name("Acme - Silk PLA", "Acme", "PLA", "Gold") == "Silk PLA Gold"

    def test_product_line_kept(self) -> None:
        assert display_name("PLA Matte", "Acme", "PLA", "Red") == "PLA Matte Red"

    def test_petg_plus_survives(self) -> None:
        """Regression: norm() once stripped '+', making 'PETG+' look like bare 'PETG' and dropping it."""
        assert display_name("PETG+", "Acme", "PETG", "Black") == "PETG+ Black"

    def test_brand_that_prefixes_a_product_word_is_not_stripped(self) -> None:
        """Regression #11: 'Acme' must not be cut out of 'Acmeflex'."""
        assert display_name("Acmeflex PLA", "Acme", "PLA", "Red") == "Acmeflex PLA Red"

    def test_brand_with_digits_and_hyphen_stripped(self) -> None:
        assert display_name("123-3D Filament PLA", "123-3D", "PLA", "Red") == "Filament PLA Red"

    def test_brand_with_spaced_hyphen_stripped(self) -> None:
        assert display_name("Brand - PLA", "Brand", "PLA", "Red") == "Red"

    def test_brand_directly_followed_by_hyphen_stripped(self) -> None:
        assert display_name("Brand-PLA", "Brand", "PLA", "Red") == "Red"

    def test_brand_only_name_stripped(self) -> None:
        assert display_name("Brand", "Brand", "PLA", "Red") == "Red"

    def test_brand_underscore_separator_stripped_case_insensitive(self) -> None:
        assert display_name("brand_PLA", "Brand", "PLA", "Red") == "Red"

    def test_bare_material_without_brand_dropped(self) -> None:
        assert display_name("PETG", "Acme", "PETG", "Black") == "Black"


class TestPickTemp:
    def test_midpoint(self) -> None:
        assert pick_temp(190, 220) == 205

    def test_zero_lower_bound_ignored(self) -> None:
        assert pick_temp(0, 60) == 60

    def test_zero_upper_bound_ignored(self) -> None:
        assert pick_temp(50, 0) == 50

    def test_both_missing_is_none(self) -> None:
        assert pick_temp(None, None) is None
        assert pick_temp(0, 0) is None

    def test_single_end(self) -> None:
        assert pick_temp(None, 70) == 70


class TestConvertOfd:
    def test_traits_map_to_spoolman_enums(self, ofd: dict[str, Any]) -> None:
        entries = legacy.convert_ofd(ofd, include_discontinued=False)
        red = by_name(entries, "Acme", "PLA Matte Red")
        assert (red["finish"], red["pattern"]) == ("matte", "sparkle")
        marble = by_name(entries, "Acme", "PLA Matte Marble Grey")
        assert (marble["finish"], marble["pattern"], marble["translucent"]) == (None, "marble", True)
        orange = by_name(entries, "Zeta", "Flex Orange")
        assert (orange["translucent"], orange["glow"]) == (True, True)

    def test_nothing_invented(self, ofd: dict[str, Any]) -> None:
        entries = legacy.convert_ofd(ofd, include_discontinued=False)
        black = by_name(entries, "Acme", "PETG+ Black")
        assert black["spool_weight"] is None
        assert by_name(entries, "Acme", "PLA Matte Mystery")["color_hex"] is None
        assert by_name(entries, "Acme", "PLA Matte Red")["spool_weight"] == 180.0

    def test_refill_marked_and_ids_use_size_uuid(self, ofd: dict[str, Any]) -> None:
        entries = legacy.convert_ofd(ofd, include_discontinued=False)
        refill = by_name(entries, "Zeta", "Flex Orange (refill)")
        assert refill["weight"] == 750.0
        size_uuids = {s["uuid"] for s in ofd["sizes"]}
        assert all(e["id"].removeprefix("ofd_") in size_uuids for e in entries)

    def test_id_is_built_from_uuid_never_from_derived_id(self, ofd: dict[str, Any]) -> None:
        """Regression #28: size["id"] is a derived uuid5 that changes on a rename/GTIN edit; size["uuid"] is the stored canonical one."""
        derived = {s["id"] for s in ofd["sizes"]}
        assert derived.isdisjoint(s["uuid"] for s in ofd["sizes"])  # fixture must tell the two apart
        entries = legacy.convert_ofd(ofd, include_discontinued=True)
        ids = {e["id"] for e in entries}
        assert ids
        assert not {f"ofd_{d}" for d in derived} & ids
        assert ids <= {f"ofd_{s['uuid']}" for s in ofd["sizes"]}

    def test_id_follows_uuid_when_derived_id_changes(self, ofd: dict[str, Any]) -> None:
        before = {e["id"] for e in legacy.convert_ofd(copy.deepcopy(ofd), include_discontinued=True)}
        for size in ofd["sizes"]:
            size["id"] = "ffffffff-" + size["id"][9:]
        after = {e["id"] for e in legacy.convert_ofd(ofd, include_discontinued=True)}
        assert before == after

    def test_sizes_without_uuid_are_skipped_and_counted(self, ofd: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
        baseline = len(legacy.convert_ofd(copy.deepcopy(ofd), include_discontinued=False))
        victim = next(s for s in ofd["sizes"] if s.get("variant_id") and "uuid" in s)
        victim_id = victim["id"]
        del victim["uuid"]
        ofd["sizes"].append({**victim, "uuid": ""})
        with caplog.at_level(logging.INFO):
            entries = legacy.convert_ofd(ofd, include_discontinued=False)
        assert len(entries) <= baseline
        assert all(victim_id not in e["id"] for e in entries)
        assert "missing uuid" in caplog.text

    def test_incomplete_and_dangling_rows_skipped(self, ofd: dict[str, Any]) -> None:
        entries = legacy.convert_ofd(ofd, include_discontinued=True)
        names = {e["name"] for e in entries}
        assert "Mystery" not in names and "Pink" not in names  # filament without density
        assert len(entries) == 14  # 17 sizes - no density - no weight - dangling

    def test_discontinued_filtered_by_default(self, ofd: dict[str, Any]) -> None:
        names = {e["name"] for e in legacy.convert_ofd(ofd, include_discontinued=False)}
        assert "PLA Matte Retired Blue" not in names  # discontinued variant
        assert "Old PLA Green" not in names and "PLA Green" not in names  # discontinued filament
        assert len(names) == 11

    def test_include_discontinued_keeps_them(self, ofd: dict[str, Any]) -> None:
        entries = legacy.convert_ofd(ofd, include_discontinued=True)
        names = {e["name"] for e in entries}
        assert {"PLA Matte Retired Blue", "Old PLA Green"} <= names
        assert len(entries) == 14
