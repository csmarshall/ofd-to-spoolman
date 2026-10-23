# 0003. Deterministic shortening of names to Spoolman's 64-character limit

- Status: Accepted
- Date: 2026-10-15

## Context

Spoolman's filament `name` is capped at 64 characters: `FilamentParameters.name` has `max_length=64` and the ORM column is `String(64)`, checked against Spoolman v0.27.0 ([#30](https://github.com/csmarshall/ofd-to-spoolman/issues/30)). The limit sits in the first commit with data models and I found no stated rationale; the issue reads it as an arbitrary default and says so explicitly as inference. `ExternalFilamentsFile` accepts longer names that the create endpoint then rejects, so a catalog can pass schema validation and still fail when a user picks the entry. On 2026-10-15, 20 of 29,490 entries in the merged catalog were over 64 (longest 78: Eryone 13, ROSA3D 6, Amolen 1). SpoolmanDB enforces no length, though its longest resolved name was 58 at the time (all from #30).

## Decision

Shorten deterministically; do not fail and do not skip (decided 2026-10-15, [#30](https://github.com/csmarshall/ofd-to-spoolman/issues/30), implemented in [PR #32](https://github.com/csmarshall/ofd-to-spoolman/pull/32)). Only OFD-derived names are changed; SpoolmanDB names are never altered, only warned about if over 64 (`warn_over_long_fixed_names`). For an over-long name:

1. Drop a trailing parenthetical (for example a colour list) if the remainder fits and does not collide with another record's name for the same manufacturer, material, weight and diameter. A trailing `(refill)` marker is never dropped.
2. Otherwise elide the middle on word boundaries with a single ellipsis, keeping whole words from the start and from the end (the tail gets up to a third of the budget, so a trailing colour survives); a single over-long word is cut by characters.
3. If an elided name still collides with another record's final name, shift the cut (smaller tail first, then larger), and as a last resort append a `~` plus a short digest of the full original name.

Records are resolved in sorted order against every other record, so the result does not depend on input order; the function is idempotent on names already within the limit. Each case is logged at INFO. The same rule lives in the OFD fork's exporter and the two are kept semantically identical ([CLAUDE.md](../../CLAUDE.md)); a shared 15-case golden fixture is asserted on both sides ([#35](https://github.com/csmarshall/ofd-to-spoolman/issues/35), [PR #36](https://github.com/csmarshall/ofd-to-spoolman/pull/36)).

## Consequences

Good: no entry is lost and names do not flap between syncs, so repeated syncs should not rename filaments. Colours remain visible at the end of the name. The behaviour is pinned by golden tests and by the create-path replay (`tests/validate_create_path.py`) that runs inside the real Spoolman image.

Bad: some displayed names are no longer the vendor's. Because collisions are judged against the shifted cut, a data change elsewhere in the catalog can in principle change which cut a name receives; the shared fixture covers the known cases, not every possible future one. A digest-tagged name is ugly and exists only as a last resort. In the fork's round on 2026-10-15 the middle-elision uniqueness check was initially missing and was ported afterwards ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5), comment of 19:46 UTC).

## Alternatives considered

- Plain tail truncation ending in an ellipsis, which was the original wording of the decision in #30. Superseded during implementation: six ROSA3D names carry the colour at the very end, so tail truncation would remove the only distinguishing part. #30 itself named middle elision as a candidate refinement, and PR #32 implemented it. The issue text and the code therefore differ; the code and PR description are authoritative.
- Skip or fail on over-long names. Rejected in the decision: losing entries or breaking the build for 0.07% of names is worse.
- Ask Spoolman to widen the column (for example to 128, following the `multi_color_hexes` precedent). #30 judged it low value and non-trivial (model, ORM, a migration that needs batch mode on SQLite, the client forms), and noted nothing was filed upstream; we would still need the workaround for older Spoolman versions.

## References

- Issues: [#30](https://github.com/csmarshall/ofd-to-spoolman/issues/30), [#35](https://github.com/csmarshall/ofd-to-spoolman/issues/35), [#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)
- Pull requests: [#32](https://github.com/csmarshall/ofd-to-spoolman/pull/32), [#36](https://github.com/csmarshall/ofd-to-spoolman/pull/36)
- Code: `shorten_long_names`, `shorten_name`, `NAME_MAX_LENGTH` in [ofd2spoolman.py](../../ofd2spoolman.py); [tests/test_golden_names.py](../../tests/test_golden_names.py)
