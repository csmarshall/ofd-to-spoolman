"""Unit tests for ofd2spoolman against small checked-in fixtures. No network."""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, NoReturn

import pytest
from conftest import FIXTURE_NOW

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
        assert ofd2spoolman.main(argv, now=FIXTURE_NOW) == 0
        assert len(load(tmp_path)) == 10

    def test_out_is_required(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            ofd2spoolman.main(["--dry-run"], fetcher=_no_network)
        assert exc.value.code == 2
        assert "--out" in capsys.readouterr().err

    def test_disk_sources_options_must_pair(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            ofd2spoolman.main(["--out", str(tmp_path), "--from-ofd-file", "x.json"])
        assert exc.value.code == 2


def _no_network(url: str) -> NoReturn:
    raise AssertionError(f"unexpected fetch of {url}")


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
        [b"", b"{not json", b'{"a": 1}', b'"text"', b"null", b"[]", b"\xff\xfe[", b"[" * 100000],
        ids=["empty", "garbage", "json-object", "json-string", "json-null", "empty-list", "non-utf8", "deeply-nested"],
    )
    def test_corrupt_published_file_exits_7_and_leaves_files_untouched(
        self, run: RunFn, tmp_path: Path, content: bytes, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "filaments.json").write_bytes(content)
        (tmp_path / "materials.json").write_text("[]")
        assert run(tmp_path) == ofd2spoolman.EXIT_CORRUPT_BASELINE == 7
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
        assert sorted(p.name for p in tmp_path.iterdir()) == ["filaments.json", "materials.json", "state.json"]
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
        import email
        import gzip
        import io
        import urllib.request

        seen: dict[str, Any] = {}

        def fake_urlopen(req: urllib.request.Request, timeout: float) -> io.BytesIO:
            seen["ua"] = req.get_header("User-agent")
            seen["timeout"] = timeout
            resp = io.BytesIO(gzip.compress(b'{"ok": true}') if req.full_url.endswith(".gz") else b"plain")
            resp.headers = email.message_from_string("Last-Modified: Tue, 13 Oct 2026 03:00:00 GMT\n\n")  # type: ignore[attr-defined]
            return resp

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert ofd2spoolman.fetch("https://example.invalid/all.json.gz") == ofd2spoolman.Fetched(
            b'{"ok": true}', "Tue, 13 Oct 2026 03:00:00 GMT"
        )
        assert ofd2spoolman.fetch("https://example.invalid/filaments.json") == ofd2spoolman.Fetched(
            b"plain", "Tue, 13 Oct 2026 03:00:00 GMT"
        )
        assert seen["ua"] == ofd2spoolman.USER_AGENT
        assert seen["timeout"] > 0


class TestCli:
    def test_out_abbreviation_still_works(self, sources: dict[str, bytes], tmp_path: Path) -> None:
        """Regression: hidden options must not make `--o` ambiguous with `--out`."""
        from conftest import fetcher_for

        assert ofd2spoolman.main(["--o", str(tmp_path), "--dry-run", "--now", "2026-10-13T04:00:00Z"], fetcher=fetcher_for(sources)) == 0


class TestPublishAtomicPair:
    """publish() stages both temps, then renames back to back; every leftover is cleaned up."""

    OLD: ClassVar[dict[str, str]] = {"filaments.json": "old-f", "materials.json": "old-m"}

    def _seed(self, d: Path) -> None:
        for name, text in self.OLD.items():
            (d / name).write_text(text)

    def _assert_old_pair(self, d: Path) -> None:
        assert {p.name: p.read_text() for p in d.iterdir()} == self.OLD

    def test_clean_publish_leaves_exactly_the_pair_mode_0644(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2, 3]})
        assert sorted(p.name for p in tmp_path.iterdir()) == ["filaments.json", "materials.json"]
        assert (tmp_path / "filaments.json").read_text() == "[1]"
        assert (tmp_path / "materials.json").read_text() == "[2,3]"
        for p in tmp_path.iterdir():
            assert stat.S_IMODE(p.stat().st_mode) == 0o644

    def test_json_dump_failure_leaves_no_temp_and_old_pair(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._seed(tmp_path)
        real_dump = json.dump
        calls = []

        def flaky_dump(*args: Any, **kwargs: Any) -> None:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            real_dump(*args, **kwargs)

        monkeypatch.setattr(ofd2spoolman.json, "dump", flaky_dump)
        with pytest.raises(RuntimeError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        self._assert_old_pair(tmp_path)

    def test_first_json_dump_failure_leaves_no_temp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._seed(tmp_path)

        def bad_dump(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(ofd2spoolman.json, "dump", bad_dump)
        with pytest.raises(RuntimeError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        self._assert_old_pair(tmp_path)

    def test_temp_creation_failure_cleans_up_earlier_temp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._seed(tmp_path)
        real_mkstemp = ofd2spoolman.tempfile.mkstemp
        calls = []

        def flaky_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
            calls.append(1)
            if len(calls) == 2:
                raise OSError("no space")
            return real_mkstemp(*args, **kwargs)

        monkeypatch.setattr(ofd2spoolman.tempfile, "mkstemp", flaky_mkstemp)
        with pytest.raises(OSError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        self._assert_old_pair(tmp_path)

    def test_first_rename_failure_leaves_no_temp_and_old_pair(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._seed(tmp_path)

        def bad_replace(src: Any, dst: Any) -> None:
            raise OSError("rename failed")

        monkeypatch.setattr(ofd2spoolman.os, "replace", bad_replace)
        with pytest.raises(OSError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        self._assert_old_pair(tmp_path)

    def test_second_rename_failure_restores_first_logs_error_no_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._seed(tmp_path)
        real_replace = ofd2spoolman.os.replace
        calls = []

        def flaky_replace(src: Any, dst: Any) -> None:
            calls.append(1)
            if len(calls) == 2:
                raise OSError("rename failed")
            real_replace(src, dst)

        monkeypatch.setattr(ofd2spoolman.os, "replace", flaky_replace)
        with caplog.at_level("ERROR"), pytest.raises(OSError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert errors
        assert "filaments.json" in errors[0] and "materials.json" in errors[0]
        # Rollback does its own os.replace (call 3), so the old pair is back and nothing else remains.
        self._assert_old_pair(tmp_path)

    def test_second_rename_failure_with_failed_restore_logs_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._seed(tmp_path)
        real_replace = ofd2spoolman.os.replace
        calls = []

        def flaky_replace(src: Any, dst: Any) -> None:
            calls.append(1)
            if len(calls) >= 2:
                raise OSError("rename failed")
            real_replace(src, dst)

        monkeypatch.setattr(ofd2spoolman.os, "replace", flaky_replace)
        with caplog.at_level("ERROR"), pytest.raises(OSError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        assert any("MISMATCH" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")
        # On disk: new materials, old filaments; the unrestored backup is kept for recovery.
        assert (tmp_path / "materials.json").read_text() == "[2]"
        assert (tmp_path / "filaments.json").read_text() == "old-f"
        assert [p.read_text() for p in tmp_path.glob(".materials.json.*.old")] == ["old-m"]
        assert not list(tmp_path.glob(".filaments.json.*"))

    def test_first_ever_publish_second_rename_failure_removes_lone_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        real_replace = ofd2spoolman.os.replace
        calls = []

        def flaky_replace(src: Any, dst: Any) -> None:
            calls.append(1)
            if len(calls) == 2:
                raise OSError("rename failed")
            real_replace(src, dst)

        monkeypatch.setattr(ofd2spoolman.os, "replace", flaky_replace)
        with pytest.raises(OSError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        assert list(tmp_path.iterdir()) == []

    def test_rename_order_is_materials_then_filaments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        real_replace = ofd2spoolman.os.replace
        order: list[str] = []

        def recording_replace(src: Any, dst: Any) -> None:
            order.append(Path(dst).name)
            real_replace(src, dst)

        monkeypatch.setattr(ofd2spoolman.os, "replace", recording_replace)
        ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        assert order == ["materials.json", "filaments.json"]

    def test_link_failure_with_live_files_never_deletes_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._seed(tmp_path)
        real_replace = ofd2spoolman.os.replace
        calls = []

        def no_link(src: Any, dst: Any) -> None:
            raise PermissionError("protected_hardlinks")

        def flaky_replace(src: Any, dst: Any) -> None:
            calls.append(1)
            if len(calls) == 2:
                raise OSError("rename failed")
            real_replace(src, dst)

        monkeypatch.setattr(ofd2spoolman.os, "link", no_link)
        monkeypatch.setattr(ofd2spoolman.os, "replace", flaky_replace)
        with caplog.at_level("ERROR"), pytest.raises(OSError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        assert sorted(p.name for p in tmp_path.iterdir()) == ["filaments.json", "materials.json"]
        assert (tmp_path / "materials.json").read_text() == "[2]"  # new file left in place, not deleted
        assert any("MISMATCH" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")

    def test_cleanup_failure_does_not_mask_error_or_skip_later_leftovers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._seed(tmp_path)
        real_dump = json.dump
        real_unlink = ofd2spoolman.os.unlink
        calls = []

        def flaky_dump(*args: Any, **kwargs: Any) -> None:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            real_dump(*args, **kwargs)

        def flaky_unlink(path: Any) -> None:
            if not getattr(flaky_unlink, "failed", False):
                flaky_unlink.failed = True  # type: ignore[attr-defined]
                raise PermissionError("nope")
            real_unlink(path)

        monkeypatch.setattr(ofd2spoolman.json, "dump", flaky_dump)
        monkeypatch.setattr(ofd2spoolman.os, "unlink", flaky_unlink)
        with pytest.raises(RuntimeError):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        # exactly one temp (the first leftover) could not be removed; the later one and the backups are gone
        leftovers = [p for p in tmp_path.iterdir() if p.name not in self.OLD]
        assert len(leftovers) == 1

    def test_interrupt_between_renames_restores_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._seed(tmp_path)
        real_replace = ofd2spoolman.os.replace
        calls = []

        def interrupted_replace(src: Any, dst: Any) -> None:
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt
            real_replace(src, dst)

        monkeypatch.setattr(ofd2spoolman.os, "replace", interrupted_replace)
        with pytest.raises(KeyboardInterrupt):
            ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        self._assert_old_pair(tmp_path)

    def test_sweeps_only_exact_stale_patterns(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        stale = [".filaments.json.ab12CD_9", ".filaments.json.ab12CD_9.old", ".materials.json.Zz09aaaa"]
        keep = [".filaments.json.short", ".filaments.json.ab12CD_9.bak", "x.filaments.json.ab12CD_9", ".other.json.ab12CD_9", "notes.txt"]
        for n in [*stale, *keep]:
            (tmp_path / n).write_text("x")
        ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*keep, "filaments.json", "materials.json"])


SDB_F = ofd2spoolman.SPOOLMANDB_URL + "filaments.json"
SDB_M = ofd2spoolman.SPOOLMANDB_URL + "materials.json"


def with_sdb(
    sources: dict[str, bytes], filaments: list[dict[str, Any]] | None = None, materials: list[dict[str, Any]] | None = None
) -> dict[str, bytes]:
    """Copy of `sources` with SpoolmanDB filaments/materials replaced."""
    out = dict(sources)
    if filaments is not None:
        out[SDB_F] = json.dumps(filaments).encode()
    if materials is not None:
        out[SDB_M] = json.dumps(materials).encode()
    return out


def write_state(out: Path, **fields: Any) -> None:
    out.mkdir(exist_ok=True)
    (out / "state.json").write_text(json.dumps(fields))


class TestSpoolmanDbSuperset:
    sdb: ClassVar[list[dict[str, Any]]] = [{"id": "a", "manufacturer": "Sunlu", "name": "X", "material": "PLA", "density": 1.24}]
    mats: ClassVar[list[dict[str, Any]]] = [{"material": "PLA", "density": 1.24}]

    def test_identical_passes(self) -> None:
        assert ofd2spoolman.check_spoolmandb_superset(self.sdb, self.mats, self.sdb, self.mats) == []

    def test_brand_respelling_is_allowed(self) -> None:
        out = [dict(self.sdb[0], manufacturer="SUNLU")]
        assert ofd2spoolman.check_spoolmandb_superset(self.sdb, self.mats, out, self.mats) == []

    def test_missing_filament_is_reported(self) -> None:
        assert ofd2spoolman.check_spoolmandb_superset(self.sdb, self.mats, [], self.mats)

    def test_changed_field_is_reported(self) -> None:
        out = [dict(self.sdb[0], density=9.9)]
        assert ofd2spoolman.check_spoolmandb_superset(self.sdb, self.mats, out, self.mats)

    def test_different_brand_is_reported(self) -> None:
        out = [dict(self.sdb[0], manufacturer="Other")]
        assert ofd2spoolman.check_spoolmandb_superset(self.sdb, self.mats, out, self.mats)

    def test_missing_material_is_reported(self) -> None:
        assert ofd2spoolman.check_spoolmandb_superset(self.sdb, self.mats, self.sdb, [])

    def test_changed_material_is_reported(self) -> None:
        assert ofd2spoolman.check_spoolmandb_superset(self.sdb, self.mats, self.sdb, [{"material": "PLA", "density": 2}])

    def test_end_to_end_good_fixtures_pass_despite_brand_unification(self, run: RunFn, tmp_path: Path) -> None:
        # the fixtures have Sunlu (SpoolmanDB) vs SUNLU (OFD): the rewrite must not trip the guard
        assert run(tmp_path) == 0

    def test_end_to_end_violation_exits_5_and_writes_nothing(
        self, run: RunFn, sources: dict[str, bytes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # simulate a merge bug that drops a SpoolmanDB entry
        real_merge = ofd2spoolman.merge_materials
        monkeypatch.setattr(ofd2spoolman, "merge_materials", lambda m, f: real_merge(m, f)[1:])
        assert run(tmp_path / "out", payloads=sources) == ofd2spoolman.EXIT_SPOOLMANDB_NOT_SUPERSET
        assert not (tmp_path / "out").exists()

    def test_runs_on_dry_run_too(self, run: RunFn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        real_merge = ofd2spoolman.merge_materials
        monkeypatch.setattr(ofd2spoolman, "merge_materials", lambda m, f: real_merge(m, f)[1:])
        assert run(tmp_path, "--dry-run") == ofd2spoolman.EXIT_SPOOLMANDB_NOT_SUPERSET


class TestSourceShrink:
    def test_ofd_collapse_refuses(self, run: RunFn, tmp_path: Path) -> None:
        write_state(tmp_path, counts={"spoolmandb_filaments": 2, "ofd_filaments": 100})
        assert run(tmp_path) == ofd2spoolman.EXIT_SOURCE_SHRANK
        assert not (tmp_path / "filaments.json").exists()

    def test_spoolmandb_collapse_refuses(self, run: RunFn, tmp_path: Path) -> None:
        write_state(tmp_path, counts={"spoolmandb_filaments": 100, "ofd_filaments": 9})
        assert run(tmp_path) == ofd2spoolman.EXIT_SOURCE_SHRANK

    def test_normal_fluctuation_passes(self, run: RunFn, tmp_path: Path) -> None:
        # 9 OFD entries now vs 12 before is a 25% drop: allowed
        write_state(tmp_path, counts={"spoolmandb_filaments": 2, "ofd_filaments": 12})
        assert run(tmp_path) == 0

    def test_exactly_at_threshold_passes(self) -> None:
        assert ofd2spoolman.check_source_shrink({"counts": {"spoolmandb_filaments": 4, "ofd_filaments": 18}}, 2, 9) == []

    def test_no_baseline_passes(self) -> None:
        assert ofd2spoolman.check_source_shrink({}, 0, 0) == []

    def test_runs_on_dry_run_too(self, run: RunFn, tmp_path: Path) -> None:
        write_state(tmp_path, counts={"spoolmandb_filaments": 2, "ofd_filaments": 100})
        assert run(tmp_path, "--dry-run") == ofd2spoolman.EXIT_SOURCE_SHRANK


class TestStateAndFreshness:
    def test_state_recorded_on_publish(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["ofd_version"] == "2026.10.13"
        assert state["ofd_generated_at"] == "2026-10-13T03:01:57Z"
        assert state["run_at"] == "2026-10-13T04:01:57+00:00"
        assert state["counts"]["spoolmandb_filaments"] == 2
        assert state["counts"]["ofd_filaments"] == 9
        assert len(state["spoolmandb_hash"]) == 64

    def test_dry_run_writes_no_state(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path, "--dry-run") == 0
        assert list(tmp_path.iterdir()) == []

    def test_refused_run_keeps_old_state(self, run: RunFn, tmp_path: Path) -> None:
        write_state(tmp_path, counts={"spoolmandb_filaments": 2, "ofd_filaments": 100})
        before = (tmp_path / "state.json").read_bytes()
        assert run(tmp_path) != 0
        assert (tmp_path / "state.json").read_bytes() == before

    def test_changed_at_only_moves_on_change(self, run: RunFn, sources: dict[str, bytes], tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        first = json.loads((tmp_path / "state.json").read_text())
        later = FIXTURE_NOW + timedelta(hours=5)
        assert run(tmp_path, now=later) == 0
        second = json.loads((tmp_path / "state.json").read_text())
        assert second["run_at"] != first["run_at"]
        assert second["ofd_version_changed_at"] == first["ofd_version_changed_at"]
        assert second["spoolmandb_changed_at"] == first["spoolmandb_changed_at"]
        sdb = json.loads(sources[SDB_F])
        sdb[0]["density"] = 1.25
        assert run(tmp_path, now=later + timedelta(hours=5), payloads=with_sdb(sources, filaments=sdb)) == 0
        third = json.loads((tmp_path / "state.json").read_text())
        assert third["spoolmandb_hash"] != first["spoolmandb_hash"]
        assert third["spoolmandb_changed_at"] == "2026-10-13T14:01:57+00:00"
        assert third["ofd_version_changed_at"] == first["ofd_version_changed_at"]

    def test_ofd_stalled_warns_but_still_publishes(self, run: RunFn, tmp_path: Path) -> None:
        code = run(tmp_path, now=FIXTURE_NOW + ofd2spoolman.OFD_STALE_AFTER + timedelta(days=1))
        assert code == ofd2spoolman.EXIT_PUBLISHED_WITH_WARNINGS == 10
        assert len(load(tmp_path)) == 10
        assert (tmp_path / "state.json").exists()

    def test_ofd_fresh_is_quiet(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path, now=FIXTURE_NOW + ofd2spoolman.OFD_STALE_AFTER - timedelta(hours=2)) == 0

    def test_stalled_fallback_uses_first_seen_version_when_no_generated_at(self) -> None:
        prev = {"ofd_version": "v1", "ofd_version_changed_at": FIXTURE_NOW.isoformat()}
        old = FIXTURE_NOW + timedelta(days=4)
        assert ofd2spoolman.check_ofd_stalled({"version": "v1"}, prev, old)
        assert not ofd2spoolman.check_ofd_stalled({"version": "v2"}, prev, old)  # version advanced
        assert not ofd2spoolman.check_ofd_stalled({"version": "v1"}, {}, old)  # nothing to compare

    def test_exit_codes_are_distinct_and_warning_code_is_not_a_failure_code(self) -> None:
        failures = [2, 3, 4, ofd2spoolman.EXIT_SPOOLMANDB_NOT_SUPERSET, ofd2spoolman.EXIT_SOURCE_SHRANK]
        assert len(set(failures)) == len(failures)
        assert ofd2spoolman.EXIT_PUBLISHED_WITH_WARNINGS not in failures


class TestNowFlag:
    def test_hidden_now_pins_the_clock(self, sources: dict[str, bytes], tmp_path: Path) -> None:
        from conftest import fetcher_for

        argv = ["--out", str(tmp_path), "--dry-run", "--now", "2026-10-13T04:00:00Z"]
        assert ofd2spoolman.main(argv, fetcher=fetcher_for(sources)) == 0

    def test_far_future_now_is_stalled(self, sources: dict[str, bytes], tmp_path: Path) -> None:
        """The fixture ages out on the real clock; this is exactly why CI must pin --now."""
        from conftest import fetcher_for

        argv = ["--out", str(tmp_path), "--dry-run", "--now", "2030-01-01T00:00:00Z"]
        assert ofd2spoolman.main(argv, fetcher=fetcher_for(sources)) == ofd2spoolman.EXIT_PUBLISHED_WITH_WARNINGS

    def test_unparseable_now_is_a_usage_error(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            ofd2spoolman.main(["--out", str(tmp_path), "--now", "yesterday"])
        assert exc.value.code == 2


class TestLoadState:
    @pytest.mark.parametrize(
        "content",
        ["not json", "[1, 2]", "null", '"s"', "42"],
    )
    def test_unusable_top_level_is_empty(self, tmp_path: Path, content: str) -> None:
        (tmp_path / "state.json").write_text(content)
        assert ofd2spoolman.load_state(tmp_path) == {}

    @pytest.mark.parametrize(
        "counts",
        [
            [1, 2],
            "abc",
            {"ofd_filaments": "abc"},
            {"ofd_filaments": None},
            {"ofd_filaments": -5},
            {"ofd_filaments": 0},
            {"ofd_filaments": True},
            {"ofd_filaments": 1.5},
        ],
    )
    def test_bad_counts_are_dropped_and_run_survives(
        self, run: RunFn, tmp_path: Path, counts: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_state(tmp_path, counts=counts, ofd_version="old")
        state = ofd2spoolman.load_state(tmp_path)
        assert "counts" not in state
        assert state["ofd_version"] == "old"
        assert any(r.levelname == "WARNING" for r in caplog.records)
        assert run(tmp_path) == 0  # no crash, no bogus baseline

    def test_good_counts_survive(self, tmp_path: Path) -> None:
        write_state(tmp_path, counts={"ofd_filaments": 5, "spoolmandb_filaments": 2})
        assert ofd2spoolman.load_state(tmp_path)["counts"] == {"ofd_filaments": 5, "spoolmandb_filaments": 2}

    def test_non_string_fields_are_dropped(self, tmp_path: Path) -> None:
        write_state(tmp_path, ofd_version=5, ofd_version_changed_at=["x"], spoolmandb_hash=None)
        assert ofd2spoolman.load_state(tmp_path) == {}


class TestStalledEdges:
    def test_none_version_does_not_false_positive(self) -> None:
        prev = {"ofd_version": None, "ofd_version_changed_at": FIXTURE_NOW.isoformat()}
        assert ofd2spoolman.check_ofd_stalled({}, prev, FIXTURE_NOW + timedelta(days=30)) == []

    def test_unparseable_generated_at_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        ofd2spoolman.check_ofd_stalled({"version": "v", "generated_at": "garbage"}, {}, FIXTURE_NOW)
        assert any(r.levelname == "WARNING" and "generated_at" in r.getMessage() for r in caplog.records)

    def test_message_reports_hours(self) -> None:
        (msg,) = ofd2spoolman.check_ofd_stalled(
            {"generated_at": "2026-10-13T03:00:00Z"}, {}, FIXTURE_NOW.replace(hour=4) + timedelta(days=3, hours=5)
        )
        assert "hours" in msg and "77" not in msg and "78.0" in msg

    def test_offset_and_naive_generated_at(self) -> None:
        late = datetime(2026, 10, 20, tzinfo=UTC)
        assert ofd2spoolman.check_ofd_stalled({"generated_at": "2026-10-13T03:00:00+02:00"}, {}, late)
        assert ofd2spoolman.check_ofd_stalled({"generated_at": "2026-10-13T03:00:00"}, {}, late)  # naive = UTC
        assert not ofd2spoolman.check_ofd_stalled({"generated_at": "2026-10-13T03:00:00+02:00"}, {}, datetime(2026, 10, 14, tzinfo=UTC))


class TestSupersetEdges:
    def test_duplicate_material_names_do_not_false_fire(self) -> None:
        mats = [{"material": "PLA", "density": 1.24}, {"material": "PLA", "density": 1.25}]
        assert ofd2spoolman.check_spoolmandb_superset([], mats, [], list(mats)) == []

    def test_lost_duplicate_material_is_reported(self) -> None:
        mats = [{"material": "PLA", "density": 1.24}, {"material": "PLA", "density": 1.25}]
        assert ofd2spoolman.check_spoolmandb_superset([], mats, [], mats[:1])

    def test_bool_is_not_int(self) -> None:
        sdb = [{"id": "a", "manufacturer": "S", "glow": False}]
        assert ofd2spoolman.check_spoolmandb_superset(sdb, [], [{"id": "a", "manufacturer": "S", "glow": 0}], [])
        assert ofd2spoolman.check_spoolmandb_superset(sdb, [], [{"id": "a", "manufacturer": "S", "glow": False}], []) == []

    def test_end_to_end_field_corruption_is_caught(self, run: RunFn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bug that alters a SpoolmanDB field after the snapshot must fire the guard (kills 'snapshot not deep-copied')."""
        real_merge = ofd2spoolman.merge_materials

        def corrupting(m: list[dict[str, Any]], f: list[dict[str, Any]]) -> list[dict[str, Any]]:
            f[0]["density"] = 9.99
            return real_merge(m, f)

        monkeypatch.setattr(ofd2spoolman, "merge_materials", corrupting)
        assert run(tmp_path / "out") == ofd2spoolman.EXIT_SPOOLMANDB_NOT_SUPERSET
        assert not (tmp_path / "out").exists()


class TestStateEdges:
    def test_ofd_version_changed_at_advances_on_new_version(self, run: RunFn, sources: dict[str, bytes], tmp_path: Path) -> None:
        assert run(tmp_path) == 0
        first = json.loads((tmp_path / "state.json").read_text())
        newer = json.loads(sources[ofd2spoolman.OFD_URL])
        newer["version"] = "2026.10.14"
        newer["generated_at"] = "2026-10-14T03:00:00Z"
        later = FIXTURE_NOW + timedelta(days=1)
        assert run(tmp_path, now=later, payloads={**sources, ofd2spoolman.OFD_URL: json.dumps(newer).encode()}) == 0
        second = json.loads((tmp_path / "state.json").read_text())
        assert second["ofd_version"] == "2026.10.14"
        assert second["ofd_version_changed_at"] == later.isoformat(timespec="seconds") != first["ofd_version_changed_at"]

    def test_failed_state_write_leaks_no_temp_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(src: Any, dst: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ofd2spoolman.os, "replace", boom)
        with pytest.raises(OSError):
            ofd2spoolman.write_state(tmp_path, {"a": 1})
        assert list(tmp_path.iterdir()) == []


LM_OFD, LM_SDB_F, LM_SDB_M = (f"Tue, 13 Oct 2026 0{n}:00:00 GMT" for n in (3, 4, 5))
LAST_MODIFIED = {ofd2spoolman.OFD_URL: LM_OFD, SDB_F: LM_SDB_F, SDB_M: LM_SDB_M}


def freshness_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "FRESHNESS" in r.getMessage()]


def mismatches(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in freshness_lines(caplog) if r.levelno == logging.WARNING]


class TestInputFreshness:
    """Last-Modified and content hash must move together; one without the other is warned about, never blocking."""

    def _first(self, run: RunFn, tmp_path: Path) -> None:
        assert run(tmp_path, last_modified=LAST_MODIFIED) == 0

    def test_first_run_is_info_only_and_records_both_fields(self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        self._first(run, tmp_path)
        assert freshness_lines(caplog)
        assert not mismatches(caplog)
        inputs = json.loads((tmp_path / "state.json").read_text())["inputs"]
        assert set(inputs) == {"ofd", "spoolmandb_filaments", "spoolmandb_materials"}
        assert inputs["ofd"]["last_modified"] == LM_OFD
        assert inputs["spoolmandb_filaments"]["last_modified"] == LM_SDB_F
        assert all(len(v["sha256"]) == 64 for v in inputs.values())

    def test_both_changed_is_a_normal_update(
        self, run: RunFn, sources: dict[str, bytes], tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._first(run, tmp_path)
        caplog.set_level(logging.INFO)
        extra = [*json.loads(sources[SDB_F]), {**json.loads(sources[SDB_F])[0], "id": "sdb_new", "name": "Brand New"}]
        newer = {k: LM_SDB_F.replace("04:00", "09:00") if k == SDB_F else v for k, v in LAST_MODIFIED.items()}
        assert run(tmp_path, payloads=with_sdb(sources, filaments=extra), last_modified=newer) == 0
        assert not mismatches(caplog)
        assert any("source=spoolmandb_filaments" in m and "updated" in m for m in (r.getMessage() for r in freshness_lines(caplog)))

    def test_neither_changed_is_unchanged(self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        self._first(run, tmp_path)
        caplog.set_level(logging.INFO)
        assert run(tmp_path, last_modified=LAST_MODIFIED) == 0
        assert not mismatches(caplog)
        unchanged = [r.getMessage() for r in freshness_lines(caplog) if "unchanged" in r.getMessage()]
        assert len(unchanged) == 3
        assert all(r.levelno == logging.INFO for r in freshness_lines(caplog))

    def test_last_modified_moved_with_identical_content_warns_for_ofd(
        self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._first(run, tmp_path)
        caplog.set_level(logging.INFO)
        moved = {**LAST_MODIFIED, ofd2spoolman.OFD_URL: "Wed, 14 Oct 2026 03:00:00 GMT"}
        assert run(tmp_path, last_modified=moved) == 0  # exit code unchanged, still publishes
        (line,) = mismatches(caplog)
        assert line.startswith("FRESHNESS-MISMATCH source=ofd ")
        assert "last_modified moved" in line
        assert "sha256" in line
        recorded = json.loads((tmp_path / "state.json").read_text())
        assert recorded["freshness_mismatches"][0]["source"] == "ofd"
        assert recorded["freshness_mismatches"][0]["moved"] == "last_modified"

    def test_content_changed_with_same_last_modified_warns(
        self, run: RunFn, sources: dict[str, bytes], tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._first(run, tmp_path)
        caplog.set_level(logging.INFO)
        mats = [*json.loads(sources[SDB_M]), {"material": "ZZZ", "density": 1.0}]
        assert run(tmp_path, payloads=with_sdb(sources, materials=mats), last_modified=LAST_MODIFIED) == 0
        (line,) = mismatches(caplog)
        assert line.startswith("FRESHNESS-MISMATCH source=spoolmandb_materials ")
        assert "sha256 moved" in line

    def test_mismatch_does_not_repeat_once_recorded(self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        self._first(run, tmp_path)
        moved = {**LAST_MODIFIED, ofd2spoolman.OFD_URL: "Wed, 14 Oct 2026 03:00:00 GMT"}
        assert run(tmp_path, last_modified=moved) == 0
        caplog.set_level(logging.INFO)
        caplog.clear()  # forget the first run's (expected) warning
        assert run(tmp_path, last_modified=moved) == 0
        assert not mismatches(caplog)
        assert json.loads((tmp_path / "state.json").read_text())["freshness_mismatches"] == []

    def test_missing_header_is_info_not_a_warning(self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        self._first(run, tmp_path)
        caplog.set_level(logging.INFO)
        assert run(tmp_path) == 0  # no Last-Modified at all (as with --from-ofd-file)
        assert not mismatches(caplog)
        assert any("no Last-Modified" in r.getMessage() for r in freshness_lines(caplog))

    def test_old_state_without_inputs_is_info_not_a_warning(self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        write_state(tmp_path, run_at="2026-10-12T04:00:00+00:00", ofd_version="v0")
        caplog.set_level(logging.INFO)
        assert run(tmp_path, last_modified=LAST_MODIFIED) == 0
        assert not mismatches(caplog)

    def test_local_fetcher_has_no_last_modified(self, sources: dict[str, bytes], tmp_path: Path) -> None:
        ofd_file = tmp_path / "all.json"
        ofd_file.write_bytes(sources[ofd2spoolman.OFD_URL])
        fetched = ofd2spoolman.local_fetcher(ofd_file, tmp_path)(ofd2spoolman.OFD_URL)
        assert fetched.last_modified is None
        assert fetched.data == sources[ofd2spoolman.OFD_URL]

    def test_detector_can_fail_and_stays_silent_on_two_sided_change(self) -> None:
        """Known-bad and known-good straight into the pure function."""
        old = b"old"
        prev = {"inputs": {"x": {"last_modified": "A", "sha256": ofd2spoolman.hashlib.sha256(old).hexdigest()}}}
        check = ofd2spoolman.check_input_freshness

        (same,) = check(prev, {"x": ofd2spoolman.Fetched(old, "A")})
        (both,) = check(prev, {"x": ofd2spoolman.Fetched(b"new", "B")})
        (lm_only,) = check(prev, {"x": ofd2spoolman.Fetched(old, "B")})
        (content_only,) = check(prev, {"x": ofd2spoolman.Fetched(b"new", "A")})
        assert lm_only.mismatch and content_only.mismatch
        assert (same.status, both.status) == ("unchanged", "updated")
        assert (lm_only.status, lm_only.moved) == ("mismatch", "last_modified")
        assert (content_only.status, content_only.moved) == ("mismatch", "sha256")

    def test_pages_redeploy_is_info_for_spoolmandb(self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """GitHub Pages restamps every file on each deploy, so a Last-Modified-only move on SpoolmanDB is expected."""
        self._first(run, tmp_path)
        caplog.set_level(logging.INFO)
        caplog.clear()
        moved = {**LAST_MODIFIED, SDB_M: "Wed, 14 Oct 2026 09:00:00 GMT"}  # materials.json restamped, bytes identical
        assert run(tmp_path, last_modified=moved) == 0
        assert not mismatches(caplog)
        (line,) = [r.getMessage() for r in freshness_lines(caplog) if "source=spoolmandb_materials" in r.getMessage()]
        assert "status=redeployed" in line
        assert json.loads((tmp_path / "state.json").read_text())["freshness_mismatches"] == []

    def test_spoolmandb_content_change_under_same_last_modified_still_warns(self) -> None:
        prev = {"inputs": {"spoolmandb_filaments": ofd2spoolman.input_record(ofd2spoolman.Fetched(b"old", "A"))}}
        (result,) = ofd2spoolman.check_input_freshness(prev, {"spoolmandb_filaments": ofd2spoolman.Fetched(b"new", "A")})
        assert (result.status, result.moved) == ("mismatch", "sha256")

    def test_policy_is_per_source(self) -> None:
        prev = {"inputs": {n: ofd2spoolman.input_record(ofd2spoolman.Fetched(b"x", "A")) for n in ("ofd", "spoolmandb_filaments")}}
        results = ofd2spoolman.check_input_freshness(
            prev, {"ofd": ofd2spoolman.Fetched(b"x", "B"), "spoolmandb_filaments": ofd2spoolman.Fetched(b"x", "B")}
        )
        assert {r.source: r.status for r in results} == {"ofd": "mismatch", "spoolmandb_filaments": "redeployed"}

    @pytest.mark.parametrize(
        "inputs",
        [
            [],
            {"ofd": {"last_modified": "A"}},
            {"ofd": {"last_modified": "A", "sha256": 5}},
            {"ofd": "oops"},
            {"ofd": {"last_modified": 5, "sha256": "abc"}},
            None,
            {"ofd": None},
        ],
        ids=["list", "missing-sha256", "int-sha256", "string-entry", "int-last-modified", "null-inputs", "null-entry"],
    )
    def test_malformed_inputs_are_ignored(self, run: RunFn, tmp_path: Path, caplog: pytest.LogCaptureFixture, inputs: Any) -> None:
        write_state(tmp_path, run_at="2026-10-12T04:00:00+00:00", ofd_version="v0", inputs=inputs)
        caplog.set_level(logging.INFO)
        assert run(tmp_path, last_modified=LAST_MODIFIED) == 0
        assert not mismatches(caplog)
        assert "Traceback" not in caplog.text
        assert all(len(v["sha256"]) == 64 for v in json.loads((tmp_path / "state.json").read_text())["inputs"].values())

    def test_hash_is_taken_after_gunzip(self) -> None:
        """Two gzip encodings of the same JSON (different embedded mtime) must hash identically."""
        import gzip

        raw = b'{"version": "v1"}'
        a, b = gzip.compress(raw, mtime=1), gzip.compress(raw, mtime=2)
        assert a != b
        prev = {"inputs": {"ofd": ofd2spoolman.input_record(ofd2spoolman.Fetched(gzip.decompress(a), "A"))}}
        (result,) = ofd2spoolman.check_input_freshness(prev, {"ofd": ofd2spoolman.Fetched(gzip.decompress(b), "A")})
        assert result.status == "unchanged"

    def test_refused_run_leaves_state_byte_identical(self, run: RunFn, sources: dict[str, bytes], tmp_path: Path) -> None:
        self._first(run, tmp_path)
        before = (tmp_path / "state.json").read_bytes()
        live_before = (tmp_path / "filaments.json").read_bytes()
        shrunk = with_sdb(sources, filaments=[], materials=json.loads(sources[SDB_M]))
        moved = {k: v + "x" for k, v in LAST_MODIFIED.items()}
        assert run(tmp_path, payloads=shrunk, last_modified=moved) in (ofd2spoolman.EXIT_CATALOG_SHRANK, ofd2spoolman.EXIT_SOURCE_SHRANK)
        assert (tmp_path / "state.json").read_bytes() == before
        assert (tmp_path / "filaments.json").read_bytes() == live_before
        assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []

    def test_publish_sweeps_stale_state_temps(self, tmp_path: Path) -> None:
        stale, keep = tmp_path / ".state.json.ab12CD_9", tmp_path / ".state.json.short"
        stale.write_text("x")
        keep.write_text("x")
        ofd2spoolman.publish(tmp_path, {"filaments.json": [1], "materials.json": [2]})
        assert not stale.exists()
        assert keep.exists()
