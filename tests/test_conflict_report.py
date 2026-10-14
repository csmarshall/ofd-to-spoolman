"""Unit tests for tools/conflict_report.py. Fixtures only, no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import fetcher_for

import ofd2spoolman
from tools import conflict_report as cr


def entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "sdb_1",
        "manufacturer": "Acme",
        "name": "Black",
        "material": "PLA",
        "density": 1.24,
        "weight": 1000.0,
        "diameter": 1.75,
        "spool_weight": 200,
        "color_hex": "000000",
        "extruder_temp": 210,
        "bed_temp": 60,
    }
    return base | overrides


def ofd_entry(**overrides: Any) -> dict[str, Any]:
    return entry(id="ofd_1") | overrides


def fields_of(conflicts: list[cr.Conflict]) -> list[str]:
    return [c["field"] for c in conflicts]


class TestFieldDiffs:
    def test_density_difference_beyond_tolerance_is_a_conflict(self) -> None:
        found = cr.find_conflicts([entry()], [ofd_entry(density=1.27)], {})
        assert fields_of(found) == ["density"]
        assert (found[0]["sdb_value"], found[0]["ofd_value"]) == (1.24, 1.27)

    def test_difference_within_tolerance_is_not_a_conflict(self) -> None:
        assert cr.find_conflicts([entry()], [ofd_entry(density=1.245, extruder_temp=215, spool_weight=205)], {}) == []

    def test_each_field_is_detected(self) -> None:
        o = ofd_entry(density=1.5, extruder_temp=250, bed_temp=100, spool_weight=300, color_hex="ff0000")
        assert fields_of(cr.find_conflicts([entry()], [o], {})) == ["density", "extruder_temp", "bed_temp", "spool_weight", "color_hex"]

    def test_missing_value_on_either_side_is_not_a_conflict(self) -> None:
        o = ofd_entry(density=1.5, spool_weight=None, color_hex=None)
        assert fields_of(cr.find_conflicts([entry(spool_weight=None, bed_temp=None)], [o], {})) == ["density"]

    def test_hex_compares_case_and_hash_insensitively_and_tolerates_near_colours(self) -> None:
        assert not cr.hex_conflict("FFA500", "#ffa500")
        assert not cr.hex_conflict("ffa500", "ffa500ff")
        assert not cr.hex_conflict("101010", "181818")
        assert cr.hex_conflict("000000", "202020")

    def test_malformed_or_absent_hex_is_never_a_conflict(self) -> None:
        assert not cr.hex_conflict("not-a-hex", "ffffff")
        assert not cr.hex_conflict("ffffff", None)

    def test_unmatched_products_are_ignored(self) -> None:
        assert cr.find_conflicts([entry()], [ofd_entry(name="Other", density=9.0)], {}) == []

    def test_match_uses_the_converters_dedupe_key(self) -> None:
        # Brand spelling and name punctuation differ but dedupe_key normalises them.
        o = ofd_entry(manufacturer="ACME", name="black!", density=1.5)
        assert ofd2spoolman.dedupe_key(entry()) == ofd2spoolman.dedupe_key(o)
        assert fields_of(cr.find_conflicts([entry()], [o], {})) == ["density"]


class TestSizeConflicts:
    def test_each_side_listing_a_size_the_other_lacks_is_one_size_conflict(self) -> None:
        found = cr.find_conflicts([entry(weight=750.0)], [ofd_entry(weight=1000.0)], {})
        assert fields_of(found) == ["size"]
        assert found[0]["sdb_value"] == "750 g / 1.75 mm"
        assert found[0]["ofd_value"] == "1000 g / 1.75 mm"

    def test_diameter_disagreement_is_a_size_conflict(self) -> None:
        assert fields_of(cr.find_conflicts([entry(diameter=2.85)], [ofd_entry()], {})) == ["size"]

    def test_a_size_only_one_side_lists_is_a_coverage_gap_not_a_conflict(self) -> None:
        sdb = [entry(), entry(id="sdb_2", weight=750.0)]
        assert cr.find_conflicts(sdb, [ofd_entry()], {}) == []

    def test_exact_match_alongside_extra_sizes_reports_no_size_conflict(self) -> None:
        sdb = [entry()]
        ofd = [ofd_entry(), ofd_entry(id="ofd_2", weight=250.0)]
        assert cr.find_conflicts(sdb, ofd, {}) == []


class TestNewVersusPrevious:
    def test_only_conflicts_absent_from_previous_are_new(self) -> None:
        found = cr.find_conflicts([entry()], [ofd_entry(density=1.5, bed_temp=100)], {})
        previous = {cr.conflict_key(found[0])}
        assert fields_of(cr.new_conflicts(found, previous)) == ["bed_temp"]

    def test_a_changed_value_on_a_known_product_counts_as_new(self) -> None:
        before = cr.find_conflicts([entry()], [ofd_entry(density=1.5)], {})
        after = cr.find_conflicts([entry()], [ofd_entry(density=1.6)], {})
        assert cr.new_conflicts(after, {cr.conflict_key(c) for c in before}) == after

    def test_resolved_conflicts_are_simply_not_reported(self) -> None:
        assert cr.new_conflicts([], {"density|a|b|1|2"}) == []

    def test_unreadable_or_missing_previous_state_means_baseline(self, tmp_path: Path) -> None:
        bad = tmp_path / "state.json"
        bad.write_text("not json")
        assert cr.load_previous(None) is None
        assert cr.load_previous(tmp_path / "absent.json") is None
        assert cr.load_previous(bad) is None
        bad.write_text(json.dumps({"keys": ["a"]}))
        assert cr.load_previous(bad) == {"a"}


class TestRendering:
    def many(self, n: int) -> list[cr.Conflict]:
        s = entry(name="N" * 200)
        return [
            cr.make_conflict("density", s, ofd_entry(id=f"ofd_{i}"), 1.0, 2.0, {f"ofd_{i}": "https://example.com/" + "x" * 200})
            for i in range(n)
        ]

    def test_small_digest_is_complete_with_links_and_no_truncation_notice(self) -> None:
        links = {"ofd_1": "https://example.com/ofd"}
        found = cr.find_conflicts([entry()], [ofd_entry(density=1.5)], links)
        body = cr.render_issue_body(found, 1, "2026.10.13", baseline=False)
        assert "https://example.com/ofd" in body
        assert "github.com/search" in body
        assert "### density (1)" in body
        assert "Truncated" not in body

    def test_oversized_digest_stays_under_github_limit_and_says_how_many_were_cut(self) -> None:
        conflicts = self.many(1000)
        body = cr.render_issue_body(conflicts, 1000, "v", baseline=True)
        assert len(body) < cr.GITHUB_BODY_LIMIT
        shown = body.count("\n- **")
        assert 0 < shown < 1000
        assert f"Truncated: {1000 - shown} more conflict(s)" in body
        assert str(cr.GITHUB_BODY_LIMIT) in body

    def test_oversized_comment_is_also_bounded(self) -> None:
        comment = cr.render_new_comment(self.many(1000), "v")
        assert len(comment) < cr.GITHUB_BODY_LIMIT
        assert "Truncated" in comment

    def test_pipes_in_names_are_escaped(self) -> None:
        found = cr.find_conflicts([entry(name="A|B")], [ofd_entry(name="A|B", density=1.5)], {})
        assert "A\\|B" in cr.render_line(found[0])


class TestOfdLinks:
    def test_link_points_at_the_variant_directory_in_the_data_repo(self, ofd: dict[str, Any]) -> None:
        links = cr.build_ofd_links(ofd)
        size = ofd["sizes"][0]
        link = links[f"ofd_{size['id']}"]
        assert link.startswith(cr.OFD_DATA_URL + "/sunlu/PLA/sunlu_pla/")


class TestMain:
    @pytest.fixture
    def payloads(self, sources: dict[str, bytes]) -> dict[str, bytes]:
        sdb = json.loads(sources[ofd2spoolman.SPOOLMANDB_URL + "filaments.json"])
        sdb[0]["density"] = 1.5  # Sunlu Black: conflicts with OFD's 1.24
        return sources | {ofd2spoolman.SPOOLMANDB_URL + "filaments.json": json.dumps(sdb).encode()}

    def test_baseline_run_writes_state_and_body_but_no_comment(self, tmp_path: Path, payloads: dict[str, bytes]) -> None:
        assert cr.main(["--out", str(tmp_path)], fetcher=fetcher_for(payloads)) == 0
        state = json.loads((tmp_path / "state.json").read_text())
        assert len(state["keys"]) == 1 and state["keys"][0].startswith("density|sunlu_pla_black_1000_175_n|")
        assert "Sunlu" in (tmp_path / "issue-body.md").read_text()
        assert not (tmp_path / "new-conflicts.md").exists()
        assert json.loads((tmp_path / "summary.json").read_text()) == {"total": 1, "new": 1, "baseline": True}

    def test_second_run_with_same_data_reports_nothing_new(self, tmp_path: Path, payloads: dict[str, bytes]) -> None:
        first, second = tmp_path / "a", tmp_path / "b"
        cr.main(["--out", str(first)], fetcher=fetcher_for(payloads))
        assert cr.main(["--out", str(second), "--previous", str(first / "state.json")], fetcher=fetcher_for(payloads)) == 0
        assert not (second / "new-conflicts.md").exists()
        assert json.loads((second / "summary.json").read_text())["new"] == 0

    def test_a_conflict_missing_from_previous_is_posted_as_a_comment(self, tmp_path: Path, payloads: dict[str, bytes]) -> None:
        previous = tmp_path / "prev.json"
        previous.write_text(json.dumps({"keys": []}))
        assert cr.main(["--out", str(tmp_path / "o"), "--previous", str(previous)], fetcher=fetcher_for(payloads)) == 0
        assert "1 new conflict(s)" in (tmp_path / "o" / "new-conflicts.md").read_text()

    def test_dry_run_prints_body_and_writes_nothing(
        self, tmp_path: Path, payloads: dict[str, bytes], capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "o"
        assert cr.main(["--dry-run", "--out", str(out)], fetcher=fetcher_for(payloads)) == 0
        assert "## Current conflicts" in capsys.readouterr().out
        assert not out.exists()

    def test_fetch_failure_exits_2_without_writing(self, tmp_path: Path) -> None:
        def boom(url: str) -> bytes:
            raise OSError("down")

        assert cr.main(["--out", str(tmp_path / "o")], fetcher=boom) == 2
        assert not (tmp_path / "o").exists()
