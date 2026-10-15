#!/usr/bin/env bash
# Build the catalog from the fixtures, validate it with the real Spoolman image, then prove the
# validator can fail by feeding it a known-bad catalog. Used by CI (spoolman-schema job) and runnable locally.
# Usage: tests/schema_check.sh [docker-command]   (default: docker; use "sudo -n docker" if needed)
unset TMOUT
set -euo pipefail

IMAGE="${SPOOLMAN_IMAGE:-ghcr.io/donkie/spoolman:latest}"
DOCKER="${1:-docker}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

validate() {
    # Read-only mounts, so nothing root-owned is ever written to the host.
    $DOCKER run --rm --user "$(id -u):$(id -g)" \
        -v "$1:/catalog:ro" -v "$HERE/validate_with_spoolman.py:/validate.py:ro" \
        -e PYTHONPATH=/home/app/spoolman --entrypoint /home/app/spoolman/.venv/bin/python \
        "$IMAGE" /validate.py /catalog
}

echo "[INFO] building catalog from fixtures"
python3 "$REPO/ofd2spoolman.py" --out "$WORK/good" --from-ofd-file "$HERE/fixtures/ofd_all.json" --from-sdb-dir "$HERE/fixtures/sdb" \
    --now 2026-10-13T04:00:00Z  # pin the clock: the fixture's generated_at ages into the OFD-stalled warning (exit 10) otherwise

echo "[INFO] good catalog must validate"
validate "$WORK/good"

validate_create_path() {
    $DOCKER run --rm --user "$(id -u):$(id -g)" \
        -v "$1:/catalog:ro" -v "$HERE/validate_create_path.py:/validate_create_path.py:ro" \
        -e PYTHONPATH=/home/app/spoolman --entrypoint /home/app/spoolman/.venv/bin/python \
        "$IMAGE" /validate_create_path.py /catalog
}

echo "[INFO] good catalog must also pass Spoolman's create path (FilamentParameters, the model POST /filament validates)"
validate_create_path "$WORK/good"

echo "[INFO] known-bad catalog (invalid finish enum, missing density, material without density) must FAIL validation"
if validate "$HERE/fixtures/bad" >"$WORK/bad.log" 2>&1; then
    echo "[ERROR] Spoolman accepted the known-bad catalog: the schema check cannot fail" >&2
    cat "$WORK/bad.log" >&2
    exit 1
fi
# It must fail for the right reasons, not because the image or script is broken.
for needle in 'INVALID filaments.json' 'INVALID materials.json' finish density; do
    grep -q "$needle" "$WORK/bad.log" || { echo "[ERROR] bad catalog failed, but not on '$needle':" >&2; cat "$WORK/bad.log" >&2; exit 1; }
done
echo "[INFO] known-bad catalog rejected as expected:"
grep -E "^INVALID|validation error|^[0-9.a-z_]+$|Input should|Field required" "$WORK/bad.log" | sed -n 1,12p

echo "[INFO] create-path known-bad records (65-char name, multi-colour with no direction, color_hex plus color_hexes) must be REJECTED"
if validate_create_path "$HERE/fixtures/bad_create" >"$WORK/bad_create.log" 2>&1; then
    echo "[ERROR] the create-path check accepted the known-bad records: it cannot fail" >&2
    cat "$WORK/bad_create.log" >&2
    exit 1
fi
for needle in 'bad_name_65' 'at most 64 characters' 'bad_multi_no_direction' 'must have multi_color_direction' 'bad_both_colour_fields' 'Cannot specify both' '3 rejected'; do
    grep -q "$needle" "$WORK/bad_create.log" || { echo "[ERROR] bad create-path records failed, but not on '$needle':" >&2; cat "$WORK/bad_create.log" >&2; exit 1; }
done
echo "[INFO] create-path known-bad records rejected as expected:"
cat "$WORK/bad_create.log"
echo "[INFO] schema check passed"
