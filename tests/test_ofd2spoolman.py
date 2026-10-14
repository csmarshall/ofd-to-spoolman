"""Unit tests for ofd2spoolman against small checked-in fixtures. No network."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import ofd2spoolman
from ofd2spoolman import display_name, norm, norm_hex, pick_temp

RunFn = Callable[..., int]


def load(out_dir: Path, name: str = "filaments.json") -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((out_dir / name).read_text())
    return data


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

    def test_bare_material_without_brand_dropped(self) -> None:
        assert display_name("PETG", "Acme", "PETG", "Black") == "Black"


class TestNorm:
    def test_keeps_plus(self) -> None:
        assert norm("PETG+") == "petg+"
        assert norm("PETG+") != norm("PETG")

    def test_strips_case_and_punctuation(self) -> None:
        assert norm("Poly-Terra (TM) Black!") == "polyterratmblack"


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


class TestNormHex:
    def test_strips_hash_and_lowercases(self) -> None:
        assert norm_hex("#C7873D") == "c7873d"

    def test_accepts_bare_and_alpha_forms(self) -> None:
        assert norm_hex("FF0000") == "ff0000"
        assert norm_hex("#FFA500FF") == "ffa500ff"

    @pytest.mark.parametrize("bad", ["#zzz", "#12345", "red", "#1234567", "", None])
    def test_malformed_is_none(self, bad: str | None) -> None:
        assert norm_hex(bad) is None


class TestConvertOfd:
    def test_traits_map_to_spoolman_enums(self, ofd: dict[str, Any]) -> None:
        entries = ofd2spoolman.convert_ofd(ofd, include_discontinued=False)
        red = by_name(entries, "Acme", "PLA Matte Red")
        assert (red["finish"], red["pattern"]) == ("matte", "sparkle")
        marble = by_name(entries, "Acme", "PLA Matte Marble Grey")
        assert (marble["finish"], marble["pattern"], marble["translucent"]) == (None, "marble", True)
        orange = by_name(entries, "Zeta", "Flex Orange")
        assert (orange["translucent"], orange["glow"]) == (True, True)

    def test_nothing_invented(self, ofd: dict[str, Any]) -> None:
        entries = ofd2spoolman.convert_ofd(ofd, include_discontinued=False)
        black = by_name(entries, "Acme", "PETG+ Black")
        assert black["spool_weight"] is None
        assert by_name(entries, "Acme", "PLA Matte Mystery")["color_hex"] is None
        assert by_name(entries, "Acme", "PLA Matte Red")["spool_weight"] == 180.0

    def test_refill_marked_and_ids_use_size_uuid(self, ofd: dict[str, Any]) -> None:
        entries = ofd2spoolman.convert_ofd(ofd, include_discontinued=False)
        refill = by_name(entries, "Zeta", "Flex Orange (refill)")
        assert refill["weight"] == 750.0
        size_ids = {s["id"] for s in ofd["sizes"]}
        assert all(e["id"].removeprefix("ofd_") in size_ids for e in entries)

    def test_incomplete_and_dangling_rows_skipped(self, ofd: dict[str, Any]) -> None:
        entries = ofd2spoolman.convert_ofd(ofd, include_discontinued=True)
        names = {e["name"] for e in entries}
        assert "Mystery" not in names and "Pink" not in names  # filament without density
        assert len(entries) == 12  # 15 sizes - no density - no weight - dangling

    def test_discontinued_filtered_by_default(self, ofd: dict[str, Any]) -> None:
        names = {e["name"] for e in ofd2spoolman.convert_ofd(ofd, include_discontinued=False)}
        assert "PLA Matte Retired Blue" not in names  # discontinued variant
        assert "Old PLA Green" not in names and "PLA Green" not in names  # discontinued filament
        assert len(names) == 9

    def test_include_discontinued_keeps_them(self, ofd: dict[str, Any]) -> None:
        entries = ofd2spoolman.convert_ofd(ofd, include_discontinued=True)
        names = {e["name"] for e in entries}
        assert {"PLA Matte Retired Blue", "Old PLA Green"} <= names
        assert len(entries) == 12


class TestMergeMaterials:
    def test_ofd_only_material_added_with_median_density(self, ofd: dict[str, Any]) -> None:
        entries = ofd2spoolman.convert_ofd(ofd, include_discontinued=False)
        sdb = [{"material": "PLA", "density": 1.24}, {"material": "PETG", "density": 1.27}]
        merged = ofd2spoolman.merge_materials(sdb, entries)
        assert merged[:2] == sdb
        (added,) = merged[2:]
        assert added["material"] == "TPU95"
        # median across the three TPU95 entries (two 1.20 sizes, one 1.22)
        assert added["density"] == 1.2
        assert added["extruder_temp"] == 220 and added["bed_temp"] == 40


class TestMain:
    def test_end_to_end_merge(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        filaments = load(tmp_path)
        assert len(filaments) == 10
        assert len({f["id"] for f in filaments}) == len(filaments)
        assert [m["material"] for m in load(tmp_path, "materials.json")] == ["PLA", "PETG", "TPU95"]

    def test_brand_spelling_spoolmandb_wins(self, run: RunFn, tmp_path: Path) -> None:
        """OFD says 'SUNLU', SpoolmanDB says 'Sunlu': one spelling in the dropdown, SpoolmanDB's."""
        assert run(tmp_path) == 0
        manufacturers = {f["manufacturer"] for f in load(tmp_path) if f["manufacturer"].lower() == "sunlu"}
        assert manufacturers == {"Sunlu"}

    def test_ofd_duplicate_of_spoolmandb_dropped(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        black = [f for f in load(tmp_path) if f["manufacturer"] == "Sunlu" and f["name"] == "Black"]
        assert len(black) == 1
        assert black[0]["id"] == "sunlu_pla_black_1000_175_n"  # the SpoolmanDB entry, not ofd_*
        assert any(f["name"] == "White" and f["id"].startswith("ofd_") for f in load(tmp_path))

    def test_same_name_different_weight_is_not_a_duplicate(self, run: RunFn, sources: dict[str, bytes], tmp_path: Path) -> None:
        """dedupe_key includes weight: a SpoolmanDB 500 g Sunlu Black must not swallow OFD's 1000 g one."""
        sdb_url = ofd2spoolman.SPOOLMANDB_URL + "filaments.json"
        sdb = json.loads(sources[sdb_url])
        for f in sdb:
            if f["manufacturer"] == "Sunlu" and f["name"] == "Black":
                f["weight"] = 500.0
        assert run(tmp_path, payloads={**sources, sdb_url: json.dumps(sdb).encode()}) == 0
        black = [f for f in load(tmp_path) if f["manufacturer"] == "Sunlu" and f["name"] == "Black"]
        assert sorted(f["weight"] for f in black) == [500.0, 1000.0]

    def test_spoolmandb_entries_pass_through_unchanged(self, run: RunFn, sources: dict[str, bytes], tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        original = json.loads(sources[ofd2spoolman.SPOOLMANDB_URL + "filaments.json"])
        out = load(tmp_path)
        assert out[: len(original)] == original

    def test_discontinued_flag(self, run: RunFn, tmp_path: Path) -> None:
        default, with_disc = tmp_path / "default", tmp_path / "disc"
        assert run(default) == 0
        assert run(with_disc, "--include-discontinued") == 0
        assert len(load(default)) == 10
        assert len(load(with_disc)) == 13

    def test_dry_run_writes_nothing(self, run: RunFn, tmp_path: Path) -> None:
        out = tmp_path / "out"
        assert run(out, "--dry-run") == 0
        assert not out.exists()

    def test_cli_reads_fixtures_from_disk(self, tmp_path: Path) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        argv = ["--out", str(tmp_path), "--from-ofd-file", str(fixtures / "ofd_all.json"), "--from-sdb-dir", str(fixtures / "sdb")]
        assert ofd2spoolman.main(argv) == 0
        assert len(load(tmp_path)) == 10

    def test_disk_sources_options_must_pair(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            ofd2spoolman.main(["--out", str(tmp_path), "--from-ofd-file", "x.json"])
        assert exc.value.code == 2


class TestGuards:
    def seed(self, out: Path, count: int) -> tuple[bytes, bytes]:
        out.mkdir(exist_ok=True)
        (out / "filaments.json").write_text(json.dumps([{"id": str(i)} for i in range(count)]))
        (out / "materials.json").write_text("[]")
        return (out / "filaments.json").read_bytes(), (out / "materials.json").read_bytes()

    def test_fetch_failure_exits_2_and_leaves_files_untouched(self, run: RunFn, tmp_path: Path) -> None:
        before = self.seed(tmp_path, 3)
        # OFD payload missing -> KeyError inside the fetcher; garbage JSON is covered below
        assert run(tmp_path, payloads={}) == 2
        assert (tmp_path / "filaments.json").read_bytes() == before[0]
        assert (tmp_path / "materials.json").read_bytes() == before[1]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["filaments.json", "materials.json"]

    def test_unparseable_source_exits_2(self, run: RunFn, sources: dict[str, bytes], tmp_path: Path) -> None:
        broken = {**sources, ofd2spoolman.OFD_URL: b"<html>502</html>"}
        assert run(tmp_path / "out", payloads=broken) == 2
        assert not (tmp_path / "out").exists()

    def test_duplicate_ids_exit_3_and_publish_nothing(self, run: RunFn, sources: dict[str, bytes], tmp_path: Path) -> None:
        sdb = json.loads(sources[ofd2spoolman.SPOOLMANDB_URL + "filaments.json"])
        sdb.append(dict(sdb[0], name="Another"))
        dup = {**sources, ofd2spoolman.SPOOLMANDB_URL + "filaments.json": json.dumps(sdb).encode()}
        assert run(tmp_path / "out", payloads=dup) == 3
        assert not (tmp_path / "out").exists()

    def test_shrink_over_half_exits_4_and_leaves_files_untouched(self, run: RunFn, tmp_path: Path) -> None:
        before = self.seed(tmp_path, 25)  # fixtures produce 10, below 25 // 2
        assert run(tmp_path) == 4
        assert (tmp_path / "filaments.json").read_bytes() == before[0]
        assert (tmp_path / "materials.json").read_bytes() == before[1]

    def test_shrink_at_exactly_half_is_allowed(self, run: RunFn, tmp_path: Path) -> None:
        self.seed(tmp_path, 20)  # 10 is not < 20 // 2
        assert run(tmp_path) == 0
        assert len(load(tmp_path)) == 10

    def test_missing_published_file_is_first_run_and_publishes(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        assert len(load(tmp_path)) == 10

    @pytest.mark.parametrize(
        "content",
        [b"", b"{not json", b'{"a": 1}', b'"text"', b"null", b"[]"],
        ids=["empty", "garbage", "json-object", "json-string", "json-null", "empty-list"],
    )
    def test_corrupt_published_file_exits_7_and_leaves_files_untouched(
        self, run: RunFn, tmp_path: Path, content: bytes, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "filaments.json").write_bytes(content)
        (tmp_path / "materials.json").write_text("[]")
        assert run(tmp_path) == 7
        assert (tmp_path / "filaments.json").read_bytes() == content
        assert (tmp_path / "materials.json").read_text() == "[]"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["filaments.json", "materials.json"]
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any(str(tmp_path / "filaments.json") in m and "delete" in m for m in errors)

    def test_corrupt_published_file_is_reported_by_dry_run_too(self, run: RunFn, tmp_path: Path) -> None:
        (tmp_path / "filaments.json").write_bytes(b"")
        assert run(tmp_path, "--dry-run") == 7
        assert (tmp_path / "filaments.json").read_bytes() == b""

    def test_deleting_the_corrupt_file_recovers(self, run: RunFn, tmp_path: Path) -> None:
        (tmp_path / "filaments.json").write_bytes(b"")
        assert run(tmp_path) == 7
        (tmp_path / "filaments.json").unlink()
        assert run(tmp_path) == 0
        assert len(load(tmp_path)) == 10

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
    def test_unreadable_published_file_exits_7(self, run: RunFn, tmp_path: Path) -> None:
        self.seed(tmp_path, 3)
        (tmp_path / "filaments.json").chmod(0o000)
        try:
            assert run(tmp_path) == 7
        finally:
            (tmp_path / "filaments.json").chmod(0o644)
        assert len(json.loads((tmp_path / "filaments.json").read_text())) == 3


class TestPublish:
    def test_no_temp_files_left_and_mode_0644(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        assert sorted(p.name for p in tmp_path.iterdir()) == ["filaments.json", "materials.json"]
        for p in tmp_path.iterdir():
            assert stat.S_IMODE(p.stat().st_mode) == 0o644

    def test_output_is_compact_json(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        text = (tmp_path / "filaments.json").read_text()
        assert json.dumps(json.loads(text), separators=(",", ":")) == text

    def test_replaces_existing_file_via_rename(self, tmp_path: Path) -> None:
        (tmp_path / "a.json").write_text("old")
        inode_before = (tmp_path / "a.json").stat().st_ino
        ofd2spoolman.publish(tmp_path, {"a.json": [1]})
        assert (tmp_path / "a.json").read_text() == "[1]"
        assert (tmp_path / "a.json").stat().st_ino != inode_before  # new file renamed in, not rewritten in place
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


class TestFetch:
    def test_gunzips_gz_urls_and_sends_user_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The production fetch path (tests otherwise inject a fetcher): .gz payloads are decompressed."""
        import gzip
        import io
        import urllib.request

        seen: dict[str, Any] = {}

        def fake_urlopen(req: urllib.request.Request, timeout: float) -> io.BytesIO:
            seen["ua"] = req.get_header("User-agent")
            seen["timeout"] = timeout
            return io.BytesIO(gzip.compress(b'{"ok": true}') if req.full_url.endswith(".gz") else b"plain")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert ofd2spoolman.fetch("https://example.invalid/all.json.gz") == b'{"ok": true}'
        assert ofd2spoolman.fetch("https://example.invalid/filaments.json") == b"plain"
        assert seen["ua"] == ofd2spoolman.USER_AGENT
        assert seen["timeout"] > 0


class TestCli:
    def test_out_abbreviation_still_works(self, sources: dict[str, bytes], tmp_path: Path) -> None:
        """Regression: hidden options must not make `--o` ambiguous with `--out`."""
        from conftest import fetcher_for

        assert ofd2spoolman.main(["--o", str(tmp_path), "--dry-run"], fetcher=fetcher_for(sources)) == 0
