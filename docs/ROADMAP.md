# Roadmap

Ideas are sketched here; each one becomes a GitHub issue before any work starts.

## Near term

- **CI + tests.** Fixture-based unit tests (name building, the `PETG+` regression, brand canonicalisation, dedupe, shrink guard, atomic publish), plus a job that validates real output against the real Spoolman image's `ExternalFilamentsFile` / `ExternalMaterialsFile` models. The validation job also gets a known-bad fixture, so we know it can fail.
- **Catalog diff report.** Each run logs added, removed and changed counts against the last publish, so upstream regressions stand out instead of hiding behind a total that still looks plausible.
- **Better duplicate detection** between SpoolmanDB and OFD for products named differently in each ("PolyTerra Black" vs "PolyTerra™ Charcoal Black"), e.g. matching on hex + weight + material within a brand. Measure false merges before switching it on.

## Data coverage

- **Multi-colour filaments.** Map OFD's coextruded and gradient traits plus `hex_variants` to Spoolman's `color_hexes` + `multi_color_direction`, where OFD has enough data. Check `hex_variants` semantics against OFD's schema first.
- **Spool type.** OFD has no plastic/cardboard field today; only add one if a real source does.
- **Gap filling** for brands missing from both catalogs (seen 2026-10: Overture High Speed PETG, CHCKX PCTG, YOUSU). Prefer contributing to OFD over keeping a private overlay.

## Upstream

- **Spoolman export format in OFD.** OFD already publishes an OrcaSlicer export. A `spoolman/filaments.json` + `materials.json` export would let any Spoolman install set `EXTERNAL_DB_URL` straight to OFD, with no bridge needed. That's code, not data, so SpoolmanDB's no-generated-data rule doesn't apply. Raise it in an OFD issue first (see OFD #353 for the existing ask).
- **Spoolman sync race.** `/external/filament` serves the cache file while the hourly sync rewrites it non-atomically. Worth an upstream issue with a one-line fix (write to a temp file, then `os.replace`).
