# 0001. Catalog values come from first-party vendor sources only

- Status: Accepted
- Date: 2026-10-21 (the rule was stated in pieces across [#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45) and [#47](https://github.com/csmarshall/ofd-to-spoolman/issues/47) on that day; there is no single earlier decision record)

## Context

This repo publishes a catalog built from two upstream databases, the Open Filament Database (OFD) and SpoolmanDB. When we find that a value in either is missing or wrong (for example a nozzle temperature that Spoolman then treats as unset), we want to fix it at the source rather than keep a private overlay, which the [roadmap](../ROADMAP.md) already prefers ("Prefer contributing to OFD over keeping a private overlay"). That raises the question of what counts as acceptable evidence for a changed value. The first draft of the audit plan in [#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45) allowed "vendor-first evidence ... then retailer listings such as Amazon". During the first rounds the same issue recorded that retail sites often block plain fetches, that WebFetch-style tools return summarised renderings rather than page text, and that [#47](https://github.com/csmarshall/ofd-to-spoolman/issues/47) found OFD itself exports OrcaSlicer presets from its own data, which makes slicer presets a circular source for OFD values.

## Decision

A value is written into OFD only when it is backed by a first-party vendor source: the vendor's own product page, technical data sheet (TDS) or wiki, quoted verbatim with its URL. Specifically:

- Retailers are never a source. One narrow, documented exception exists: Inland is Micro Center's house brand, so Micro Center product pages are the first-party pages for it ([OFD #670](https://github.com/OpenFilamentCollective/open-filament-database/pull/670), whose body states this reasoning).
- Slicer presets, including OrcaSlicer and Bambu Studio presets, are not evidence for OFD values, because OFD generates its OrcaSlicer export from its own data and the check would be circular. A preset that predates OFD or is authored separately by the vendor may serve as a cross-check, as noted in [#47](https://github.com/csmarshall/ofd-to-spoolman/issues/47).
- Never infer a value: if no first-party source states it, the field stays unset ([#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45), "Never infer a value; no evidence means the field stays a gap"). Vendors do not publish an empty-spool weight, so `spool_weight` is only ever a measurement ([#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45), round 1 results).
- SpoolmanDB gets evidence only. This repo never edits SpoolmanDB's values and never files anything upstream automatically; the conflict report ([#44](https://github.com/csmarshall/ofd-to-spoolman/issues/44), [ADR 0004](0004-merge-policy-spoolmandb-wins.md)) is a digest of disagreements that a human may turn into a vendor-verified fix. SpoolmanDB's contribution rules forbid AI-generated data and require a measured spool weight ([#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45), constraints), so any fix there has to be human-verified vendor fact. The audit work so far has produced pull requests to OFD only; I did not search SpoolmanDB's pull requests to confirm that none exist from this project.
- The same principle applies to the converter itself: where OFD has no value we emit null and do not backfill from "similar" products ([CLAUDE.md](../../CLAUDE.md), "Never invent data").

## Consequences

Good: every changed value can be traced to a URL and a quote, which makes review by the OFD maintainer a matter of checking a quote; the circularity trap (OFD data to OrcaSlicer preset to OFD data) is closed; our output cannot quietly diverge from what the vendors say.

Bad: coverage is slower and smaller. Several vendors block automated fetching (Micro Center, Amazon, Newegg returned 403 in [#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45) round 1) and need a browser pass; vendors contradict themselves (Sunlu, Polymaker TDS V5.4 against V5.5) and those fields are left alone; first-party sources rarely give empty-spool weights, so that gap stays open. The audit tooling also rests on text extraction that may be imperfect, which is why each commit carries a verbatim quote that a reviewer can check. This ADR cannot guarantee a vendor's page is itself correct; it only makes the claim traceable.

## Alternatives considered

- Accept retailer listings as a fallback, as the first plan in #45 did. Rejected because retail copy is frequently copied from, or inconsistent with, the vendor's data, and it was the thing most often blocked.
- Use slicer presets as a cross-check on OFD values. Rejected for OFD values because of the circularity above.
- Keep a private overlay of corrections in this repo. Rejected in the roadmap in favour of upstream contributions.

## References

- Issues: [#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45), [#47](https://github.com/csmarshall/ofd-to-spoolman/issues/47), [#44](https://github.com/csmarshall/ofd-to-spoolman/issues/44), [#15](https://github.com/csmarshall/ofd-to-spoolman/issues/15)
- OFD pull request [#670](https://github.com/OpenFilamentCollective/open-filament-database/pull/670) (the Inland exception); see also [ADR 0008](0008-vendor-data-audit-conventions.md)
- [CLAUDE.md](../../CLAUDE.md), [docs/ROADMAP.md](../ROADMAP.md)
