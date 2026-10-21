# 0002. Catalog ids are `ofd_<size uuid>`

- Status: Accepted
- Date: 2026-10-15

## Context

Spoolman links a user's filament to a catalog entry through `external_id`, so the catalog's ids are a public contract: if an id changes, every link made against the old id breaks. The first version of the converter used `ofd_<size.id>`. OFD's `size.id` is a uuid5 that its builder derives from the brand, material, filament name, colour name, weight, diameter, GTIN/EAN, refill flag, article number and the row's position in `sizes.json` ([#28](https://github.com/csmarshall/ofd-to-spoolman/issues/28)), so a colour-name typo fix, a newly added GTIN or a reordered file changes it. OFD also stores a random canonical `uuid` per size, which its manual describes as stable and never changing on a rename ([#28](https://github.com/csmarshall/ofd-to-spoolman/issues/28)), and keeps `moved_from` and a uuid-index redirect map for merges.

Evidence that the derived id is unstable in practice: in a measurement over 120 consecutive OFD releases, the derived id changed for 22,158 entries on 2026-05-19 while content barely moved (47 added, 20 removed), and the `uuid` field first appeared in OFD's export on 2026-07-18 ([#29](https://github.com/csmarshall/ofd-to-spoolman/issues/29)). That retroactively supports the choice; it was not the reason it was made, which was OFD's own documentation of the two fields.

## Decision

Ids are `ofd_<size["uuid"]>`, OFD's stored canonical uuid, decided 2026-10-15 and shipped in [PR #32](https://github.com/csmarshall/ofd-to-spoolman/pull/32). Sizes without a `uuid` are skipped and counted (reported as `missing uuid`), and the code never falls back to the derived `size["id"]`. A regression test fails if the id is built from `id`. The OFD-fork exporter was switched the same way so both produce identical ids ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)). The rule is recorded in [CLAUDE.md](../../CLAUDE.md), and changing it is treated as a breaking change.

## Consequences

Good: links should survive name edits, GTIN additions and file reordering. On 2026-10-15 every size in OFD's data tree had a uuid (23,309 of 23,309, checked in #28), and the switch cost nothing at the time because no install depended on the old ids (the maintainer's only Spoolman filament had a null `external_id`, per #28); doing it before the Pages catalog went live and before any upstream exporter proposal was the point. SpoolmanDB ids are untouched, and `ofd_` ids cannot collide with them.

Bad: Spoolman has no alias concept, so when OFD retires a uuid and records it in `moved_from` (471 sizes carried `moved_from` on 2026-10-15), links to the old uuid do not follow. If OFD ever regenerates uuids in bulk, this scheme is exposed the same way the derived one was; that is a risk I cannot rule out, only one that OFD's documentation says should not happen. The published ids are now a contract with every consumer of the Pages catalog and of any OFD exporter that emits the same ids ([ADR 0007](0007-upstream-spoolman-exporter-to-ofd.md)), so the id scheme cannot be revisited cheaply. The first build after the switch changed all 21,370 OFD ids at once (PR #32 live-build note), which is expected for a one-time migration; the id-set size guard stayed quiet because the count did not move.

## Alternatives considered

- Keep the derived `size.id`. Rejected for the instability above.
- Fall back to the derived id when `uuid` is missing. Rejected: it would silently mix two schemes, and the fallback value is exactly the unstable one. Skipping and counting is visible instead.
- Mint our own ids (for example a hash of the content key). Rejected as unnecessary when OFD already stores a canonical one, and it would diverge from an upstream exporter.

## References

- Issues: [#28](https://github.com/csmarshall/ofd-to-spoolman/issues/28), [#29](https://github.com/csmarshall/ofd-to-spoolman/issues/29), [#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)
- Pull request: [#32](https://github.com/csmarshall/ofd-to-spoolman/pull/32)
- Code: `convert_ofd` in [ofd2spoolman.py](../../ofd2spoolman.py); [CLAUDE.md](../../CLAUDE.md)
