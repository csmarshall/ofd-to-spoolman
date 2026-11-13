#!/usr/bin/env python3
"""Build a Spoolman external-filament catalog from the Open Filament Database (OFD) + SpoolmanDB.

Spoolman's "external" filament dropdown reads two files, `filaments.json` and
`materials.json`, from EXTERNAL_DB_URL (format: spoolman/externaldb.py,
ExternalFilament / ExternalMaterial). SpoolmanDB publishes exactly that, but only
covers ~67 brands. OFD publishes the same format itself (spoolman/filaments.json and
spoolman/materials.json, plus manifest.json with the dataset version).

This script merges both into one catalog (docs/adr/0009: OFD wins on overlap):
  * every OFD filament and material is kept exactly as OFD published it (apart from
    brand spelling being unified across sources, OFD seen first);
  * every SpoolmanDB filament is kept unless OFD has an entry with the same
    (manufacturer, material, name, weight, diameter) after normalisation, and every
    SpoolmanDB material OFD does not list is kept.

Published atomically into OUT_DIR, which a static web server (the `catalog`
sidecar in docker-compose.yml) serves to Spoolman.

Usage:
    ofd2spoolman.py --out DIR [--dry-run]
Hidden (testing/CI): --from-ofd-dir DIR --from-sdb-dir DIR build from local copies
of OFD's manifest/filaments/materials.json and SpoolmanDB's filaments/materials.json
instead of the network. Named so they don't share the --o prefix with --out (argparse abbreviations).
`--include-discontinued` is accepted and ignored: OFD's published catalog keeps discontinued products.

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
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

OFD_BASE_URL = "https://api.openfilamentdatabase.org/"
OFD_FILAMENTS_URL = OFD_BASE_URL + "spoolman/filaments.json"
OFD_MATERIALS_URL = OFD_BASE_URL + "spoolman/materials.json"
OFD_MANIFEST_URL = OFD_BASE_URL + "manifest.json"
SPOOLMANDB_URL = "https://donkie.github.io/SpoolmanDB/"
USER_AGENT = "ofd2spoolman (self-hosted Spoolman catalog builder)"

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

# OFD rebuilds its export daily (~03:00 UTC; manifest.json's generated_at). Three days = two missed rebuilds plus slack for a
# weekend, which is the figure the issue asked for; a single missed night is not worth a warning.
OFD_STALE_AFTER = timedelta(days=3)

STATE_FILE = "state.json"

log = logging.getLogger("ofd2spoolman")


class Fetched(NamedTuple):
    """One downloaded input: the (gunzipped) payload and the HTTP Last-Modified header, if the server sent one."""

    data: bytes
    last_modified: str | None


def fetch(url: str) -> Fetched:
    """Download a URL, transparently gunzipping `.gz` payloads, and keep its Last-Modified header."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
        last_modified = resp.headers.get("Last-Modified")
    log.debug("fetched %s (%d bytes, Last-Modified %s)", url, len(data), last_modified)
    return Fetched(gzip.decompress(data) if url.endswith(".gz") else data, last_modified)


def local_fetcher(ofd_dir: Path, sdb_dir: Path) -> Callable[[str], Fetched]:
    """Build a fetcher that serves the OFD catalog files and SpoolmanDB files from disk (for tests and CI); there is no Last-Modified."""

    def fetch_local(url: str) -> Fetched:
        base = ofd_dir if url.startswith(OFD_BASE_URL) else sdb_dir
        return Fetched((base / url.rsplit("/", 1)[1]).read_bytes(), None)

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


# Spoolman's maximum filament name length: FilamentParameters.name max_length=64 in spoolman/api/v1/filament.py
# and Filament.name String(64) in spoolman/database/models.py (checked against v0.27.0).
NAME_MAX_LENGTH = 64


def warn_over_long_fixed_names(records: Sequence[dict[str, Any]], source: str = "SpoolmanDB") -> None:
    """Both inputs are passed through unchanged (OFD's exporter owns its names), so an over-long name is reported, not altered."""
    too_long = [r for r in records if len(r["name"]) > NAME_MAX_LENGTH]
    for record in too_long:
        log.warning(
            "%s name exceeds Spoolman's %d-character limit and is passed through unchanged: %s %r",
            source,
            NAME_MAX_LENGTH,
            record["id"],
            record["name"],
        )


def dedupe_key(f: dict[str, Any]) -> tuple[str, str, str, float, float]:
    """Identity used to decide that a SpoolmanDB entry duplicates an OFD one."""
    return (norm(f["manufacturer"]), norm(f["material"]), norm(f["name"]), float(f["weight"]), float(f["diameter"]))


def validate_catalog(name: str, data: Any, required: Sequence[str]) -> list[dict[str, Any]]:
    """The parsed OFD catalog file as a non-empty list of dicts that carry the `required` keys, else ValueError.

    OFD's records are published as they are, so this is the only place a malformed upstream is caught before the
    merge indexes into them; the caller treats the ValueError as a failed fetch.
    """
    if not isinstance(data, list) or not data:
        raise ValueError(f"OFD {name} is not a non-empty JSON list")
    for index, record in enumerate(data):
        if not isinstance(record, dict) or any(key not in record for key in required):
            raise ValueError(f"OFD {name}[{index}] is not an object with {list(required)}")
    return data


FILAMENT_KEYS = ("id", "manufacturer", "name", "material", "weight", "diameter")
MATERIAL_KEYS = ("material",)


def merge_filaments(ofd_filaments: list[dict[str, Any]], sdb_filaments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ADR 0009: OFD's records as published, plus every SpoolmanDB record whose dedupe_key OFD does not have.

    Brand spelling is unified in place (first seen wins, OFD seen first), the one change made to either side.
    """
    canonical: dict[str, str] = {}
    for f in ofd_filaments + sdb_filaments:
        f["manufacturer"] = canonical.setdefault(norm(f["manufacturer"]), f["manufacturer"])
    ofd_keys = {dedupe_key(f) for f in ofd_filaments}
    sdb_only = [f for f in sdb_filaments if dedupe_key(f) not in ofd_keys]
    log.info(
        "merge: %d OFD filaments + %d SpoolmanDB-only (%d SpoolmanDB entries covered by OFD and dropped)",
        len(ofd_filaments),
        len(sdb_only),
        len(sdb_filaments) - len(sdb_only),
    )
    return ofd_filaments + sdb_only


def sdb_only_materials(ofd_materials: list[dict[str, Any]], sdb_materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SpoolmanDB materials whose name (norm(), so PETG+ is not PETG) OFD's materials file does not have."""
    known = {norm(m["material"]) for m in ofd_materials}
    return [m for m in sdb_materials if norm(m["material"]) not in known]


def merge_materials(ofd_materials: list[dict[str, Any]], sdb_materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OFD's materials as published, plus the SpoolmanDB materials OFD does not have."""
    extra = sdb_only_materials(ofd_materials, sdb_materials)
    log.info("materials: %d from OFD + %d SpoolmanDB-only", len(ofd_materials), len(extra))
    return ofd_materials + extra


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


def serialise(records: list[Any]) -> str:
    """One compact JSON record per line (still a valid JSON array), so git diffs of the catalog are readable.

    Deterministic: the same records always give the same bytes, which keeps committed history free of noise.
    """
    if not records:
        return "[]\n"
    return "[\n" + ",\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n]\n"


def publish(out_dir: Path, files: dict[str, Any]) -> dict[str, dict[str, Any]]:
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

    Returns {name: {"count", "sha256"}} for the bytes written, for the state file's provenance record.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _sweep_stale_temps(out_dir, [*files, STATE_FILE])
    staged: dict[str, str] = {}  # name -> temp path
    backups: dict[str, str] = {}  # name -> hard link to the previous file
    outputs: dict[str, dict[str, Any]] = {}
    had_previous: dict[str, bool] = {}
    try:
        for name, payload in files.items():
            fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{name}.")
            staged[name] = tmp
            data = serialise(payload).encode()
            outputs[name] = {"count": len(payload), "sha256": hashlib.sha256(data).hexdigest()}
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
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
        return outputs
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


def same_value(a: Any, b: Any) -> bool:
    """Type-aware JSON equality: False is not 0, 1 is not 1.0 (plain == conflates them)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def check_spoolmandb_superset(
    sdb_filaments: list[dict[str, Any]],
    sdb_materials: list[dict[str, Any]],
    ofd_filaments: list[dict[str, Any]],
    ofd_materials: list[dict[str, Any]],
    filaments: list[dict[str, Any]],
    materials: list[dict[str, Any]],
) -> list[str]:
    """Problems (empty = fine) found comparing the raw inputs with the merged output (ADR 0009).

    EXTERNAL_DB_URL replaces Spoolman's default SpoolmanDB, so anything we lose is gone from the dropdown, except what
    OFD deliberately replaces. A SpoolmanDB filament OFD does not cover (no OFD record with its dedupe_key) must be in
    the output unchanged; one OFD does cover must be accounted for by an OFD record with the same dedupe_key in the
    output. A SpoolmanDB material OFD's materials lack must be in the output unchanged. The snapshots must predate
    the brand-spelling rewrite: manufacturer is compared by norm(), every other field exactly.
    """
    problems: list[str] = []
    out_by_id = {f["id"]: f for f in filaments}
    ofd_ids = {f["id"] for f in ofd_filaments}
    ofd_keys = {dedupe_key(f) for f in ofd_filaments}
    out_ofd_keys = {dedupe_key(f) for f in filaments if f["id"] in ofd_ids}
    for want in sdb_filaments:
        if dedupe_key(want) in ofd_keys:
            if dedupe_key(want) not in out_ofd_keys:
                problems.append(f"SpoolmanDB filament {want['id']} is covered by OFD but no OFD record with its key is in the output")
            continue
        got = out_by_id.get(want["id"])
        if got is None:
            problems.append(f"SpoolmanDB filament {want['id']} missing from output")
            continue
        for key in sorted(set(want) | set(got)):
            same = same_value(want.get(key), got.get(key))
            if key == "manufacturer":
                same = norm(want.get(key) or "") == norm(got.get(key) or "")
            if not same:
                problems.append(f"SpoolmanDB filament {want['id']} field {key!r}: {want.get(key)!r} -> {got.get(key)!r}")
    # Materials as a multiset: duplicate names must each find their own identical output entry.
    remaining = list(materials)
    ofd_names = {norm(m["material"]) for m in ofd_materials}
    for want in sdb_materials:
        if norm(want["material"]) in ofd_names:
            continue
        match = next((i for i, got in enumerate(remaining) if same_value(want, got)), None)
        if match is not None:
            del remaining[match]
            continue
        if any(got.get("material") == want["material"] for got in materials):
            problems.append(f"SpoolmanDB material {want['material']!r} changed or duplicated differently in output: {want!r}")
        else:
            problems.append(f"SpoolmanDB material {want['material']!r} missing from output")
    return problems


def load_state(out_dir: Path) -> dict[str, Any]:
    """The state recorded by the last successful publish, with anything malformed dropped.

    A corrupt or old-schema file must never crash a run or plant a bogus shrink baseline, so each
    piece that fails validation is treated as absent (with a WARNING) rather than trusted.
    """
    try:
        raw = json.loads((out_dir / STATE_FILE).read_text())
    except OSError:
        return {}
    except ValueError:
        log.warning("%s is not valid JSON; ignoring it", STATE_FILE)
        return {}
    if not isinstance(raw, dict):
        log.warning("%s is not a JSON object; ignoring it", STATE_FILE)
        return {}
    state: dict[str, Any] = {}
    for key in ("run_at", "ofd_version", "ofd_generated_at", "ofd_version_changed_at", "spoolmandb_hash", "spoolmandb_changed_at"):
        if isinstance(raw.get(key), str):
            state[key] = raw[key]
        elif key in raw:
            log.warning("%s: %r is not a string; ignoring it", STATE_FILE, key)
    inputs = load_recorded_inputs(raw.get("inputs"))
    if inputs:
        state["inputs"] = inputs
    elif "inputs" in raw:
        log.warning("%s: 'inputs' has no usable entries; freshness will be treated as a first run", STATE_FILE)
    counts = raw.get("counts")
    if isinstance(counts, dict) and all(type(v) is int and v > 0 for v in counts.values()):
        state["counts"] = counts
    elif "counts" in raw:
        log.warning("%s: 'counts' is not a mapping of positive integers; ignoring all recorded counts", STATE_FILE)
    return state


def load_recorded_inputs(raw: Any) -> dict[str, dict[str, str | None]]:
    """The per-input {last_modified, sha256} records that are well-formed; anything else is dropped."""
    if not isinstance(raw, dict):
        return {}
    inputs: dict[str, dict[str, str | None]] = {}
    for name, entry in raw.items():
        if isinstance(entry, dict) and isinstance(entry.get("sha256"), str) and isinstance(entry.get("last_modified"), str | None):
            inputs[name] = {"last_modified": entry["last_modified"], "sha256": entry["sha256"]}
    return inputs


# Per-source freshness policy: inputs whose Last-Modified is the host's DEPLOY time, not the time the file's
# own content changed. GitHub Pages (SpoolmanDB) restamps every file on each deploy, so materials.json gets a
# new Last-Modified, with identical bytes, whenever filaments.json changes. For these a Last-Modified-only
# move is expected (status "redeployed", INFO); only changed content under an unchanged Last-Modified is a
# mismatch. OFD's two catalog files are listed on the evidence that they carry the build's deploy time (their
# Last-Modified is a second after manifest.json's, and a rebuild with unchanged data would restamp them); the
# manifest's content changes every build, so it is not listed and warns on either one-sided move.
DEPLOY_STAMPED_SOURCES = frozenset({"spoolmandb_filaments", "spoolmandb_materials", "ofd_filaments", "ofd_materials"})


class FreshnessResult(NamedTuple):
    """Outcome of comparing one input with the previous successful run.

    status: first (nothing to compare), updated (both moved), unchanged (neither moved),
    unverifiable (no Last-Modified on one side, so only the content hash is known), redeployed (only Last-Modified
    moved, on a DEPLOY_STAMPED_SOURCES input: expected), mismatch (exactly one moved, and worth a warning).
    """

    source: str
    status: str
    moved: str | None  # for a mismatch: "last_modified" or "sha256", whichever moved alone
    message: str
    previous: dict[str, str | None]
    current: dict[str, str | None]

    @property
    def mismatch(self) -> bool:
        return self.status == "mismatch"


def input_record(fetched: Fetched) -> dict[str, str | None]:
    """The persisted shape of one input; maps onto the planned manifest (#24) as `last_modified` and `sha256`."""
    return {"last_modified": fetched.last_modified, "sha256": hashlib.sha256(fetched.data).hexdigest()}


def check_input_freshness(prev_state: dict[str, Any], fetched: dict[str, Fetched]) -> list[FreshnessResult]:
    """Compare each input's Last-Modified header and content hash with the last successful run.

    A header that moves while the bytes are identical (or bytes that change under the same header) means the
    upstream is not behaving the way this tool assumes; it is reported, never blocking.
    """
    results = []
    recorded = prev_state.get("inputs") or {}
    for name, item in fetched.items():
        cur = input_record(item)
        prev = recorded.get(name)
        if prev is None:
            status, moved, detail = "first", None, "no previous run to compare with"
        else:
            hash_moved = prev["sha256"] != cur["sha256"]
            if prev["last_modified"] is None or cur["last_modified"] is None:
                status, moved = "unverifiable", None
                detail = f"no Last-Modified to compare (content {'changed' if hash_moved else 'identical'})"
            else:
                lm_moved = prev["last_modified"] != cur["last_modified"]
                if lm_moved and hash_moved:
                    status, moved, detail = "updated", None, "Last-Modified and content both moved"
                elif not lm_moved and not hash_moved:
                    status, moved, detail = "unchanged", None, "Last-Modified and content both identical"
                elif lm_moved and name in DEPLOY_STAMPED_SOURCES:
                    status, moved = "redeployed", None
                    detail = "Last-Modified moved over identical content; expected, the host stamps files with its deploy time"
                else:
                    status, moved = "mismatch", "last_modified" if lm_moved else "sha256"
                    other = "content hash" if lm_moved else "Last-Modified"
                    detail = f"{moved} moved but {other} did not"
        before = prev or {"last_modified": None, "sha256": None}
        fields = (
            f"last_modified={before['last_modified']}->{cur['last_modified']} "
            f"sha256={(before['sha256'] or '')[:12]}->{str(cur['sha256'])[:12]}"
        )
        tag = "FRESHNESS-MISMATCH" if status == "mismatch" else "FRESHNESS"
        message = f"{tag} source={name} " + (f"moved={moved} " if moved else f"status={status} ") + f"({detail}) {fields}"
        results.append(FreshnessResult(name, status, moved, message, prev or {}, cur))
    return results


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
    manifest: dict[str, Any],
    sdb_filaments: list[dict[str, Any]],
    sdb_materials: list[dict[str, Any]],
    ofd_count: int,
    freshness: list[FreshnessResult],
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """The state to record after a successful publish; *_changed_at only moves when the value does."""
    stamp = now.isoformat(timespec="seconds")
    version = manifest.get("dataset_version")
    content_hash = spoolmandb_hash(sdb_filaments, sdb_materials)
    version_same = prev_state.get("ofd_version") == version and prev_state.get("ofd_version_changed_at")
    hash_same = prev_state.get("spoolmandb_hash") == content_hash and prev_state.get("spoolmandb_changed_at")
    return {
        "run_at": stamp,
        "ofd_version": version,
        "ofd_generated_at": manifest.get("generated_at"),
        "ofd_version_changed_at": prev_state["ofd_version_changed_at"] if version_same else stamp,
        "spoolmandb_hash": content_hash,
        "spoolmandb_changed_at": prev_state["spoolmandb_changed_at"] if hash_same else stamp,
        "inputs": {r.source: r.current for r in freshness},
        "freshness_mismatches": [
            {"source": r.source, "moved": r.moved, "previous": r.previous, "current": r.current} for r in freshness if r.mismatch
        ],
        "counts": {
            "spoolmandb_filaments": len(sdb_filaments),
            "spoolmandb_materials": len(sdb_materials),
            "ofd_filaments": ofd_count,
        },
        "outputs": outputs,
    }


def check_ofd_stalled(manifest: dict[str, Any], prev_state: dict[str, Any], now: datetime) -> list[str]:
    """Warnings if OFD's export has not advanced for OFD_STALE_AFTER.

    The manifest's own `generated_at` is authoritative when present. Without it we can only fall back to
    when this tool first saw the current `dataset_version`.
    """
    generated = parse_timestamp(manifest.get("generated_at"))
    if manifest.get("generated_at") is not None and generated is None:
        log.warning("OFD generated_at %r is not a parseable timestamp; staleness not checked from it", manifest.get("generated_at"))
    version = manifest.get("dataset_version")
    first_seen = parse_timestamp(prev_state.get("ofd_version_changed_at"))
    # A generated_at in the future yields a negative age and never fires; that is deliberate (clock skew is not a stall).
    if generated is not None:
        age, basis = now - generated, f"generated_at {manifest['generated_at']}"
    elif version and prev_state.get("ofd_version") == version and first_seen is not None:
        age, basis = now - first_seen, f"dataset_version {version} unchanged since first seen"
    else:
        return []
    if age > OFD_STALE_AFTER:
        hours = age.total_seconds() / 3600
        return [f"OFD looks stalled: {basis} is {hours:.1f} hours old (limit {OFD_STALE_AFTER.total_seconds() / 3600:.0f} hours)"]
    return []


def write_state(out_dir: Path, state: dict[str, Any]) -> None:
    """Write the state file atomically (temp file in the same dir, then rename)."""
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{STATE_FILE}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out_dir / STATE_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(
    argv: Sequence[str] | None = None,
    fetcher: Callable[[str], Fetched] | None = None,
    now: datetime | None = None,
) -> int:
    """Fetch both sources, merge, sanity-check, and publish.

    `argv` defaults to sys.argv[1:]; `fetcher` replaces the network download and `now` the clock (both used by tests).
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="output directory the static web server serves")
    ap.add_argument("--dry-run", action="store_true", help="build and report, but write nothing")
    ap.add_argument("--include-discontinued", action="store_true", help="deprecated, ignored (OFD keeps discontinued products)")
    # Hidden: read the sources from disk instead of the network (CI schema job).
    ap.add_argument("--from-ofd-dir", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--from-sdb-dir", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--now", help=argparse.SUPPRESS)  # pin the clock (ISO-8601); CI fixtures age out otherwise
    args = ap.parse_args(argv)
    if args.now is not None:
        pinned = parse_timestamp(args.now)
        if pinned is None:
            ap.error("--now must be an ISO-8601 timestamp")
        now = pinned
    if (args.from_ofd_dir is None) != (args.from_sdb_dir is None):
        ap.error("--from-ofd-dir and --from-sdb-dir must be given together")
    if args.from_ofd_dir is not None:
        fetcher = local_fetcher(args.from_ofd_dir, args.from_sdb_dir)
    fetch_source = fetcher or fetch
    now = now or datetime.now(UTC)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.include_discontinued:
        log.warning("--include-discontinued is ignored: OFD's published catalog already includes discontinued products")
    try:
        fetched = {
            "spoolmandb_filaments": fetch_source(SPOOLMANDB_URL + "filaments.json"),
            "spoolmandb_materials": fetch_source(SPOOLMANDB_URL + "materials.json"),
            "ofd_filaments": fetch_source(OFD_FILAMENTS_URL),
            "ofd_materials": fetch_source(OFD_MATERIALS_URL),
            "ofd_manifest": fetch_source(OFD_MANIFEST_URL),
        }
        sdb_filaments = json.loads(fetched["spoolmandb_filaments"].data)
        sdb_materials = json.loads(fetched["spoolmandb_materials"].data)
        ofd_filaments = validate_catalog("filaments.json", json.loads(fetched["ofd_filaments"].data), FILAMENT_KEYS)
        ofd_materials = validate_catalog("materials.json", json.loads(fetched["ofd_materials"].data), MATERIAL_KEYS)
        manifest = json.loads(fetched["ofd_manifest"].data)
        if not isinstance(manifest, dict):
            raise ValueError("OFD manifest.json is not a JSON object")
    except Exception as exc:  # network / parse failure: keep the last good catalog
        log.error("fetch failed, leaving existing catalog untouched: %s", exc)
        return EXIT_FETCH_FAILED
    log.info(
        "SpoolmanDB: %d filaments, %d materials; OFD: %d filaments, %d materials, dataset %s",
        len(sdb_filaments),
        len(sdb_materials),
        len(ofd_filaments),
        len(ofd_materials),
        manifest.get("dataset_version"),
    )

    raw = copy.deepcopy((sdb_filaments, sdb_materials, ofd_filaments, ofd_materials))  # before the brand-spelling rewrite below
    warn_over_long_fixed_names(sdb_filaments)
    warn_over_long_fixed_names(ofd_filaments, source="OFD")
    filaments = merge_filaments(ofd_filaments, sdb_filaments)
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

    materials = merge_materials(ofd_materials, sdb_materials)
    unknown = sorted({f["material"] for f in filaments} - {m["material"] for m in materials})
    if unknown:
        log.info("filaments reference %d material(s) missing from materials.json: %s", len(unknown), unknown[:10])
    brands = len({f["manufacturer"] for f in filaments})
    log.info("merged: %d filaments across %d manufacturers", len(filaments), brands)

    # Drift checks. Hard guards first; they also run on --dry-run because they only read.
    prev_state = load_state(args.out)
    missing = check_spoolmandb_superset(*raw, filaments, materials)
    for problem in missing:
        log.error("coverage: %s", problem)
    if missing:
        log.error("output does not cover SpoolmanDB (beyond what OFD replaces); refusing to publish")
        return EXIT_SPOOLMANDB_NOT_SUPERSET
    shrunk = check_source_shrink(prev_state, len(sdb_filaments), len(ofd_filaments))
    for problem in shrunk:
        log.error("source shrink: %s", problem)
    if shrunk:
        log.error("a source shrank by more than %d%%; refusing to publish", round((1 - MIN_RETAINED_FRACTION) * 100))
        return EXIT_SOURCE_SHRANK
    warnings = check_ofd_stalled(manifest, prev_state, now)
    for warning in warnings:
        log.warning("%s", warning)
    # Reported only: a freshness mismatch must neither block publishing nor change the exit code (alert routing is #24).
    freshness = check_input_freshness(prev_state, fetched)
    for result in freshness:
        log.log(logging.WARNING if result.mismatch else logging.INFO, "%s", result.message)
    exit_code = EXIT_PUBLISHED_WITH_WARNINGS if warnings else 0

    if args.dry_run:
        log.info("dry run: nothing written")
        return exit_code
    outputs = publish(args.out, {"filaments.json": sorted(filaments, key=lambda f: f["id"]), "materials.json": materials})
    write_state(args.out, build_state(prev_state, now, manifest, raw[0], raw[1], len(ofd_filaments), freshness, outputs))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
