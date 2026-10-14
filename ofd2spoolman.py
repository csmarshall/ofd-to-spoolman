#!/usr/bin/env python3
"""Build a Spoolman external-filament catalog from SpoolmanDB + the Open Filament Database.

Spoolman's "external" filament dropdown reads two files, `filaments.json` and
`materials.json`, from EXTERNAL_DB_URL (format: spoolman/externaldb.py,
ExternalFilament / ExternalMaterial). SpoolmanDB publishes exactly that, but only
covers ~67 brands. The Open Filament Database (OFD, MIT) covers ~176 but publishes
its own nested brand -> material -> filament -> variant -> size schema.

This script merges both into one SpoolmanDB-format catalog:
  * every SpoolmanDB entry is kept (verbatim apart from brand spelling being
    unified across sources; SpoolmanDB has the stricter data policy:
    measured spool weights, no generated values);
  * every OFD size row becomes one ExternalFilament, unless SpoolmanDB already has
    the same (manufacturer, material, colour, weight, diameter) — SpoolmanDB wins.

Published atomically into OUT_DIR, which a static web server (the `catalog`
sidecar in docker-compose.yml) serves to Spoolman.

Usage:
    ofd2spoolman.py [--out DIR] [--dry-run] [--include-discontinued]
Hidden (testing/CI): --from-ofd-file FILE --from-sdb-dir DIR build from local copies
of the OFD all.json and SpoolmanDB filaments/materials.json instead of the network.
Named so they don't share the --o prefix with --out (argparse abbreviations).

Env:
    LOG_LEVEL   DEBUG | INFO (default) | WARNING | ERROR
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

OFD_URL = "https://api.openfilamentdatabase.org/json/all.json.gz"
SPOOLMANDB_URL = "https://donkie.github.io/SpoolmanDB/"
DEFAULT_OUT = Path("/opt/sw/spoolman/catalog")
USER_AGENT = "ofd2spoolman (self-hosted Spoolman catalog builder)"

# Enum values accepted by Spoolman's ExternalFilament model.
SPOOLMAN_FINISHES = {"matte", "glossy"}
SPOOLMAN_PATTERNS = {"marble", "sparkle"}
HEX_RE = re.compile(r"^[0-9a-f]{6}([0-9a-f]{2})?$")

# Exit codes (also in the README). 0 and EXIT_PUBLISHED_WITH_WARNINGS are the only ones where
# the catalog was published; every other non-zero code left the live catalog untouched.
EXIT_FETCH_FAILED = 2
EXIT_DUPLICATE_IDS = 3
EXIT_CATALOG_SHRANK = 4
EXIT_SPOOLMANDB_NOT_SUPERSET = 5
EXIT_SOURCE_SHRANK = 6
EXIT_CORRUPT_BASELINE = 7
EXIT_PUBLISHED_WITH_WARNINGS = 10  # deliberately far from the failure codes: success, but look at the log

# A catalog (or one source) may lose at most half of its last good size. Shared by the whole-catalog
# guard and the per-source guards: the smallest legitimate drop we have seen is a few percent (OFD
# pruning discontinued items), while a broken export or an empty-array response loses ~100%, so any
# fraction between the two separates them; 50% was already the project's chosen point.
MIN_RETAINED_FRACTION = 0.5

# OFD rebuilds its export daily (~03:00 UTC). Three days = two missed rebuilds plus slack for a
# weekend, which is the figure the issue asked for; a single missed night is not worth a warning.
OFD_STALE_AFTER = timedelta(days=3)

STATE_FILE = "state.json"

log = logging.getLogger("ofd2spoolman")


def fetch(url: str) -> bytes:
    """Download a URL, transparently gunzipping `.gz` payloads."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    log.debug("fetched %s (%d bytes)", url, len(data))
    return gzip.decompress(data) if url.endswith(".gz") else data


def local_fetcher(ofd_file: Path, sdb_dir: Path) -> Callable[[str], bytes]:
    """Build a fetcher that serves the OFD export and SpoolmanDB files from disk (for tests and CI)."""

    def fetch_local(url: str) -> bytes:
        path = ofd_file if url == OFD_URL else sdb_dir / url.rsplit("/", 1)[1]
        return path.read_bytes()

    return fetch_local


def norm(text: str) -> str:
    """Normalise a name for duplicate detection: lowercase alphanumerics plus '+'.

    '+' is kept because it is meaningful in filament lines: PETG+ is not PETG.
    """
    return re.sub(r"[^a-z0-9+]", "", text.lower())


def norm_hex(value: str | None) -> str | None:
    """Return a Spoolman-style hex (lowercase, no '#') or None if malformed."""
    if not value:
        return None
    h = value.strip().lstrip("#").lower()
    return h if HEX_RE.match(h) else None


def pick_temp(lo: int | None, hi: int | None) -> int | None:
    """Collapse an OFD min/max range into Spoolman's single temperature.

    Uses the midpoint when both ends are real. OFD often records a bed minimum of 0
    meaning "unheated is fine", which would drag a midpoint to nonsense, so a zero
    end is ignored rather than averaged.
    """
    ends = [t for t in (lo, hi) if t]
    if not ends:
        return None
    return round(sum(ends) / len(ends))


def display_name(filament_name: str, brand_name: str, material: str, colour: str) -> str:
    """Build a SpoolmanDB-style name: product line + colour, minus brand and bare material.

    OFD filament names often repeat the brand ("123-3D Filament PLA"); Spoolman shows
    manufacturer and material in their own columns, so strip a leading brand name.
    The material is dropped only when it is the whole remaining line, so product lines
    like "PETG+" or "PLA Matte" survive intact.
    """
    line = filament_name.strip()
    if re.match(rf"{re.escape(brand_name)}(?=[\s_-]|$)", line, re.IGNORECASE):
        line = line[len(brand_name) :].strip(" -_")
    if norm(line) == norm(material):
        line = ""
    return f"{line} {colour}".strip() if line else colour


def convert_ofd(ofd: dict[str, Any], include_discontinued: bool) -> list[dict[str, Any]]:
    """Flatten OFD into one Spoolman ExternalFilament dict per size row."""
    brands = {b["id"]: b for b in ofd["brands"]}
    filaments = {f["id"]: f for f in ofd["filaments"]}
    variants = {v["id"]: v for v in ofd["variants"]}
    out: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for size in ofd["sizes"]:
        variant = variants.get(size["variant_id"])
        fil = filaments.get(variant["filament_id"]) if variant else None
        brand = brands.get(fil["brand_id"]) if fil else None
        if not (variant and fil and brand):
            skip("dangling reference")
            continue
        if not include_discontinued and (size.get("discontinued") or variant.get("discontinued") or fil.get("discontinued")):
            skip("discontinued")
            continue
        if not fil.get("density") or not size.get("filament_weight") or not size.get("diameter"):
            skip("missing density/weight/diameter")
            continue

        traits = {k for k, v in (variant.get("traits") or {}).items() if v}
        name = display_name(fil["name"], brand["name"], fil["material"], variant["name"])
        if size.get("spool_refill"):
            name += " (refill)"

        finish = "matte" if "matte" in traits else None
        pattern = "marble" if "imitates_marble" in traits else "sparkle" if "glitter" in traits else None
        entry: dict[str, Any] = {
            "id": f"ofd_{size['id']}",
            "manufacturer": brand["name"],
            "name": name,
            "material": fil["material"],
            "density": float(fil["density"]),
            "weight": float(size["filament_weight"]),
            "spool_weight": float(size["empty_spool_weight"]) if size.get("empty_spool_weight") else None,
            "spool_type": None,
            "diameter": float(size["diameter"]),
            "color_hex": norm_hex(variant.get("color_hex")),
            "color_hexes": None,
            "extruder_temp": pick_temp(fil.get("min_print_temperature"), fil.get("max_print_temperature")),
            "bed_temp": pick_temp(fil.get("min_bed_temperature"), fil.get("max_bed_temperature")),
            "finish": finish if finish in SPOOLMAN_FINISHES else None,
            "multi_color_direction": None,
            "pattern": pattern if pattern in SPOOLMAN_PATTERNS else None,
            "translucent": bool(traits & {"translucent", "transparent"}),
            "glow": "glow" in traits,
        }
        out.append(entry)

    log.info("OFD: %d sizes -> %d filaments; skipped %s", len(ofd["sizes"]), len(out), skipped or "none")
    return out


def dedupe_key(f: dict[str, Any]) -> tuple[str, str, str, float, float]:
    """Identity used to decide that an OFD entry duplicates a SpoolmanDB one."""
    return (norm(f["manufacturer"]), norm(f["material"]), norm(f["name"]), float(f["weight"]), float(f["diameter"]))


def median_int(entries: list[dict[str, Any]], key: str) -> int | None:
    """Median of a numeric field across entries, ignoring missing/zero values."""
    vals = [e[key] for e in entries if e.get(key)]
    return round(statistics.median(vals)) if vals else None


def merge_materials(sdb_materials: list[dict[str, Any]], filaments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep SpoolmanDB's materials; add any material only OFD knows, with median density/temps."""
    known = {norm(m["material"]) for m in sdb_materials}
    by_material: dict[str, list[dict[str, Any]]] = {}
    for f in filaments:
        if norm(f["material"]) not in known:
            by_material.setdefault(f["material"], []).append(f)
    added = []
    for material, fs in sorted(by_material.items()):
        added.append(
            {
                "material": material,
                "density": round(statistics.median(f["density"] for f in fs), 3),
                "extruder_temp": median_int(fs, "extruder_temp"),
                "bed_temp": median_int(fs, "bed_temp"),
            }
        )
    log.info("materials: %d from SpoolmanDB + %d OFD-only", len(sdb_materials), len(added))
    return sdb_materials + added


def _sweep_stale_temps(out_dir: Path, names: Sequence[str]) -> None:
    """Remove temp/backup files a previously SIGKILLed publish left behind (exact mkstemp pattern only)."""
    for name in names:
        stale = re.compile(rf"^\.{re.escape(name)}\.[A-Za-z0-9_]{{8}}(\.old)?$")
        for entry in out_dir.iterdir():
            if stale.match(entry.name):
                try:
                    entry.unlink()
                    log.warning("removed stale leftover %s", entry)
                except OSError as exc:
                    log.warning("could not remove stale leftover %s: %s", entry, exc)


def publish(out_dir: Path, files: dict[str, Any]) -> None:
    """Publish the files as a pair: stage every temp file completely, then rename them back to back.

    Two renames are still not one atomic operation. If the process dies between them the pair is
    mismatched; if the second rename fails (or is interrupted), the first file is restored from a
    hard link to its previous inode taken just before the renames (best effort, logged either way).
    A file that was live is never deleted: if it cannot be restored, the new one stays and ERROR says so.
    Files are renamed in REVERSE of the given order: callers pass filaments first, so materials
    land first. Spoolman resolves each filament's material by name, and new materials with the old
    filaments only works for materials the old filaments still reference (merge_materials regenerates
    the set), whereas new filaments pointing at materials the old materials.json lacks would fail.
    So this order is the less bad one, not a guarantee.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _sweep_stale_temps(out_dir, list(files))
    staged: dict[str, str] = {}  # name -> temp path
    backups: dict[str, str] = {}  # name -> hard link to the previous file
    had_previous: dict[str, bool] = {}
    try:
        for name, payload in files.items():
            fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{name}.")
            staged[name] = tmp
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, separators=(",", ":"))
            os.chmod(tmp, 0o644)

        for name in staged:
            backup = f"{staged[name]}.old"
            try:
                os.link(out_dir / name, backup)
                backups[name] = backup
                had_previous[name] = True
            except FileNotFoundError:
                had_previous[name] = False  # first-ever publish: nothing to restore
            except OSError as exc:
                had_previous[name] = True  # a live file exists but cannot be backed up
                log.warning("cannot back up %s (%s); a failed publish will not be rolled back", out_dir / name, exc)

        renamed: list[str] = []
        for name in reversed(list(staged)):
            try:
                os.replace(staged[name], out_dir / name)
            except BaseException:
                if renamed:
                    _restore_after_partial_publish(out_dir, renamed, backups, had_previous, name)
                raise
            staged.pop(name)
            renamed.append(name)
        for name, payload in files.items():
            log.info("published %s (%d entries)", out_dir / name, len(payload))
    finally:
        for leftover in [*staged.values(), *backups.values()]:
            try:
                os.unlink(leftover)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("could not remove leftover %s: %s", leftover, exc)


def _restore_after_partial_publish(
    out_dir: Path, renamed: list[str], backups: dict[str, str], had_previous: dict[str, bool], failed: str
) -> None:
    """The pair is half-published: put the already-renamed files back, or log exactly what state remains.

    A backup that could not be restored is dropped from `backups` so cleanup leaves it on disk for recovery.
    """
    for name in renamed:
        try:
            if name in backups:
                os.replace(backups[name], out_dir / name)
                backups.pop(name)
            elif not had_previous[name]:
                os.unlink(out_dir / name)  # first-ever publish: no previous file to go back to
            else:
                raise OSError("no backup of the previous file was possible")
        except OSError as exc:
            kept = backups.pop(name, None)
            log.error(
                "publish of %s failed after %s was already replaced and could not be restored (%s): "
                "the catalog pair is now a MISMATCH (new %s, old %s); rerun to repair%s",
                failed,
                name,
                exc,
                name,
                failed,
                f"; previous {name} kept at {kept}" if kept else "",
            )
            return
    log.error("publish of %s failed after %s was replaced; %s restored to its previous version", failed, renamed, renamed)


class CorruptBaseline(Exception):
    """The published filaments.json exists but cannot serve as a shrink-guard baseline."""


def previous_count(out_dir: Path) -> int | None:
    """Entry count of the currently published filaments.json.

    None only when no catalog was ever published (the file does not exist).
    Raises CorruptBaseline when it exists but is unreadable, unparseable, not a
    JSON list, or an empty list: publishing over it would bypass the shrink guard.
    """
    path = out_dir / "filaments.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CorruptBaseline(f"cannot read {path}: {exc}") from exc
    try:
        published = json.loads(raw.decode())  # UnicodeDecodeError is a ValueError
    except (ValueError, RecursionError) as exc:
        raise CorruptBaseline(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(published, list):
        raise CorruptBaseline(f"{path} is not a JSON list")
    if not published:
        raise CorruptBaseline(f"{path} is an empty list")
    return len(published)


def below_floor(count: int, last_good: int | None) -> bool:
    """True when `count` fell under MIN_RETAINED_FRACTION of the last good size (no baseline: False)."""
    return bool(last_good) and count < int(last_good * MIN_RETAINED_FRACTION)  # type: ignore[operator]


def check_spoolmandb_superset(
    sdb_filaments: list[dict[str, Any]],
    sdb_materials: list[dict[str, Any]],
    filaments: list[dict[str, Any]],
    materials: list[dict[str, Any]],
) -> list[str]:
    """Problems (empty = fine) found comparing raw SpoolmanDB data with the merged output.

    EXTERNAL_DB_URL replaces Spoolman's default SpoolmanDB, so anything we lose is gone from the
    dropdown. The snapshot must predate the brand-spelling rewrite: manufacturer is compared by
    norm(), every other field exactly.
    """
    problems: list[str] = []
    out_by_id = {f["id"]: f for f in filaments}
    for want in sdb_filaments:
        got = out_by_id.get(want["id"])
        if got is None:
            problems.append(f"SpoolmanDB filament {want['id']} missing from output")
            continue
        for key in sorted(set(want) | set(got)):
            same = want.get(key) == got.get(key)
            if key == "manufacturer":
                same = norm(want.get(key) or "") == norm(got.get(key) or "")
            if not same:
                problems.append(f"SpoolmanDB filament {want['id']} field {key!r}: {want.get(key)!r} -> {got.get(key)!r}")
    out_materials = {m["material"]: m for m in materials}
    for want in sdb_materials:
        got = out_materials.get(want["material"])
        if got is None:
            problems.append(f"SpoolmanDB material {want['material']!r} missing from output")
        elif got != want:
            problems.append(f"SpoolmanDB material {want['material']!r} changed: {want!r} -> {got!r}")
    return problems


def load_state(out_dir: Path) -> dict[str, Any]:
    """The state recorded by the last successful publish, or {} if none/unreadable."""
    try:
        state = json.loads((out_dir / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def spoolmandb_hash(sdb_filaments: list[dict[str, Any]], sdb_materials: list[dict[str, Any]]) -> str:
    """Content hash of the raw SpoolmanDB data (key order and whitespace independent)."""
    canonical = json.dumps([sdb_filaments, sdb_materials], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (a trailing Z is accepted) into an aware datetime, else None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def check_source_shrink(prev_state: dict[str, Any], sdb_count: int, ofd_count: int) -> list[str]:
    """Problems for each source that fell under MIN_RETAINED_FRACTION of its last good count."""
    last = prev_state.get("counts") or {}
    problems = []
    for label, now_count, key in (("SpoolmanDB", sdb_count, "spoolmandb_filaments"), ("OFD", ofd_count, "ofd_filaments")):
        if below_floor(now_count, last.get(key)):
            problems.append(f"{label} returned {now_count} filaments vs {last[key]} on the last good fetch")
    return problems


def build_state(
    prev_state: dict[str, Any],
    now: datetime,
    ofd: dict[str, Any],
    sdb_filaments: list[dict[str, Any]],
    sdb_materials: list[dict[str, Any]],
    ofd_count: int,
) -> dict[str, Any]:
    """The state to record after a successful publish; *_changed_at only moves when the value does."""
    stamp = now.isoformat(timespec="seconds")
    version = ofd.get("version")
    content_hash = spoolmandb_hash(sdb_filaments, sdb_materials)
    version_same = prev_state.get("ofd_version") == version and prev_state.get("ofd_version_changed_at")
    hash_same = prev_state.get("spoolmandb_hash") == content_hash and prev_state.get("spoolmandb_changed_at")
    return {
        "run_at": stamp,
        "ofd_version": version,
        "ofd_generated_at": ofd.get("generated_at"),
        "ofd_version_changed_at": prev_state["ofd_version_changed_at"] if version_same else stamp,
        "spoolmandb_hash": content_hash,
        "spoolmandb_changed_at": prev_state["spoolmandb_changed_at"] if hash_same else stamp,
        "counts": {
            "spoolmandb_filaments": len(sdb_filaments),
            "spoolmandb_materials": len(sdb_materials),
            "ofd_filaments": ofd_count,
        },
    }


def check_ofd_stalled(ofd: dict[str, Any], prev_state: dict[str, Any], now: datetime) -> list[str]:
    """Warnings if OFD's export has not advanced for OFD_STALE_AFTER.

    OFD's own `generated_at` is authoritative when present. Without it we can only fall back to
    when this tool first saw the current `version`.
    """
    generated = parse_timestamp(ofd.get("generated_at"))
    if generated is not None:
        age, basis = now - generated, f"generated_at {ofd['generated_at']}"
    elif prev_state.get("ofd_version") == ofd.get("version") and parse_timestamp(prev_state.get("ofd_version_changed_at")):
        age, basis = now - parse_timestamp(prev_state["ofd_version_changed_at"]), f"version {ofd.get('version')} unchanged since first seen"  # type: ignore[operator]
    else:
        return []
    if age > OFD_STALE_AFTER:
        return [f"OFD looks stalled: {basis} is {age.days} days old (limit {OFD_STALE_AFTER.days})"]
    return []


def write_state(out_dir: Path, state: dict[str, Any]) -> None:
    """Write the state file atomically (temp file in the same dir, then rename)."""
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{STATE_FILE}.")
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh, indent=2)
    os.chmod(tmp, 0o644)
    os.replace(tmp, out_dir / STATE_FILE)


def main(
    argv: Sequence[str] | None = None,
    fetcher: Callable[[str], bytes] | None = None,
    now: datetime | None = None,
) -> int:
    """Fetch both sources, merge, sanity-check, and publish.

    `argv` defaults to sys.argv[1:]; `fetcher` replaces the network download and `now` the clock (both used by tests).
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default {DEFAULT_OUT})")
    ap.add_argument("--dry-run", action="store_true", help="build and report, but write nothing")
    ap.add_argument("--include-discontinued", action="store_true", help="keep OFD products marked discontinued")
    # Hidden: read the sources from disk instead of the network (CI schema job).
    ap.add_argument("--from-ofd-file", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--from-sdb-dir", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if (args.from_ofd_file is None) != (args.from_sdb_dir is None):
        ap.error("--from-ofd-file and --from-sdb-dir must be given together")
    if args.from_ofd_file is not None:
        fetcher = local_fetcher(args.from_ofd_file, args.from_sdb_dir)
    fetch_source = fetcher or fetch
    now = now or datetime.now(UTC)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        sdb_filaments = json.loads(fetch_source(SPOOLMANDB_URL + "filaments.json"))
        sdb_materials = json.loads(fetch_source(SPOOLMANDB_URL + "materials.json"))
        ofd = json.loads(fetch_source(OFD_URL))
    except Exception as exc:  # network / parse failure: keep the last good catalog
        log.error("fetch failed, leaving existing catalog untouched: %s", exc)
        return EXIT_FETCH_FAILED
    log.info("SpoolmanDB: %d filaments, %d materials; OFD dataset %s", len(sdb_filaments), len(sdb_materials), ofd.get("version"))

    ofd_filaments = convert_ofd(ofd, args.include_discontinued)
    sdb_raw = copy.deepcopy((sdb_filaments, sdb_materials))  # before the brand-spelling rewrite below

    # One spelling per brand in the dropdown ("SUNLU" vs "Sunlu"): the first seen
    # wins, and SpoolmanDB is seen first.
    canonical: dict[str, str] = {}
    for f in sdb_filaments + ofd_filaments:
        f["manufacturer"] = canonical.setdefault(norm(f["manufacturer"]), f["manufacturer"])

    sdb_keys = {dedupe_key(f) for f in sdb_filaments}
    unique_ofd = [f for f in ofd_filaments if dedupe_key(f) not in sdb_keys]
    log.info("dedupe: %d OFD entries already in SpoolmanDB, %d added", len(ofd_filaments) - len(unique_ofd), len(unique_ofd))

    filaments = sdb_filaments + unique_ofd
    ids = [f["id"] for f in filaments]
    if len(ids) != len(set(ids)):
        log.error("duplicate ids in merged catalog; refusing to publish")
        return EXIT_DUPLICATE_IDS

    # Guard against a broken upstream silently emptying the dropdown: refuse to
    # shrink below half of what is currently published.
    try:
        prev = previous_count(args.out)
    except CorruptBaseline as exc:
        log.error(
            "published catalog is unusable as a baseline, refusing to publish: %s; if it is really bad, delete it to allow a fresh publish",
            exc,
        )
        return EXIT_CORRUPT_BASELINE
    if below_floor(len(filaments), prev):
        log.error("merged catalog has %d entries vs %d published; refusing to shrink by >50%%", len(filaments), prev)
        return EXIT_CATALOG_SHRANK

    materials = merge_materials(sdb_materials, filaments)
    brands = len({f["manufacturer"] for f in filaments})
    log.info("merged: %d filaments across %d manufacturers", len(filaments), brands)

    # Drift checks. Hard guards first; they also run on --dry-run because they only read.
    prev_state = load_state(args.out)
    missing = check_spoolmandb_superset(sdb_raw[0], sdb_raw[1], filaments, materials)
    for problem in missing:
        log.error("coverage: %s", problem)
    if missing:
        log.error("output is not a superset of SpoolmanDB; refusing to publish")
        return EXIT_SPOOLMANDB_NOT_SUPERSET
    shrunk = check_source_shrink(prev_state, len(sdb_filaments), len(ofd_filaments))
    for problem in shrunk:
        log.error("source shrink: %s", problem)
    if shrunk:
        log.error("a source shrank by more than %d%%; refusing to publish", round((1 - MIN_RETAINED_FRACTION) * 100))
        return EXIT_SOURCE_SHRANK
    warnings = check_ofd_stalled(ofd, prev_state, now)
    for warning in warnings:
        log.warning("%s", warning)
    exit_code = EXIT_PUBLISHED_WITH_WARNINGS if warnings else 0

    if args.dry_run:
        log.info("dry run: nothing written")
        return exit_code
    publish(args.out, {"filaments.json": filaments, "materials.json": materials})
    write_state(args.out, build_state(prev_state, now, ofd, sdb_raw[0], sdb_raw[1], len(ofd_filaments)))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
