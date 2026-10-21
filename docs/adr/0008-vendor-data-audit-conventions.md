# 0008. Vendor data audit conventions for upstream OFD data PRs

- Status: Accepted
- Date: 2026-10-21

## Context

From 2026-10-21 we audited OFD's stored values against vendors and sent corrections upstream ([#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45), [#47](https://github.com/csmarshall/ofd-to-spoolman/issues/47)). Many sources disagree with each other (datasheet versions, pages that differ by colour or SKU), and an unreviewable bulk change would not be merged. The conventions below were stated in those issues and in the PR bodies themselves; there is no earlier standalone written rule, so the precedence rules in particular are reconstructed from OFD PRs [#670](https://github.com/OpenFilamentCollective/open-filament-database/pull/670) and [#679](https://github.com/OpenFilamentCollective/open-filament-database/pull/679) and Snapmaker [#659](https://github.com/OpenFilamentCollective/open-filament-database/pull/659).

## Decision

- Granularity: one PR per OFD brand directory, one commit per product line. Part 1 (corrections and fills to existing lines) goes before Part 2 (new products or colours); sub-brands that share a vendor are audited together but each directory is its own PR ([#47](https://github.com/csmarshall/ofd-to-spoolman/issues/47), [#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45)).
- Evidence: each commit message cites its source URL(s) and a verbatim quote, first-party only ([ADR 0001](0001-first-party-catalog-sources.md)). Page text is retrieved with curl or a real browser, not summarised renderings. Edits keep OFD's integer min/max convention, omit unknown fields (never null), keep file formatting, and the OFD validator is run before and after. PRs carry an AI-assistance line and a human reviews every value before submission.
- Precedence: a dated datasheet with a text layer is quoted verbatim and wins over the product page, and the commit says where they differ ([#679](https://github.com/OpenFilamentCollective/open-filament-database/pull/679)). A structured spec table wins over descriptive prose; when the prose disagrees, the PR says so and names the alternative ([#670](https://github.com/OpenFilamentCollective/open-filament-database/pull/670), Silk lines). An image-only datasheet is never a value source: nothing read visually from it is written, the page value is used only where the image does not contradict it, and where they disagree the field is left as it was and listed ([#679](https://github.com/OpenFilamentCollective/open-filament-database/pull/679)). Vendor-contradictory fields (for example Polymaker's PolySupport TDS V5.4 against V5.5) are left alone and queued ([#48](https://github.com/csmarshall/ofd-to-spoolman/issues/48)).
- Disagreement within one vendor: where the vendor's pages differ between colours or SKU generations, change only lines where every page agrees after unit normalisation, tolerating at most one named outlier ([#670](https://github.com/OpenFilamentCollective/open-filament-database/pull/670)); everything else is listed as left unchanged with counts.
- Honesty about verification: PR bodies state what the validator cannot catch (it checks schema, not truth) and what a recomputation script confirms (agreement with the capture, not that the capture is complete).

## Consequences

Good: the maintainer can review a commit by checking one quote; by 2026-10-21, 23 of the 24 PRs from #644 to #679 (our data PRs) had been merged by the OFD maintainer, #679 still open (GitHub PR list, queried 2026-10-21). The merged PRs I sampled showed no review comments from the maintainer beyond the merge, so I cannot say how closely the content was checked upstream; note the count is 24 PRs with 23 merged, slightly under the "25+" figure in the brief for this ADR set. Bad: slow, many fields stay unset (image-only datasheets and contradictory pages leave gaps, 31 of 51 Fiberlogy datasheets had no text layer), and the rules depend on the reviewer applying them consistently. Vendors that block fetches need a browser pass. A nightly unattended audit was floated ([#46](https://github.com/csmarshall/ofd-to-spoolman/issues/46), open) and would need these rules written as code.

## Alternatives considered

- One big PR or one per vendor family: rejected as unreviewable; OFD's structure is per brand directory.
- Allow retailer pages and presets: rejected, [ADR 0001](0001-first-party-catalog-sources.md).
- Fill gaps with midpoints or similar products: rejected, no invented data.

## References

- Issues: [#45](https://github.com/csmarshall/ofd-to-spoolman/issues/45), [#46](https://github.com/csmarshall/ofd-to-spoolman/issues/46), [#47](https://github.com/csmarshall/ofd-to-spoolman/issues/47), [#48](https://github.com/csmarshall/ofd-to-spoolman/issues/48), [#49](https://github.com/csmarshall/ofd-to-spoolman/issues/49)
- OFD pull requests: [#659](https://github.com/OpenFilamentCollective/open-filament-database/pull/659), [#660](https://github.com/OpenFilamentCollective/open-filament-database/pull/660), [#670](https://github.com/OpenFilamentCollective/open-filament-database/pull/670), [#679](https://github.com/OpenFilamentCollective/open-filament-database/pull/679) (full list #644 to #679)
