# ofd-to-spoolman — agent instructions

Bridge from the Open Filament Database (OFD) into Spoolman's external filament catalog (SpoolmanDB format): it merges OFD's own published Spoolman catalog with SpoolmanDB's. See README.md for what it does and what is merged.

## Golden rules

- **The output contract is Spoolman's own model**, `spoolman.externaldb.ExternalFilament` / `ExternalMaterial`. Any field or enum change must be checked against the current Spoolman source, not against memory. CI validates real output with the real Spoolman image; never weaken that job to make it pass.
- **Never invent data.** OFD's records are published exactly as OFD's exporter produced them (apart from brand spelling); don't backfill or rewrite densities, spool weights, hex codes, temperatures or names here. Mapping fixes belong in OFD's exporter.
- **OFD wins on collision** (ADR 0009, which supersedes 0004): the output is OFD's published catalog plus every SpoolmanDB filament whose `dedupe_key` OFD lacks, and SpoolmanDB materials OFD lacks. SpoolmanDB entries that survive are passed through unchanged apart from brand-spelling unification (first seen wins, OFD first).
- **Never publish a worse catalog than the live one.** Fetch failures, duplicate ids and a >50% shrink all exit non-zero without touching the output. Keep the writes atomic (temp file + rename in the same dir).
- **Standard library only** for `ofd2spoolman.py`; it runs from cron under `/usr/bin/python3 -I` with no venv. Dev tooling (pytest, ruff, mypy) lives in the `dev` extra.
- **Names are at most 64 characters** (Spoolman's `FilamentParameters.name` `max_length=64`, ORM `String(64)`). Names from either source are never altered here, only warned about; OFD's exporter owns OFD's names (the shared golden-names test was retired with the converter's own shortening).
- `tests/validate_create_path.py` replays the UI's create mapping against `FilamentParameters` inside the real Spoolman image, because `ExternalFilamentsFile` accepts records the create endpoint rejects. `tests/schema_check.sh` must also see its known-bad fixture rejected; never weaken either.
- Ids of OFD records are whatever OFD's exporter publishes (`ofd_<size uuid>` so far). SpoolmanDB ids that OFD covers disappear from the catalog, so existing links to them dangle; Spoolman has no alias concept. Changing the id scheme orphans every Spoolman filament linked to an external id, so treat it as a breaking change.
- `tools/legacy_ofd_convert.py` is the pre-ADR-0009 mapping, kept only for `tools/conflict_report.py`; do not use it for the catalog.

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

- OFD's Spoolman catalog is at `https://api.openfilamentdatabase.org/spoolman/filaments.json` and `.../spoolman/materials.json`, with `.../manifest.json` (`dataset_version`, `generated_at`) for the version; none of them is under `/api/v1/`. The catalog files carry no version themselves.
- The `norm()` used for name comparison must keep `+`: `PETG+` is not `PETG`. A regression test covers this.
- Spoolman serves `/external/filament` straight from its cache file; a request during the hourly sync rewrite can 500 once ("Response content longer than Content-Length"). That's upstream behaviour, not ours.
