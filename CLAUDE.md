# ofd-to-spoolman — agent instructions

Bridge from the Open Filament Database (OFD) into Spoolman's external filament catalog (SpoolmanDB format). See README.md for what it does and the field mapping.

## Golden rules

- **The output contract is Spoolman's own model**, `spoolman.externaldb.ExternalFilament` / `ExternalMaterial`. Any field or enum change must be checked against the current Spoolman source, not against memory. CI validates real output with the real Spoolman image; never weaken that job to make it pass.
- **Never invent data.** Where OFD has no value, emit null. Don't backfill densities, spool weights or hex codes from "similar" products. The one deliberate derivation is collapsing a temperature range into one value; it's documented in the README and must stay documented.
- **SpoolmanDB entries win on collision** and are passed through unchanged apart from brand-spelling unification.
- **Never publish a worse catalog than the live one.** Fetch failures, duplicate ids and a >50% shrink all exit non-zero without touching the output. Keep the writes atomic (temp file + rename in the same dir).
- **Standard library only** for `ofd2spoolman.py`; it runs from cron under `/usr/bin/python3 -I` with no venv. Dev tooling (pytest, ruff, mypy) lives in the `dev` extra.
- **Names are at most 64 characters** (Spoolman's `FilamentParameters.name` `max_length=64`, ORM `String(64)`). OFD-derived names go through `shorten_long_names`; SpoolmanDB names are never altered, only warned about. The same rule lives in the OFD fork's `spoolman_mapping.py`; keep the semantics identical so both produce the same names.
- `tests/validate_create_path.py` replays the UI's create mapping against `FilamentParameters` inside the real Spoolman image, because `ExternalFilamentsFile` accepts records the create endpoint rejects. `tests/schema_check.sh` must also see its known-bad fixture rejected; never weaken either.
- Ids are `ofd_<size["uuid"]>`: OFD's stored canonical `uuid`, NOT `size["id"]` (a uuid5 derived from names, GTIN and row position, which changes on an edit). Sizes without a `uuid` are skipped and counted, never falling back to `id`. Spoolman has no alias concept, so links to a uuid that OFD retires via `moved_from` won't follow. Changing the id scheme orphans every Spoolman filament linked to an external id, so treat it as a breaking change.

## Workflow

1. Issue first (`gh issue create`) for every change.
2. Branch in a worktree outside this tree: `~/work/claude/ofd-to-spoolman-worktrees/<n>-<slug>`.
3. PR with `Closes #n`; commit subjects `fix(#n): …` / `feat(#n): …`.
4. A green CI run is the gate, not a local "tests pass".
5. A `reviewer` subagent reviews the diff, then the maintainer reviews.
6. Squash-merge, then deploy deliberately with `./install.sh` (`--check` first for drift).

## Deploy target

Per-host deploy details (paths, cron, how to check what's live) are NOT committed. A gitignored `CLAUDE.local.md` may exist in the repo directory with them; read it if present. `install.sh` takes its target from `--target` or a gitignored `install.local` (`TARGET=...`). `--out` of `ofd2spoolman.py` is required. Never write host names, IPs or deploy paths into tracked files.

## Gotchas

- OFD's bulk export is at `https://api.openfilamentdatabase.org/json/all.json.gz`, NOT under `/api/v1/`.
- The `norm()` used for name comparison must keep `+`: `PETG+` is not `PETG`. A regression test covers this.
- Spoolman serves `/external/filament` straight from its cache file; a request during the hourly sync rewrite can 500 once ("Response content longer than Content-Length"). That's upstream behaviour, not ours.
