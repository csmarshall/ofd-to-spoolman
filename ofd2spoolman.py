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
import gzip
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
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


def publish(out_dir: Path, files: dict[str, Any]) -> None:
    """Write each file atomically (temp file in the same dir, then rename)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{name}.")
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, out_dir / name)
        log.info("published %s (%d entries)", out_dir / name, len(payload))


def previous_count(out_dir: Path) -> int | None:
    """Entry count of the currently published filaments.json, if any."""
    try:
        return len(json.loads((out_dir / "filaments.json").read_text()))
    except (OSError, ValueError):
        return None


def main(argv: Sequence[str] | None = None, fetcher: Callable[[str], bytes] | None = None) -> int:
    """Fetch both sources, merge, sanity-check, and publish.

    `argv` defaults to sys.argv[1:]; `fetcher` replaces the network download (used by tests).
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
        return 2
    log.info("SpoolmanDB: %d filaments, %d materials; OFD dataset %s", len(sdb_filaments), len(sdb_materials), ofd.get("version"))

    ofd_filaments = convert_ofd(ofd, args.include_discontinued)

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
        return 3

    # Guard against a broken upstream silently emptying the dropdown: refuse to
    # shrink below half of what is currently published.
    prev = previous_count(args.out)
    if prev and len(filaments) < prev // 2:
        log.error("merged catalog has %d entries vs %d published; refusing to shrink by >50%%", len(filaments), prev)
        return 4

    materials = merge_materials(sdb_materials, filaments)
    brands = len({f["manufacturer"] for f in filaments})
    log.info("merged: %d filaments across %d manufacturers", len(filaments), brands)

    if args.dry_run:
        log.info("dry run: nothing written")
        return 0
    publish(args.out, {"filaments.json": filaments, "materials.json": materials})
    return 0


if __name__ == "__main__":
    sys.exit(main())
