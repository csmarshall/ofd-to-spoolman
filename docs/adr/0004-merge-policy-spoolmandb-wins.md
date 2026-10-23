# 0004. Merge policy: SpoolmanDB wins on duplicates, with a conflict report

- Status: Accepted
- Date: 2026-10-14 (the rule is in the initial import commit [0a4baae](https://github.com/csmarshall/ofd-to-spoolman/commit/0a4baae); the conflict report was filed the same day as [#15](https://github.com/csmarshall/ofd-to-spoolman/issues/15))

## Context

The published catalog combines OFD and SpoolmanDB, which overlap: on 2026-10-21 there were 8,120 SpoolmanDB filaments, 1,610 of them duplicates of OFD entries ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5), comment of 2026-10-21). Spoolman users already select SpoolmanDB ids, and SpoolmanDB is the catalog Spoolman itself points at by default, so changing or removing its entries would surprise them. The two sources also disagree about the same products; the conflict report currently lists 1,761 conflicts (density 562, extruder_temp 93, bed_temp 37, spool_weight 145, color_hex 848, size 76 as of OFD dataset 2026.10.21, [#44](https://github.com/csmarshall/ofd-to-spoolman/issues/44)). I did not check which side is right for any of those; the report exists to surface them.

## Decision

SpoolmanDB wins on duplicates, and its entries pass through unchanged apart from unifying the brand spelling ([CLAUDE.md](../../CLAUDE.md); the first spelling seen wins and SpoolmanDB is seen first). A duplicate is decided by the dedupe key `(manufacturer, material, name, weight, diameter)`, with manufacturer, material and name normalised by `norm()` (case, punctuation and similar are folded, but `+` is kept so `PETG+` is not `PETG`; a regression test covers it) and weight and diameter compared as floats (`dedupe_key` in [ofd2spoolman.py](../../ofd2spoolman.py)). An OFD entry whose key is already present in SpoolmanDB is dropped; the rest are added.

Safeguards, all in the converter: a hard superset guard that fails the run (exit 5) if any SpoolmanDB filament is missing from the output or changed, a per-source shrink guard (exit 6), and the whole-catalog shrink guard ([PR #16](https://github.com/csmarshall/ofd-to-spoolman/pull/16)).

Disagreements are reported, not resolved: a scheduled workflow ([.github/workflows/conflict-report.yml](../../.github/workflows/conflict-report.yml), [PR #17](https://github.com/csmarshall/ofd-to-spoolman/pull/17), tool in [tools/conflict_report.py](../../tools/conflict_report.py)) matches products present in both with the converter's own key and keeps one tracking issue ([#44](https://github.com/csmarshall/ofd-to-spoolman/issues/44)) listing density, temperature, spool weight, hex and size disagreements beyond small tolerances, with new ones called out. A missing value on either side is not a conflict. Nothing is filed upstream automatically; the report is evidence for a human-verified fix ([ADR 0001](0001-first-party-catalog-sources.md)).

## Consequences

Good: users of SpoolmanDB entries see no change, the superset guard turns "we damaged SpoolmanDB" into a failed build, and the report gives a concrete list for upstream corrections.

Bad: where OFD is the more accurate side, the catalog still carries the SpoolmanDB value until it is fixed upstream. Duplicate detection is exact-key, so differently named products ("PolyTerra Black" against "PolyTerra Charcoal Black") slip through as two entries ([#3](https://github.com/csmarshall/ofd-to-spoolman/issues/3), still open, which also says false merges must be measured before any fuzzier matching is switched on). The report's tolerances are choices, not measurements, and 76 `size` conflicts are the noisiest category (see [#23](https://github.com/csmarshall/ofd-to-spoolman/issues/23)). The superset guard only shows the output contains SpoolmanDB as fetched, not that SpoolmanDB itself is intact ([PR #16](https://github.com/csmarshall/ofd-to-spoolman/pull/16), "What this can't catch"). The workflow header comment still says the repo is private; the repo has since become public, so that comment is stale.

## Alternatives considered

- OFD wins on duplicates. Rejected: it would change entries users may already have linked, and the roadmap prefers fixing OFD data upstream over overriding.
- Field-by-field merge (take whichever side has a value). Rejected: it would create hybrid records matching neither source and would be hard to explain; the conflict report keeps the disagreement visible instead.
- Fuzzy matching on hex, weight and material within a brand ([#3](https://github.com/csmarshall/ofd-to-spoolman/issues/3)). Deferred pending false-merge measurement.

## References

- Issues: [#15](https://github.com/csmarshall/ofd-to-spoolman/issues/15), [#44](https://github.com/csmarshall/ofd-to-spoolman/issues/44), [#3](https://github.com/csmarshall/ofd-to-spoolman/issues/3), [#23](https://github.com/csmarshall/ofd-to-spoolman/issues/23), [#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)
- Pull requests: [#16](https://github.com/csmarshall/ofd-to-spoolman/pull/16), [#17](https://github.com/csmarshall/ofd-to-spoolman/pull/17)
- [CLAUDE.md](../../CLAUDE.md)
