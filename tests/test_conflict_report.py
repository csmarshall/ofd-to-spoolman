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


class TestToleranceBoundaries:
    """Differences exactly at a tolerance are noise (including float noise); just over is a conflict."""

    @pytest.mark.parametrize(
        ("field", "sdb", "ofd", "conflict"),
        [
            ("density", 1.24, 1.25, False),  # 1.25 - 1.24 == 0.010000000000000009 in floats
            ("density", 1.25, 1.24, False),
            ("density", 1.0, 1.01, False),
            ("density", 1.24, 1.251, True),
            ("extruder_temp", 200, 210, False),
            ("extruder_temp", 200, 211, True),
            ("bed_temp", 60, 50, False),
            ("bed_temp", 60, 49, True),
            ("spool_weight", 200, 210, False),
            ("spool_weight", 200.0, 210.0, False),
            ("spool_weight", 200, 211, True),
        ],
    )
    def test_numeric_boundary(self, field: str, sdb: float, ofd: float, conflict: bool) -> None:
        assert cr.field_differs(field, sdb, ofd) is conflict

    @pytest.mark.parametrize(("other", "conflict"), [("0c0c0c", False), ("0d0d0d", True), ("000000", False)])
    def test_hex_channel_boundary(self, other: str, conflict: bool) -> None:
        # HEX_CHANNEL_TOLERANCE is 12 == 0x0c
        assert cr.hex_conflict("000000", other) is conflict

    @pytest.mark.parametrize("junk", ["abc", True, float("nan"), float("inf"), [1], {"a": 1}])
    def test_non_numeric_upstream_values_do_not_crash_or_conflict(self, junk: Any) -> None:
        assert cr.field_differs("density", 1.2, junk) is False


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

    def test_partial_overlap_with_a_shared_size_is_not_a_size_conflict(self) -> None:
        sdb = [entry(), entry(id="sdb_2", weight=750.0)]
        ofd = [ofd_entry(), ofd_entry(id="ofd_2", weight=250.0)]
        assert cr.find_conflicts(sdb, ofd, {}) == []

    def test_size_conflict_key_does_not_depend_on_feed_order_or_which_member_is_first(self) -> None:
        a = [entry(id="s_a", weight=750.0), entry(id="s_b", weight=500.0)]
        b = [ofd_entry(id="o_a", weight=1000.0), ofd_entry(id="o_b", weight=250.0)]
        forward = cr.find_conflicts(a, b, {})
        backward = cr.find_conflicts(a[::-1], b[::-1], {})
        assert [cr.conflict_key(c) for c in forward] == [cr.conflict_key(c) for c in backward]
        assert forward[0]["sdb_value"] == "500 g / 1.75 mm, 750 g / 1.75 mm"

    def test_duplicate_sdb_entries_resolve_to_the_lowest_id_regardless_of_order(self) -> None:
        dup_a, dup_b = entry(id="a", density=1.5), entry(id="b", density=1.7)
        for order in ([dup_a, dup_b], [dup_b, dup_a]):
            (found,) = cr.find_conflicts(order, [ofd_entry()], {})
            assert (found["sdb_id"], found["sdb_value"]) == ("a", 1.5)

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
        bad.write_text(json.dumps({"keys": ["a"], "counts": {"matched": 3}}))
        loaded = cr.load_previous(bad)
        assert loaded is not None
        assert (loaded.keys, loaded.counts) == ({"a"}, {"matched": 3})

    @pytest.mark.parametrize(
        "raw", [{"keys": "abc"}, {"keys": [1, 2]}, {"keys": None}, {"keys": [], "counts": {"x": "y"}}, [1], {"keys": [], "counts": []}]
    )
    def test_malformed_state_shapes_mean_baseline(self, tmp_path: Path, raw: Any) -> None:
        state = tmp_path / "state.json"
        state.write_text(json.dumps(raw))
        assert cr.load_previous(state) is None


class TestShrinkGuard:
    BEFORE = 1000

    def previous(self) -> cr.PreviousState:
        return cr.PreviousState(set(), {"matched": self.BEFORE})

    def test_exactly_at_the_retained_fraction_is_accepted_and_one_below_is_refused(self) -> None:
        threshold = int(self.BEFORE * cr.MIN_RETAINED_FRACTION)
        assert cr.shrink_problem({"matched": threshold}, self.previous()) is None
        assert cr.shrink_problem({"matched": threshold - 1}, self.previous()) is not None

    def test_growth_and_missing_previous_are_accepted_but_empty_never_is(self) -> None:
        assert cr.shrink_problem({"matched": self.BEFORE * 2}, self.previous()) is None
        assert cr.shrink_problem({"matched": 1}, None) is None
        assert cr.shrink_problem({"matched": 0}, None) is not None

    def test_each_counted_source_is_checked(self) -> None:
        previous = cr.PreviousState(set(), {"a": 100, "b": 100})
        assert cr.shrink_problem({"a": 100, "b": 10}, previous) is not None


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

    def multi(self, per_field: int, **extra: Any) -> list[cr.Conflict]:
        out: list[cr.Conflict] = []
        for field in cr.FIELDS:
            s = entry(name=f"{field} " + "N" * 60 + extra.get("suffix", ""))
            out += [cr.make_conflict(field, s, ofd_entry(id=f"ofd_{field}_{i}"), 1, 2, {}) for i in range(per_field)]
        return out

    def test_truncation_gives_every_field_entries_and_true_totals(self) -> None:
        conflicts = self.multi(200)
        body = cr.render_issue_body(conflicts, 0, "v", baseline=True, budget=6000)
        for field in cr.FIELDS:
            assert f"### {field} (200)" in body
            section = body.split(f"### {field} (200)")[1].split("\n### ")[0]
            assert section.count("\n- **") >= 3, field
        assert "Truncated" in body

    def test_truncation_notice_links_the_full_artifact(self) -> None:
        body = cr.render_issue_body(self.multi(50), 0, "v", baseline=True, budget=3000, full_report_url="https://example.com/run/1")
        assert "conflicts-full" in body and "https://example.com/run/1" in body

    def test_budget_is_measured_in_utf8_bytes_not_characters(self) -> None:
        conflicts = self.multi(40, suffix="\u00e9" * 60 + "\U0001f600" * 20)
        body = cr.render_issue_body(conflicts, 0, "v", baseline=True)
        assert len(body.encode()) <= cr.BODY_BUDGET_BYTES
        assert len(body) < len(body.encode())  # genuinely multi-byte
        small = cr.render_issue_body(conflicts, 0, "v", baseline=True, budget=5000)
        assert len(small.encode()) <= 5000

    def test_full_report_has_every_conflict_untruncated(self) -> None:
        conflicts = self.multi(300)
        full = cr.render_digest("## full", "intro", conflicts, budget=None)
        assert full.count("\n- **") == len(conflicts)
        assert "Truncated" not in full

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

    @pytest.mark.parametrize(
        ("hostile", "must_not_contain"),
        [
            ("evil\r\nINJECT", "\r"),
            ("a\x00b\x1bc\u2028d\u200be", "\x1b"),
            ("ping @maintainer", "@"),
            ("fixes #15", "#15"),
            ("[click](https://evil.example/x)", "]("),
            ("https://evil.example", "://"),
            ("see www.evil.example", "www."),
            ("<script>x</script> <!-- c -->", "<"),
            ("`code` *b* _i_ ~s~ !x", "`"),
        ],
    )
    def test_upstream_text_is_made_inert(self, hostile: str, must_not_contain: str) -> None:
        cleaned = cr.clean(hostile)
        assert must_not_contain not in cleaned.replace("\\" + must_not_contain, "")
        assert "\n" not in cleaned and "\r" not in cleaned

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("user@x.com", "user&#64;x.com"),
            ("fixes #15", "fixes &#35;15"),
            ("a@b#c", "a&#64;b&#35;c"),
            ("GH-123 and gh-9", "GH&#45;123 and gh&#45;9"),
            ("ghost-writer", "ghost-writer"),
            ("http://x www.y", "http:&#47;&#47;x www&#46;y"),
        ],
    )
    def test_neutralised_triggers_use_exact_entities_without_double_replacement(self, raw: str, expected: str) -> None:
        assert cr.clean(raw) == expected

    def test_none_and_missing_version_do_not_render_none(self) -> None:
        assert cr.clean(None) == ""
        body = cr.render_issue_body([], 0, None, baseline=True)
        comment = cr.render_new_comment(self.multi(1), None)
        assert "None" not in body and "None" not in comment
        assert "(version unknown)" in body

    def test_status_line_only_claims_a_comment_when_there_is_one(self) -> None:
        assert "posted as a comment" not in cr.render_issue_body([], 0, "v", baseline=False)
        assert "No new conflicts" in cr.render_issue_body([], 0, "v", baseline=False)
        assert "3 new since the previous run (posted as a comment)" in cr.render_issue_body([], 3, "v", baseline=False)

    @pytest.mark.parametrize("budget", [2500, 3000, 4000, 6000])
    def test_body_including_truncation_notice_never_exceeds_the_budget(self, budget: int) -> None:
        body = cr.render_issue_body(self.multi(100), 0, "v", baseline=True, budget=budget, full_report_url="https://example.com/run/1")
        assert "Truncated" in body
        assert len(body.encode()) <= budget

    def test_a_short_line_in_a_later_field_still_fits_after_a_long_line_in_an_earlier_field_does_not(self) -> None:
        long = cr.make_conflict("density", entry(name="L" * 600), ofd_entry(), 1, 2, {})
        short = cr.make_conflict("size", entry(name="s"), ofd_entry(id="ofd_2"), 1, 2, {})
        short_line = cr.render_line(short) + "\n"
        headers = len(b"\n### density (1)\n") + len(b"\n### size (2)\n")
        text, omitted = cr.render_sections([long, short, short], headers + 2 * len(short_line.encode()))
        assert omitted == 1
        assert text.count(short_line) == 2 and "LLLL" not in text

    def test_control_and_format_characters_are_replaced_by_spaces(self) -> None:
        assert cr.clean("a\x00b\x1bc\u200bd\u2028e\rf") == "a b c d e f"

    def test_hostile_name_is_inert_in_a_rendered_line_and_in_dry_run_output(self) -> None:
        name = "x\r@bob [l](http://e.x) #1"
        (found,) = cr.find_conflicts([entry(name=name)], [ofd_entry(name=name, density=1.5)], {})
        line = cr.render_line(found)
        product = line.split(": SpoolmanDB")[0]  # the legitimate link follows; only the upstream-derived text must be inert
        assert "\r" not in line
        assert "@bob" not in product and "](http" not in product and "#1" not in product


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

    def test_empty_upstream_is_refused_without_writing(self, tmp_path: Path, sources: dict[str, bytes]) -> None:
        empty = sources | {ofd2spoolman.SPOOLMANDB_URL + "filaments.json": b"[]"}
        assert cr.main(["--out", str(tmp_path / "o")], fetcher=fetcher_for(empty)) == 5
        assert not (tmp_path / "o").exists()

    def test_sharp_drop_against_previous_counts_is_refused_but_a_sane_run_is_accepted(
        self, tmp_path: Path, payloads: dict[str, bytes]
    ) -> None:
        good, bad = tmp_path / "good.json", tmp_path / "bad.json"
        good.write_text(json.dumps({"keys": [], "counts": {"sdb_filaments": 2, "ofd_filaments": 9, "matched": 2}}))
        bad.write_text(json.dumps({"keys": [], "counts": {"sdb_filaments": 2, "ofd_filaments": 9, "matched": 100}}))
        assert cr.main(["--out", str(tmp_path / "a"), "--previous", str(good)], fetcher=fetcher_for(payloads)) == 0
        assert cr.main(["--out", str(tmp_path / "b"), "--previous", str(bad)], fetcher=fetcher_for(payloads)) == 5
        assert not (tmp_path / "b").exists()

    def test_state_records_counts_and_the_full_report_is_written(self, tmp_path: Path, payloads: dict[str, bytes]) -> None:
        cr.main(["--out", str(tmp_path), "--run-url", "https://example.com/run/7"], fetcher=fetcher_for(payloads))
        counts = json.loads((tmp_path / "state.json").read_text())["counts"]
        assert counts == {"sdb_filaments": 2, "ofd_filaments": 11, "matched": 1}
        assert "Full conflict report" in (tmp_path / "conflicts-full.md").read_text()

    def test_stale_new_conflicts_file_from_a_reused_out_dir_is_removed(self, tmp_path: Path, payloads: dict[str, bytes]) -> None:
        previous = tmp_path / "prev.json"
        previous.write_text(json.dumps({"keys": []}))
        out = tmp_path / "o"
        cr.main(["--out", str(out), "--previous", str(previous)], fetcher=fetcher_for(payloads))
        assert (out / "new-conflicts.md").exists()
        cr.main(["--out", str(out), "--previous", str(out / "state.json")], fetcher=fetcher_for(payloads))
        assert not (out / "new-conflicts.md").exists()

    def test_fetch_failure_exits_2_without_writing(self, tmp_path: Path) -> None:
        def boom(url: str) -> bytes:
            raise OSError("down")

        assert cr.main(["--out", str(tmp_path / "o")], fetcher=boom) == 2
        assert not (tmp_path / "o").exists()
