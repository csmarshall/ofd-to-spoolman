"""Validate a catalog directory with Spoolman's own models. Runs INSIDE the ghcr.io/donkie/spoolman image, not in the dev venv.

Usage (see .github/workflows/ci.yml):
    PYTHONPATH=/home/app/spoolman /home/app/spoolman/.venv/bin/python validate_with_spoolman.py CATALOG_DIR

Exit 0 if both files parse, 1 (with each pydantic error on stderr) otherwise.
"""

import sys
from pathlib import Path

from pydantic import ValidationError
from spoolman.externaldb import ExternalFilamentsFile, ExternalMaterialsFile


def main(catalog: Path) -> int:
    """Validate both files independently so one bad file cannot mask the other."""
    failures = 0
    for filename, model in (("filaments.json", ExternalFilamentsFile), ("materials.json", ExternalMaterialsFile)):
        try:
            parsed = model.model_validate_json((catalog / filename).read_bytes())
        except ValidationError as exc:
            failures += 1
            print(f"INVALID {filename}: {exc}", file=sys.stderr)
        else:
            print(f"OK {filename}: {len(parsed.root)} entries validated by Spoolman")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
