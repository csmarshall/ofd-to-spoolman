# Architecture decision records

Short records of the decisions that shape this project, written down so the rationale is on record, can be linked from upstream discussions, and is easy to revisit. Before these, the decisions lived in issue threads, [CLAUDE.md](../../CLAUDE.md) and session notes. Each ADR is a snapshot: where the code or the issues have moved on, the ADR says what was verified at the time and what was only projected.

## Format

Roughly [MADR](https://adr.github.io/madr/): title, Status, Date, Context, Decision, Consequences (good and bad), Alternatives considered, References. Status is `Accepted`, `Proposed`, or later `Superseded by NNNN`. The date is the decision date taken from the issue thread, not the date the ADR was written; where it could not be pinned down the ADR says so. Files are numbered in the order written, `NNNN-short-title.md`, and are not renumbered. To change a decision, add a new ADR and mark the old one superseded rather than editing history. Prose is not hard-wrapped.

## Index

| No. | Title | Status | Date |
|---|---|---|---|
| [0001](0001-first-party-catalog-sources.md) | Catalog values come from first-party vendor sources only | Accepted | 2026-10-21 |
| [0002](0002-ofd-size-uuid-ids.md) | Catalog ids are `ofd_<size uuid>` | Accepted | 2026-10-15 |
| [0003](0003-deterministic-name-shortening.md) | Deterministic shortening of names to 64 characters | Accepted | 2026-10-15 |
| [0004](0004-merge-policy-spoolmandb-wins.md) | Merge policy: SpoolmanDB wins on duplicates, with a conflict report | Accepted | 2026-10-14 |
| [0005](0005-multi-colour-mapping.md) | Multi-colour mapping: `color_hexes` plus direction, single-colour fallback | Accepted | 2026-10-15 |
| [0006](0006-publish-via-scheduled-action-to-pages.md) | Publish via a scheduled Action to GitHub Pages | Accepted | 2026-10-14 |
| [0007](0007-upstream-spoolman-exporter-to-ofd.md) | Upstream route: propose a Spoolman exporter to OFD | Proposed | 2026-10-15 |
| [0008](0008-vendor-data-audit-conventions.md) | Vendor data audit conventions for upstream data PRs | Accepted | 2026-10-21 |
