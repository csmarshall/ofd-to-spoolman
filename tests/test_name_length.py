"""Spoolman caps filament names at 64 characters (#30). Semantics mirror the OFD fork's shorten_long_names / shorten_name."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import ofd2spoolman
from ofd2spoolman import ELLIPSIS, NAME_MAX_LENGTH, _digest_tagged, _elision_candidates, shorten_long_names, shorten_name

ROSA_NAME = "Impact Abrasive Uv H2O Microbe Resistant ROSA TPU HardTech+ 83D Neon Yellow"


def named(record_id: str, name: str, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"id": record_id, "manufacturer": "Acme", "material": "PLA", "weight": 1000.0, "diameter": 1.75, "name": name}
    record.update(overrides)
    return record


def test_name_limit_is_spoolmans() -> None:
    assert NAME_MAX_LENGTH == 64


def test_short_names_untouched() -> None:
    exact = "x" * NAME_MAX_LENGTH
    assert shorten_name(exact) == exact
    records = [named("a", exact), named("b", "Red")]
    assert shorten_long_names(records) == []
    assert records[0]["name"] == exact


def test_exact_64_boundary() -> None:
    name = " ".join(["word"] * 13)
    assert len(name) == 64
    assert shorten_name(name) == name
    over = name + "s"
    assert len(over) == 65
    short = shorten_name(over)
    assert len(short) <= NAME_MAX_LENGTH and short != over


def test_trailing_parenthetical_dropped_when_it_fits_and_is_unique() -> None:
    long_name = "PLA Silk High-Speed Quadruple Twilight Glow (Black & Red & Dark Purple & Gold)"
    records = [named("a", long_name)]
    changes = shorten_long_names(records)
    assert records[0]["name"] == "PLA Silk High-Speed Quadruple Twilight Glow"
    assert changes == [("a", long_name, "PLA Silk High-Speed Quadruple Twilight Glow", "parenthetical")]


def test_parenthetical_not_dropped_when_it_would_collide() -> None:
    base = "PLA Matte High Speed Gradient Multi-Color"
    one = f"{base} (coffee & khaki & white & dark brown & yellow & light brown)"
    two = f"{base} (dark brown & yellow & light brown & coffee & khaki & white)"
    records = [named("a", one), named("b", two)]
    shorten_long_names(records)
    assert records[0]["name"] != records[1]["name"]
    assert all(len(r["name"]) <= NAME_MAX_LENGTH for r in records)
    assert all("…" in r["name"] for r in records)


def test_parenthetical_not_dropped_when_it_would_collide_with_a_short_record() -> None:
    long_name = "PLA Rainbow Long Product Line Name Gradient Multi-Colour Super (a & b & c & d)"
    stripped = "PLA Rainbow Long Product Line Name Gradient Multi-Colour Super"
    records = [named("a", long_name), named("b", stripped)]
    shorten_long_names(records)
    assert records[1]["name"] == stripped
    assert records[0]["name"] != stripped


def test_parenthetical_not_dropped_when_it_would_collide_with_a_fixed_record() -> None:
    """A SpoolmanDB entry is never altered, but it still occupies its name."""
    long_name = "PLA Rainbow Long Product Line Name Gradient Multi-Colour Super (a & b & c & d)"
    stripped = "PLA Rainbow Long Product Line Name Gradient Multi-Colour Super"
    fixed = [named("sdb", stripped)]
    records = [named("a", long_name)]
    shorten_long_names(records, fixed=fixed)
    assert fixed[0]["name"] == stripped
    assert records[0]["name"] != stripped


def test_collision_is_judged_per_weight_and_diameter() -> None:
    long_name = "PLA Rainbow Long Product Line Name Gradient Multi-Colour Super (a & b & c & d)"
    stripped = "PLA Rainbow Long Product Line Name Gradient Multi-Colour Super"
    records = [named("a", long_name), named("b", stripped, weight=500.0)]
    shorten_long_names(records)
    assert records[0]["name"] == stripped


def test_middle_elision_keeps_start_and_trailing_colour() -> None:
    assert len(ROSA_NAME) > NAME_MAX_LENGTH
    short = shorten_name(ROSA_NAME)
    assert len(short) <= NAME_MAX_LENGTH
    assert short.startswith("Impact Abrasive")
    assert short.endswith("Neon Yellow")
    assert short.count("…") == 1 and "… " in short


def test_elision_is_idempotent_and_deterministic() -> None:
    once = shorten_name(ROSA_NAME)
    assert shorten_name(once) == once
    assert shorten_name(ROSA_NAME) == once
    records = [named("a", ROSA_NAME)]
    shorten_long_names(records)
    assert shorten_long_names(records) == []  # second pass changes nothing


def test_elision_of_unbroken_text_still_fits() -> None:
    short = shorten_name("x" * 200)
    assert len(short) <= NAME_MAX_LENGTH and "…" in short


def colour_variants() -> list[str]:
    """Names identical at both ends, differing only in the middle, so a plain elision gives them the same text."""
    return [f"Impact Abrasive Uv H2O Microbe Resistant ROSA {mid} HardTech+ 83D Neon Yellow" for mid in ("TPU", "PLA", "ABS")]


def test_elision_collision_is_made_unique_deterministically() -> None:
    names = colour_variants()
    assert len({shorten_name(n) for n in names}) == 1  # the premise: a plain elision collides
    records = [named(str(i), n) for i, n in enumerate(names)]
    shorten_long_names(records)
    result = [r["name"] for r in records]
    assert len(set(result)) == len(result)
    assert all(len(n) <= NAME_MAX_LENGTH and "…" in n for n in result)
    reordered = [named(str(i), n) for i, n in reversed(list(enumerate(names)))]
    shorten_long_names(reordered)
    assert {r["id"]: r["name"] for r in reordered} == {r["id"]: r["name"] for r in records}


def test_elision_collision_with_a_short_record_is_avoided() -> None:
    long_name = ROSA_NAME
    taken = shorten_name(long_name)
    records = [named("a", long_name), named("b", taken)]
    shorten_long_names(records)
    assert records[1]["name"] == taken
    assert records[0]["name"] != taken and len(records[0]["name"]) <= NAME_MAX_LENGTH


def test_elision_collision_across_different_weights_is_not_a_collision() -> None:
    records = [named("a", ROSA_NAME), named("b", ROSA_NAME, weight=500.0)]
    shorten_long_names(records)
    assert records[0]["name"] == records[1]["name"] == shorten_name(ROSA_NAME)


def test_collision_fallback_still_unique_when_every_cut_collides() -> None:
    """Names differing only deep inside one over-long word: no cut reveals it, so the digest-tagged last resort must stay unique."""
    names = [f"{'a' * 70}{c}{'b' * 10} Red" for c in "ABCD"]
    assert len({shorten_name(n) for n in names}) == 1
    records = [named(str(i), n) for i, n in enumerate(names)]
    shorten_long_names(records)
    result = [r["name"] for r in records]
    assert len(set(result)) == len(result)
    assert all(len(n) <= NAME_MAX_LENGTH for n in result)
    assert any("~" in n for n in result)
    assert shorten_name(names[0]) in result  # the first in sorted order keeps the plain elision


def test_fixed_record_holding_the_plain_elision_forces_a_shifted_cut() -> None:
    plain = shorten_name(ROSA_NAME)
    fixed = [named("sdb", plain)]
    records = [named("a", ROSA_NAME)]
    shorten_long_names(records, fixed=fixed)
    result = records[0]["name"]
    assert result != plain and len(result) <= NAME_MAX_LENGTH
    assert "~" not in result  # a shifted cut was available, so no digest
    assert fixed[0]["name"] == plain


def test_every_candidate_taken_by_fixed_records_gives_the_digest_tag() -> None:
    fixed = [named(f"sdb{i}", c) for i, c in enumerate(_elision_candidates(ROSA_NAME))]
    assert len(fixed) > 1
    records = [named("a", ROSA_NAME)]
    shorten_long_names(records, fixed=fixed)
    assert records[0]["name"] == _digest_tagged(ROSA_NAME, 4)
    assert len(records[0]["name"]) <= NAME_MAX_LENGTH


def test_digest_length_grows_until_the_tag_is_free() -> None:
    taken = [*_elision_candidates(ROSA_NAME), _digest_tagged(ROSA_NAME, 4), _digest_tagged(ROSA_NAME, 5)]
    fixed = [named(f"sdb{i}", c) for i, c in enumerate(taken)]
    records = [named("a", ROSA_NAME)]
    shorten_long_names(records, fixed=fixed)
    assert records[0]["name"] == _digest_tagged(ROSA_NAME, 6)
    assert len(records[0]["name"]) <= NAME_MAX_LENGTH


def test_shifted_cuts_always_keep_a_tail_of_whole_words() -> None:
    name = f"{'a' * 70}A{'b' * 10} Red"
    candidates = _elision_candidates(name)
    assert candidates[0] == shorten_name(name)
    words = name.split()
    whole_tails = {" ".join(words[-k:]) for k in range(1, len(words) + 1)}
    for candidate in candidates[1:]:
        assert candidate.rsplit(ELLIPSIS + " ", 1)[1] in whole_tails
    # A character-cut tail such as "… d" would otherwise sneak in for this single over-long word.
    assert all(not c.endswith("… d") for c in candidates)


def test_refill_marker_is_never_dropped_as_a_parenthetical() -> None:
    name = "PLA Silk High-Speed Quadruple Twilight Gorgeous Harmony Red (refill)"
    assert len(name) > NAME_MAX_LENGTH
    records = [named("a", name)]
    changes = shorten_long_names(records)
    assert changes[0][3] == "elided"
    assert records[0]["name"].endswith("(refill)") and len(records[0]["name"]) <= NAME_MAX_LENGTH


def test_logs_count_and_examples(caplog: pytest.LogCaptureFixture) -> None:
    records = [named("a", ROSA_NAME), named("b", "Red")]
    with caplog.at_level(logging.INFO):
        ofd2spoolman.shorten_ofd_names(records, fixed=[])
    assert "shortened 1 name" in caplog.text
    assert ROSA_NAME in caplog.text


def test_spoolmandb_over_64_warns_and_is_not_altered(
    run: Callable[..., int], sources: dict[str, bytes], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = ofd2spoolman.SPOOLMANDB_URL + "filaments.json"
    sdb = json.loads(sources[url])
    sdb[0]["name"] = "S" * 70
    sources[url] = json.dumps(sdb).encode()
    with caplog.at_level(logging.WARNING):
        assert run(tmp_path) == 0
    out = json.loads((tmp_path / "filaments.json").read_text())
    assert {f["id"]: f for f in out}[sdb[0]["id"]]["name"] == "S" * 70  # output is sorted by id, so find it by id
    assert "SpoolmanDB" in caplog.text and "64" in caplog.text


def test_ofd_long_names_shortened_in_output(run: Callable[..., int], sources: dict[str, bytes], tmp_path: Path) -> None:
    ofd = json.loads(sources[ofd2spoolman.OFD_URL])
    for variant in ofd["variants"]:
        variant["name"] = "Neon Yellow " + "Z" * 70 + " End"
    sources[ofd2spoolman.OFD_URL] = json.dumps(ofd).encode()
    assert run(tmp_path) == 0
    out = json.loads((tmp_path / "filaments.json").read_text())
    ofd_entries = [f for f in out if f["id"].startswith("ofd_")]
    assert ofd_entries
    assert all(len(f["name"]) <= NAME_MAX_LENGTH for f in out)
