# 0005. Multi-colour mapping: `color_hexes` plus `multi_color_direction`, single-colour fallback

- Status: Accepted (implemented in the OFD-fork exporter; not yet in this repo's converter, see Consequences)
- Date: 2026-10-15

## Context

OFD's variant schema allows `color_hex` as a string or an array, but OFD's crawler kept only the first element, so every OFD export lost the other colours ([#4](https://github.com/csmarshall/ofd-to-spoolman/issues/4), investigation of 2026-10-15). In OFD's data, 1,513 variants use the array form and 992 of those have two or more colours. Spoolman models multi-colour filaments as `color_hexes: list[str]` plus `multi_color_direction` (`coaxial` or `longitudinal`); SpoolmanDB's convention in its 104 multi-colour entries is `color_hex` null whenever `color_hexes` is set and the direction always set (longitudinal 78, coaxial 26). OFD's `hex_variants` is a different concept (alternative codes, for example from NFC) and is not multi-colour (all from #4).

Of the 992 multi-colour variants, 399 carry the `coextruded` trait, 339 carry `gradual_color_change`, none carry both, and 254 carry neither. The investigation of those 254 found about 197 that are not multi-colour in Spoolman's sense (glow, thermochromic, iridescent and similar, where the list is states or viewing angles) and about 57 that are genuinely multi-colour but lack a trait in OFD (47 dual/tri-colour silk without `coextruded`, 10 gradients without `gradual_color_change`), identifiable from the product name ([#4](https://github.com/csmarshall/ofd-to-spoolman/issues/4), comment of 2026-10-15 18:27 UTC).

## Decision

- Map `coextruded` to `coaxial` and `gradual_color_change` to `longitudinal`, emitting `color_hexes` and a null `color_hex`.
- When a multi-colour variant has no direction trait, or both, fall back to a single colour (the first hex) rather than emit a null direction. Spoolman's create path rejects a multi-colour record with no direction, and the create-path replay in CI was extended to reject it ([PR #32](https://github.com/csmarshall/ofd-to-spoolman/pull/32), `tests/fixtures/bad_create`). The exporter round of 2026-10-15 produced 783 multi-colour records, all with a direction, plus 258 single-colour fallbacks ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)).
- Fix the data upstream, not in the mapping: OFD [PR #645](https://github.com/OpenFilamentCollective/open-filament-database/pull/645) makes the crawler preserve the full list as `color_hexes` (additive; `color_hex` unchanged) and OFD [PR #646](https://github.com/OpenFilamentCollective/open-filament-database/pull/646) adds `coextruded` to 47 variants and `gradual_color_change` to 10, with three borderline entries kept at the maintainer's direction. Both were opened 2026-10-15 and merged 2026-10-16 (GitHub merge timestamps). Once merged, those 57 should export as real multi-colour instead of falling back.

## Consequences

Good: the single-colour fallback means the catalog never contains a record Spoolman would reject, and the fix lives where the data does, so every consumer of OFD benefits.

Bad / open: this repo's converter does not do this yet. In `convert_ofd` at the time of writing, `color_hexes` and `multi_color_direction` are always null, and [#4](https://github.com/csmarshall/ofd-to-spoolman/issues/4) is still open; the mapping exists on the fork's `feat/spoolman-exporter` branch, which has not been proposed upstream ([ADR 0007](0007-upstream-spoolman-exporter-to-ofd.md)). So the published Pages catalog currently carries first-hex single colours for these variants; I did not verify that against the catalog files. The trait-to-direction mapping is a judgement: OFD's trait definitions come from its own `variant_schema.json` and I did not independently confirm each of the 399 and 339 cases. Roughly 197 variants stay single-colour by design. Four had incomplete hex lists (a "tri" product with two colours) and were not corrected.

## Alternatives considered

- Emit multi-colour with a null direction for the 254 trait-less variants. Rejected: invalid for Spoolman's create path, and mostly semantically wrong (glow or thermochromic states are not a spool pattern).
- Infer the direction from product names in the mapping. Rejected for the mapping layer; it became the data fix in OFD #646, where a human reviewed it.
- Treat `hex_variants` as colours. Rejected: different concept.

## References

- Issues: [#4](https://github.com/csmarshall/ofd-to-spoolman/issues/4), [#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)
- Pull request: [#32](https://github.com/csmarshall/ofd-to-spoolman/pull/32) (create-path check)
- OFD pull requests: [#645](https://github.com/OpenFilamentCollective/open-filament-database/pull/645), [#646](https://github.com/OpenFilamentCollective/open-filament-database/pull/646)
