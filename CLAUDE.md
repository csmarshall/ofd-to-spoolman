# ofd-to-spoolman — agent instructions

Bridge from the Open Filament Database (OFD) into Spoolman's external filament catalog (SpoolmanDB format). See README.md for what it does and the field mapping.

## Golden rules

- **The output contract is Spoolman's own model**, `spoolman.externaldb.ExternalFilament` / `ExternalMaterial`. Any field or enum change must be checked against the current Spoolman source, not against memory. CI validates real output with the real Spoolman image; never weaken that job to make it pass.
- **Never invent data.** Where OFD has no value, emit null. Don't backfill densities, spool weights or hex codes from "similar" products. The one deliberate derivation is collapsing a temperature range into one value; it's documented in the README and must stay documented.
- **SpoolmanDB entries win on collision** and are passed through unchanged apart from brand-spelling unification.
- **Never publish a worse catalog than the live one.** Fetch failures, duplicate ids and a >50% shrink all exit non-zero without touching the output. Keep the writes atomic (temp file + rename in the same dir).
- **Standard library only** for `ofd2spoolman.py`; it runs from cron under `/usr/bin/python3 -I` with no venv. Dev tooling (pytest, ruff, mypy) lives in the `dev` extra.
- Ids are `ofd_<OFD size uuid>`. Changing the id scheme orphans every Spoolman filament linked to an external id, so treat it as a breaking change.

## Workflow

1. Issue first (`gh issue create`) for every change.
2. Branch in a worktree outside this tree: `~/work/claude/ofd-to-spoolman-worktrees/<n>-<slug>`.
3. PR with `Closes #n`; commit subjects `fix(#n): …` / `feat(#n): …`.
4. A green CI run is the gate, not a local "tests pass".
5. A `reviewer` subagent reviews the diff, then Charles reviews.
6. Squash-merge, then deploy deliberately with `./install.sh` (`--check` first for drift).

## Deploy target (rosa)

- Script: `/opt/sw/docker-compose/spoolman/ofd2spoolman.py` (copied by install.sh with a pointer header; never edit it in place).
- Output: `/opt/sw/spoolman/catalog/`, served by the `catalog` nginx sidecar in `/opt/sw/docker-compose/spoolman/docker-compose.yml`.
- Cron (charles): daily 23:15; last-run log at `/opt/sw/spoolman/catalog-build.log`.
- Check what's live: `curl -s http://127.0.0.1:7912/spoolman/api/v1/info` (`external_db_name`), and the count from `/spoolman/api/v1/external/filament`.

## Gotchas

- OFD's bulk export is at `https://api.openfilamentdatabase.org/json/all.json.gz`, NOT under `/api/v1/`.
- The `norm()` used for name comparison must keep `+`: `PETG+` is not `PETG`. A regression test covers this.
- rosa's `~/.curlrc` follows redirects; use `curl -q` when a status code or redirect is what you're checking.
- Spoolman serves `/external/filament` straight from its cache file; a request during the hourly sync rewrite can 500 once ("Response content longer than Content-Length"). That's upstream behaviour, not ours.
