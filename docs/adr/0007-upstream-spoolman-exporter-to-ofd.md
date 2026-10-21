# 0007. Upstream route: propose a Spoolman exporter to OFD

- Status: Proposed
- Date: 2026-10-15 (plan approved; updated 2026-10-21)

## Context

OFD already ships exporters for other formats, among them OrcaSlicer presets, with the field mapping kept in its own module (`ofd/builder/orca_mapping.py`, `orca_exporter.py`) ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)). OFD issue [#353](https://github.com/OpenFilamentCollective/open-filament-database/issues/353) asked for Spoolman use in June 2026 and was answered that Spoolman would have to add support; nobody proposed the reverse. The precedent is OFD issue [#365](https://github.com/OpenFilamentCollective/open-filament-database/issues/365) (2026-07-08, how to use OFD in OrcaSlicer), where a maintainer said a mapping "wouldn't be impossible" and then built it in PR [#503](https://github.com/OpenFilamentCollective/open-filament-database/pull/503), merged 2026-09-05. Per [#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5), OFD's `build-dataset.yml` runs daily and deploys to Pages, so a `spoolman/` exporter would be served and versioned with its releases.

## Decision

On 2026-10-15 the maintainer approved building the exporter as if directly into OFD, on a public fork (`feat/spoolman-exporter`, code only, shaped like the orca exporter: a mapping module, an exporter, golden tests), soaking it for a few weeks, then offering it upstream: first a feature request citing #365/#503 (the same shape of ask), then a small PR, addressed to the maintainer who built the OrcaSlicer exporter ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)). It emits the same `ofd_<size uuid>` ids ([ADR 0002](0002-ofd-size-uuid-ids.md)), the same name shortening ([ADR 0003](0003-deterministic-name-shortening.md)) and the multi-colour mapping ([ADR 0005](0005-multi-colour-mapping.md)). The 3-commit rebased version exists locally; as of 2026-10-21 the PR is opened only on the maintainer's go. Prerequisite data fixes already went upstream as OFD [#644](https://github.com/OpenFilamentCollective/open-filament-database/pull/644), [#645](https://github.com/OpenFilamentCollective/open-filament-database/pull/645) and [#646](https://github.com/OpenFilamentCollective/open-filament-database/pull/646), all merged. This repo stays the merge and deployment layer ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)); [#26](https://github.com/csmarshall/ofd-to-spoolman/issues/26) (split a portable mapping module here) was closed as superseded by writing it in OFD's structure from the start. On 2026-10-21 the maintainer decided not to move the home Spoolman to the Pages catalog yet: first get the exporter accepted, then switch.

Trigger: because the exporter would run inside OFD's own build-dataset workflow, `spoolman/` is published in the same deploy that cuts the dataset release, so it is release-driven by construction with no extra trigger. The scheduled trigger used here is discussed in [ADR 0006](0006-publish-via-scheduled-action-to-pages.md); that a release-triggered action in this repo is impossible (the `release` event fires only in the owning repo) and that tightening the schedule is only an open consideration are recorded there.

Open design question (to settle once the exporter lands): OFD's own `spoolman/` output would contain OFD data only. The merged catalog also carries about 6,500 SpoolmanDB-only entries (8,120 SpoolmanDB filaments, 1,610 duplicates of OFD; [#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5), comment of 2026-10-21), so pointing `EXTERNAL_DB_URL` at OFD's endpoint would drop them. Consumers therefore either keep this repo's Pages merge layer as their URL or accept OFD-only. Not decided. SpoolmanDB merging inside OFD's build is a cross-project dependency OFD is unlikely to want ([#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5)); that is a projection about the maintainers' preferences, not something they have said.

## Consequences

Good, if accepted: any Spoolman install could use OFD directly, versioned with OFD's releases, and the mapping would be maintained next to the data. Bad / risks: acceptance is not assured and nothing has been proposed yet; the maintainer may prefer not to carry Spoolman code, and the PR is unseen by OFD reviewers so far. The id scheme and the 64-character rule become public contracts in OFD's tree. Parity between the fork and this repo's converter is a consistency check (0 of 22,980 shared names differing on 2026-10-15), not proof both are right; the real-Spoolman create-path replay is the independent check. Note that multi-colour is only implemented in the fork ([ADR 0005](0005-multi-colour-mapping.md)). Our earlier data and builder PRs were merged upstream (23 of 24 in the #644 to #679 range as of 2026-10-21), which says nothing about whether this exporter will be.

## Alternatives considered

- Keep only this repo's Pages catalog. Works today, and is the fallback if the proposal is declined.
- Ask Spoolman to read OFD's format: OFD's maintainer already said Spoolman would have to add support, and there is no sign of that.
- Run OFD's builder in our Action (phase 2 in [#24](https://github.com/csmarshall/ofd-to-spoolman/issues/24)): a bridge variant, not started.

## References

- Issues: [#5](https://github.com/csmarshall/ofd-to-spoolman/issues/5), [#26](https://github.com/csmarshall/ofd-to-spoolman/issues/26), [#27](https://github.com/csmarshall/ofd-to-spoolman/issues/27), [#24](https://github.com/csmarshall/ofd-to-spoolman/issues/24)
- OFD: issues [#353](https://github.com/OpenFilamentCollective/open-filament-database/issues/353), [#365](https://github.com/OpenFilamentCollective/open-filament-database/issues/365); pull requests [#503](https://github.com/OpenFilamentCollective/open-filament-database/pull/503), [#644](https://github.com/OpenFilamentCollective/open-filament-database/pull/644), [#645](https://github.com/OpenFilamentCollective/open-filament-database/pull/645), [#646](https://github.com/OpenFilamentCollective/open-filament-database/pull/646)
