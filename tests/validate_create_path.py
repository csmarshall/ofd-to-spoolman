"""Replay Spoolman's web UI "create filament from the external DB" mapping against the real create model.

Runs INSIDE the ghcr.io/donkie/spoolman image.

ExternalFilamentsFile (validate_with_spoolman.py) only checks that the catalog parses. It accepts records the create endpoint then
rejects: a name over 64 characters, a multi-colour record with no direction, a record carrying both color_hex and color_hexes. This
script feeds every record through spoolman.api.v1.filament.FilamentParameters (what POST /filament validates), plus the limits that
live on other models and columns.

The UI mapping is read from the compiled client bundle (client/dist/assets/functions-*.js): spool_weight / extruder_temp / bed_temp
use `||void 0` (0 or null is omitted), external_id is the record id, vendor_id comes from the vendor. Deliberate difference: the UI
sends only color_hex when both colour fields are set and silently drops the others, which would hide ambiguous data, so here both are
passed through and the model rejects it. Second deliberate deviation: a multi_color_direction with no colour list is sent and
rejected ("Single-color filament must not have multi_color_direction set"); the UI would drop it. What this cannot
catch: a change in the client bundle's mapping (it is replicated by hand,
not imported), or limits enforced only at the database.

Usage:
    PYTHONPATH=/home/app/spoolman /home/app/spoolman/.venv/bin/python validate_create_path.py CATALOG_DIR

Exit 0 if every record is accepted, 1 (listing the first rejections on stderr) otherwise.
"""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from spoolman.api.v1.filament import FilamentParameters

# spoolman/api/v1/vendor.py and models.py Vendor.name: 64. The vendor is created by a different model, so it is checked directly.
VENDOR_NAME_MAX = 64
# models.py Filament.multi_color_hexes is String(128).
MULTI_COLOR_HEXES_MAX = 128
REPORTED_REJECTIONS = 10


def ui_payload(record: dict[str, Any]) -> dict[str, Any]:
    """The body the UI would POST to /filament for this external record."""
    payload: dict[str, Any] = {
        "name": record["name"],
        "material": record["material"],
        "vendor_id": 1,
        "density": record["density"],
        "diameter": record["diameter"],
        "weight": record["weight"],
        "spool_weight": record.get("spool_weight") or None,
        "settings_extruder_temp": record.get("extruder_temp") or None,
        "settings_bed_temp": record.get("bed_temp") or None,
        "external_id": record["id"],
    }
    if record.get("color_hex"):
        payload["color_hex"] = record["color_hex"]
    if record.get("color_hexes"):
        payload["multi_color_hexes"] = ",".join(record["color_hexes"])
        payload["multi_color_direction"] = record.get("multi_color_direction")
    elif record.get("multi_color_direction"):
        payload["multi_color_direction"] = record["multi_color_direction"]
    return payload


def problems_for(record: dict[str, Any]) -> list[str]:
    """Every reason Spoolman's create path would refuse this record."""
    problems: list[str] = []
    try:
        parsed = FilamentParameters(**ui_payload(record))
        if parsed.multi_color_hexes and len(parsed.multi_color_hexes) > MULTI_COLOR_HEXES_MAX:
            problems.append(f"multi_color_hexes: longer than the String({MULTI_COLOR_HEXES_MAX}) column")
    except ValidationError as exc:
        problems.extend(f"{'.'.join(map(str, e['loc'])) or 'model'}: {e['msg']}" for e in exc.errors())
    if len(record["manufacturer"]) > VENDOR_NAME_MAX:
        problems.append(f"manufacturer: longer than the {VENDOR_NAME_MAX}-character vendor name limit")
    return problems


def main(catalog: Path) -> int:
    records = json.loads((catalog / "filaments.json").read_bytes())
    rejected = 0
    for record in records:
        problems = problems_for(record)
        if problems:
            rejected += 1
            if rejected <= REPORTED_REJECTIONS:
                print(f"REJECTED {record['id']} {record['name']!r}: {problems}", file=sys.stderr)
    print(f"create-path: {len(records)} records replayed, {rejected} rejected")
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
