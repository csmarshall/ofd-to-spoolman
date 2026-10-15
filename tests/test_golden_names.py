"""Shared golden test for Spoolman name shortening.

``tests/fixtures/spoolman_golden_names.json`` maps input records to the names expected after
``shorten_long_names``. The same file is asserted by the OFD fork's Spoolman exporter
(csmarshall/open-filament-database, ``tests/fixtures/spoolman_golden_names.json``), so the two
implementations cannot drift apart. Regenerate it from this repo whenever the naming rules change,
and copy it to the fork. Cases that depend on SpoolmanDB ``fixed`` names are excluded, because the
fork has no SpoolmanDB catalog to collide with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import ofd2spoolman

CASES: list[dict[str, Any]] = json.loads((Path(__file__).parent / "fixtures" / "spoolman_golden_names.json").read_text(encoding="utf-8"))


def shortened(records: list[dict[str, Any]]) -> dict[str, str]:
    """Run the shortening on a copy and return id -> resulting name."""
    ofd2spoolman.shorten_long_names(records)
    return {record["id"]: record["name"] for record in records}


@pytest.mark.parametrize("case", CASES, ids=[case["case"] for case in CASES])
def test_matches_golden_fixture(case: dict[str, Any]) -> None:
    assert shortened(json.loads(json.dumps(case["records"]))) == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[case["case"] for case in CASES])
def test_result_does_not_depend_on_record_order(case: dict[str, Any]) -> None:
    assert shortened(json.loads(json.dumps(case["records"]))[::-1]) == case["expected"]


def test_golden_names_fit_and_are_unique_per_key() -> None:
    for case in CASES:
        by_key: dict[tuple[Any, ...], set[str]] = {}
        for record in case["records"]:
            name = case["expected"][record["id"]]
            assert len(name) <= ofd2spoolman.NAME_MAX_LENGTH, case["case"]
            key = (record["manufacturer"], record["material"], record["weight"], record["diameter"])
            assert name not in by_key.setdefault(key, set()), case["case"]
            by_key[key].add(name)
