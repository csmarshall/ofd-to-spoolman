"""LEGACY: the converter's own OFD all.json -> Spoolman mapping, kept only so tools/conflict_report.py keeps working.

The catalog builder (ofd2spoolman.py) no longer converts OFD itself: since ADR 0009 it consumes OFD's published
Spoolman catalog and OFD wins on overlap. This module is the pre-ADR-0009 mapping, moved here unchanged. It is not
used by the published catalog; conflict_report.py still compares SpoolmanDB against it and needs porting to OFD's
published catalog (follow-up). Delete this module when that happens.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ofd2spoolman import Fetched, norm, norm_hex

OFD_ALL_URL = "https://api.openfilamentdatabase.org/json/all.json.gz"
SPOOLMAN_FINISHES = {"matte", "glossy"}
SPOOLMAN_PATTERNS = {"marble", "sparkle"}

log = logging.getLogger("ofd2spoolman")


def local_fetcher(ofd_file: Path, sdb_dir: Path) -> Callable[[str], Fetched]:
    """Serve the OFD all.json and SpoolmanDB files from disk (tests); there is no Last-Modified."""

    def fetch_local(url: str) -> Fetched:
        path = ofd_file if url == OFD_ALL_URL else sdb_dir / url.rsplit("/", 1)[1]
        return Fetched(path.read_bytes(), None)

    return fetch_local


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
        # The canonical stored uuid, never the derived size["id"] (a uuid5 over names, GTIN and row position that changes on an edit).
        # No fallback to it.
        uuid = size.get("uuid")
        if not uuid or not isinstance(uuid, str):
            skip("missing uuid")
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
            "id": f"ofd_{uuid}",
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
