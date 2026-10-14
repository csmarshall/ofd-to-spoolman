#!/usr/bin/env python3
"""Report data conflicts between SpoolmanDB and OFD for products present in both.

Fetches both upstreams with the converter's own fetch/URLs, converts OFD with the converter's
convert_ofd, and matches products with the converter's dedupe_key. For every exact match it
diffs density, extruder/bed temperature, spool weight and colour hex. Weight and diameter are
part of the match key, so a disagreement there cannot show up as a field diff: instead,
products that match on manufacturer + material + name but where EACH side lists a weight/diameter
the other lacks are reported as one "size" conflict. A size listed by only one side is a coverage
gap, not a conflict, and is not reported.

Outputs (into --out): state.json (the full current conflict-key set, the next run's "previous"),
issue-body.md (digest for the tracking issue) and, when there are conflicts new since --previous,
new-conflicts.md (posted as an issue comment so the maintainer is notified). Nothing is ever
filed upstream; the workflow only talks to this repository.

stdlib only; imports ofd2spoolman read-only from the repo root.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ofd2spoolman
from ofd2spoolman import OFD_URL, SPOOLMANDB_URL, convert_ofd, dedupe_key, norm, norm_hex

log = logging.getLogger("conflict_report")

# GitHub rejects issue and comment bodies over 65536 characters. The budget is measured in UTF-8 BYTES,
# which is always >= the character count, so it is conservative for non-ASCII names.
GITHUB_BODY_LIMIT = 65536
BODY_BUDGET_BYTES = 64000

# Same shrink rule as the converter's publish guard (ofd2spoolman.main refuses `len < prev // 2`, inline and
# unnamed there, so it cannot be imported): a source whose size falls below this fraction of the previous
# run's is treated as a broken upstream.
MIN_RETAINED_FRACTION = 0.5

# Differences are rounded to this many decimals before comparing, so float noise (1.25 - 1.24 is
# 0.010000000000000009) cannot push a difference exactly at the tolerance over it.
COMPARE_DECIMALS = 6

# Differences at or below these are rounding/representation noise, not conflicts. OFD stores a
# temperature range which the converter collapses to a midpoint, so small temperature gaps are
# expected; these are judgement calls, kept in one place.
DENSITY_TOLERANCE = 0.01
TEMP_TOLERANCE = 10
SPOOL_WEIGHT_TOLERANCE = 10
HEX_CHANNEL_TOLERANCE = 12

FIELDS = ("density", "extruder_temp", "bed_temp", "spool_weight", "color_hex", "size")

Conflict = dict[str, Any]

OFD_DATA_URL = "https://github.com/OpenFilamentCollective/open-filament-database/tree/main/data"


def hex_channels(value: str) -> tuple[int, ...]:
    """RGB(A) channels of a normalised hex string."""
    return tuple(int(value[i : i + 2], 16) for i in range(0, len(value), 2))


def hex_conflict(sdb_hex: str | None, ofd_hex: str | None) -> bool:
    """True when both hexes are valid and some channel differs by more than the tolerance."""
    a, b = norm_hex(sdb_hex), norm_hex(ofd_hex)
    if not a or not b:
        return False
    ca, cb = hex_channels(a[:6]), hex_channels(b[:6])
    return max(abs(x - y) for x, y in zip(ca, cb, strict=True)) > HEX_CHANNEL_TOLERANCE


def field_differs(field: str, sdb_value: Any, ofd_value: Any) -> bool:
    """Whether two values for `field` conflict. A missing value on either side is never a conflict."""
    if sdb_value is None or ofd_value is None:
        return False
    if field == "color_hex":
        return hex_conflict(sdb_value, ofd_value)
    tolerance = {
        "density": DENSITY_TOLERANCE,
        "extruder_temp": TEMP_TOLERANCE,
        "bed_temp": TEMP_TOLERANCE,
        "spool_weight": SPOOL_WEIGHT_TOLERANCE,
    }[field]
    a, b = to_number(sdb_value), to_number(ofd_value)
    if a is None or b is None:
        return False
    return round(abs(a - b), COMPARE_DECIMALS) > tolerance


def to_number(value: Any) -> float | None:
    """A finite float, or None for anything upstream put there that is not a plain number."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def sdb_link(entry: dict[str, Any]) -> str:
    """Code-search link into SpoolmanDB for an entry (its published id is generated, so search by name)."""
    query = f'repo:Donkie/SpoolmanDB "{entry["name"]}" {entry["manufacturer"]}'
    return "https://github.com/search?q=" + urllib.parse.quote(query) + "&type=code"


def build_ofd_links(ofd: dict[str, Any]) -> dict[str, str]:
    """Map converter id (`ofd_<size id>`) to the variant's directory in the OFD data repo."""
    brands = {b["id"]: b for b in ofd["brands"]}
    filaments = {f["id"]: f for f in ofd["filaments"]}
    variants = {v["id"]: v for v in ofd["variants"]}
    links: dict[str, str] = {}
    for size in ofd["sizes"]:
        variant = variants.get(size["variant_id"])
        fil = filaments.get(variant["filament_id"]) if variant else None
        brand = brands.get(fil["brand_id"]) if fil else None
        if variant and fil and brand and all(x.get("slug") for x in (variant, fil, brand)):
            path = "/".join(urllib.parse.quote(p) for p in (brand["slug"], fil["material"], fil["slug"], variant["slug"]))
            links[f"ofd_{size['id']}"] = f"{OFD_DATA_URL}/{path}"
    return links


def loose_key(f: dict[str, Any]) -> tuple[str, str, str]:
    """Match key without weight/diameter: manufacturer + material + name."""
    return (norm(f["manufacturer"]), norm(f["material"]), norm(f["name"]))


def conflict_key(c: Conflict) -> str:
    """Stable identity of a conflict INCLUDING both values, so a changed value counts as new."""
    return "|".join([c["field"], c["identity"], str(c["sdb_value"]), str(c["ofd_value"])])


def make_conflict(
    field: str,
    sdb: dict[str, Any],
    ofd: dict[str, Any],
    sdb_value: Any,
    ofd_value: Any,
    ofd_links: dict[str, str],
) -> Conflict:
    return {
        "field": field,
        "identity": f"{sdb['id']}|{ofd['id']}",
        "manufacturer": sdb["manufacturer"],
        "material": sdb["material"],
        "name": sdb["name"],
        "sdb_id": sdb["id"],
        "ofd_id": ofd["id"],
        "sdb_value": sdb_value,
        "ofd_value": ofd_value,
        "sdb_link": sdb_link(sdb),
        "ofd_link": ofd_links.get(ofd["id"]),
    }


def format_size(size: tuple[float, float]) -> str:
    return f"{size[0]:g} g / {size[1]:g} mm"


def find_conflicts(sdb_filaments: list[dict[str, Any]], ofd_filaments: list[dict[str, Any]], ofd_links: dict[str, str]) -> list[Conflict]:
    """Field conflicts for exact (dedupe_key) matches plus size conflicts for near matches."""
    conflicts: list[Conflict] = []
    # Sorted by id so that when several entries share a key the same one always wins (ids must not flip between runs).
    sdb_filaments = sorted(sdb_filaments, key=lambda f: str(f["id"]))
    ofd_filaments = sorted(ofd_filaments, key=lambda f: str(f["id"]))

    sdb_by_key: dict[tuple[str, str, str, float, float], dict[str, Any]] = {}
    for s in sdb_filaments:
        sdb_by_key.setdefault(dedupe_key(s), s)
    for o in ofd_filaments:
        match = sdb_by_key.get(dedupe_key(o))
        if match is None:
            continue
        for field in FIELDS[:-1]:
            if field_differs(field, match.get(field), o.get(field)):
                conflicts.append(make_conflict(field, match, o, match.get(field), o.get(field), ofd_links))

    sdb_sizes: dict[tuple[str, str, str], dict[tuple[float, float], dict[str, Any]]] = {}
    for s in sdb_filaments:
        sdb_sizes.setdefault(loose_key(s), {}).setdefault((float(s["weight"]), float(s["diameter"])), s)
    ofd_sizes: dict[tuple[str, str, str], dict[tuple[float, float], dict[str, Any]]] = {}
    for o in ofd_filaments:
        ofd_sizes.setdefault(loose_key(o), {}).setdefault((float(o["weight"]), float(o["diameter"])), o)
    for key, sdb_side in sdb_sizes.items():
        ofd_side = ofd_sizes.get(key)
        if not ofd_side:
            continue
        # Disjoint sets only: a shared size means the catalogs agree on the product and one just lists more.
        if set(sdb_side) & set(ofd_side):
            continue
        sdb_sizes_sorted, ofd_sizes_sorted = sorted(sdb_side), sorted(ofd_side)
        s, o = sdb_side[sdb_sizes_sorted[0]], ofd_side[ofd_sizes_sorted[0]]
        conflicts.append(
            make_conflict(
                "size",
                s,
                o,
                ", ".join(map(format_size, sdb_sizes_sorted)),
                ", ".join(map(format_size, ofd_sizes_sorted)),
                ofd_links,
            )
        )

    conflicts.sort(key=lambda c: (norm(c["manufacturer"]), norm(c["name"]), FIELDS.index(c["field"]), c["ofd_id"]))
    return conflicts


def new_conflicts(conflicts: list[Conflict], previous_keys: set[str]) -> list[Conflict]:
    """Conflicts whose key was not in the previous run's set."""
    return [c for c in conflicts if conflict_key(c) not in previous_keys]


MARKDOWN_SPECIALS = "\\`*_{}[]()<>|~&!"


def clean(text: Any) -> str:
    """Make an upstream string inert in GitHub markdown.

    Control/format/line-separator characters (including \\r) become spaces, markdown syntax is backslash-escaped,
    and @ (mentions), # (issue refs), `://` and `www.` (autolinks) are replaced by HTML entities after escaping.
    """
    flattened = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in str(text))
    escaped = "".join("\\" + ch if ch in MARKDOWN_SPECIALS else ch for ch in " ".join(flattened.split()))
    escaped = escaped.replace("@", "&#64;").replace("#", "&#35;").replace("://", ":&#47;&#47;")
    return re.sub(r"(?i)www\.", "www&#46;", escaped)


def render_line(c: Conflict) -> str:
    links = f"[SpoolmanDB]({c['sdb_link']})" + (f" · [OFD]({c['ofd_link']})" if c["ofd_link"] else "")
    product = f"**{clean(c['manufacturer'])}** {clean(c['material'])} {clean(c['name'])}"
    return f"- {product}: SpoolmanDB {clean(c['sdb_value'])} vs OFD {clean(c['ofd_value'])} ({links})"


def size_bytes(text: str) -> int:
    return len(text.encode())


def render_sections(conflicts: list[Conflict], budget: int | None) -> tuple[str, int]:
    """Render conflicts grouped by field within `budget` UTF-8 bytes; returns (markdown, number omitted).

    Every non-empty field always gets its header with the TRUE total, and the remaining budget is shared
    round-robin so no field is starved by the ones before it. `budget=None` renders everything.
    """
    groups = {f: [c for c in conflicts if c["field"] == f] for f in FIELDS}
    groups = {f: g for f, g in groups.items() if g}
    headers = {f: f"\n### {f} ({len(g)})\n" for f, g in groups.items()}
    remaining = None if budget is None else budget - sum(size_bytes(h) for h in headers.values())
    lines: dict[str, list[str]] = {f: [] for f in groups}
    cursor = dict.fromkeys(groups, 0)
    active = list(groups)
    while active:
        still_active = []
        for f in active:
            if cursor[f] >= len(groups[f]):
                continue
            line = render_line(groups[f][cursor[f]]) + "\n"
            cost = size_bytes(line)
            if remaining is not None and cost > remaining:
                continue  # this field is done; a shorter line in another field may still fit
            if remaining is not None:
                remaining -= cost
            lines[f].append(line)
            cursor[f] += 1
            still_active.append(f)
        active = still_active
    shown = sum(len(v) for v in lines.values())
    return "".join(headers[f] + "".join(lines[f]) for f in groups), len(conflicts) - shown


def truncation_notice(omitted: int, full_report_url: str | None) -> str:
    where = f" The full untruncated list is the `conflicts-full` artifact of [this run]({full_report_url})." if full_report_url else ""
    return (
        f"\n_Truncated: {omitted} more conflict(s) not shown because GitHub limits issue and comment bodies to "
        f"{GITHUB_BODY_LIMIT} characters; each field above shows a share of its entries and its true total in the heading.{where}_\n"
    )


def render_digest(
    heading: str, intro: str, conflicts: list[Conflict], full_report_url: str | None = None, budget: int | None = BODY_BUDGET_BYTES
) -> str:
    """Heading + intro + per-field sections, truncated (budget in bytes) to stay under GitHub's body limit."""
    head = f"{heading}\n\n{intro}\n"
    if budget is None:
        return head + render_sections(conflicts, None)[0]
    reserve = size_bytes(truncation_notice(10**9, full_report_url))
    sections, omitted = render_sections(conflicts, budget - size_bytes(head) - reserve)
    return head + sections + (truncation_notice(omitted, full_report_url) if omitted else "")


def render_issue_body(
    conflicts: list[Conflict],
    new_count: int,
    ofd_version: str | None,
    baseline: bool,
    full_report_url: str | None = None,
    budget: int | None = BODY_BUDGET_BYTES,
) -> str:
    counts = ", ".join(f"{f}: {n}" for f in FIELDS if (n := sum(1 for c in conflicts if c["field"] == f)))
    status = (
        "First run: everything below is the baseline." if baseline else f"{new_count} new since the previous run (posted as a comment)."
    )
    intro = (
        f"Updated by the conflict-report workflow against OFD dataset {clean(ofd_version)}. "
        f"{len(conflicts)} current conflict(s) ({counts or 'none'}). {status}\n\n"
        "Products present in both catalogs (matched with the converter's own dedupe key) whose values disagree beyond a small tolerance. "
        "A missing value on either side is not a conflict. `size` means the two sides list entirely different weight/diameter combinations "
        "for the same manufacturer + material + name. "
        "This is evidence for upstream fixes to either project; nothing is filed upstream automatically."
    )
    return render_digest("## Current conflicts", intro, conflicts, full_report_url, budget)


def render_new_comment(
    new: list[Conflict], ofd_version: str | None, full_report_url: str | None = None, budget: int | None = BODY_BUDGET_BYTES
) -> str:
    return render_digest(f"## {len(new)} new conflict(s)", f"Found against OFD dataset {clean(ofd_version)}.", new, full_report_url, budget)


@dataclass
class PreviousState:
    keys: set[str]
    counts: dict[str, int]


def load_previous(path: Path | None) -> PreviousState | None:
    """Previous run's state, or None when there is none usable (baseline run)."""
    if path is None or not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
        keys = raw["keys"]
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise TypeError("keys must be a list of strings")
        counts = raw.get("counts", {})
        if not isinstance(counts, dict) or not all(isinstance(v, int) for v in counts.values()):
            raise TypeError("counts must map names to ints")
        return PreviousState(set(keys), counts)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        log.warning("previous state %s unreadable; treating this as a baseline run", path)
        return None


def shrink_problem(counts: dict[str, int], previous: PreviousState | None) -> str | None:
    """Describe why the source counts look like a broken upstream, or None if they look sane."""
    for name, count in counts.items():
        if count == 0:
            return f"{name} is empty"
        before = previous.counts.get(name) if previous else None
        if before and count < int(before * MIN_RETAINED_FRACTION):
            return f"{name} fell from {before} to {count} (below {MIN_RETAINED_FRACTION:.0%} of the previous run)"
    return None


def main(argv: Sequence[str] | None = None, fetcher: Callable[[str], bytes] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="directory for state.json, issue-body.md and new-conflicts.md")
    ap.add_argument("--previous", type=Path, help="state.json from the previous run (absent: baseline run)")
    ap.add_argument("--run-url", help="URL of the workflow run, linked from truncated digests to the full artifact")
    ap.add_argument("--dry-run", action="store_true", help="print the issue body and new-conflicts comment instead of writing files")
    ap.add_argument("--from-ofd-file", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--from-sdb-dir", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if not args.dry_run and args.out is None:
        ap.error("--out is required unless --dry-run")
    if (args.from_ofd_file is None) != (args.from_sdb_dir is None):
        ap.error("--from-ofd-file and --from-sdb-dir must be given together")
    if args.from_ofd_file is not None:
        fetcher = ofd2spoolman.local_fetcher(args.from_ofd_file, args.from_sdb_dir)
    fetch_source = fetcher or ofd2spoolman.fetch

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        sdb_filaments = json.loads(fetch_source(SPOOLMANDB_URL + "filaments.json"))
        ofd = json.loads(fetch_source(OFD_URL))
    except Exception as exc:
        log.error("fetch failed: %s", exc)
        return 2

    ofd_filaments = convert_ofd(ofd, include_discontinued=False)
    sdb_keys = {dedupe_key(f) for f in sdb_filaments}
    counts = {
        "sdb_filaments": len(sdb_filaments),
        "ofd_filaments": len(ofd_filaments),
        "matched": sum(dedupe_key(f) in sdb_keys for f in ofd_filaments),
    }
    previous = load_previous(args.previous)
    problem = shrink_problem(counts, previous)
    if problem:
        log.error("upstream looks broken (%s); refusing to report or write state", problem)
        return 5
    conflicts = find_conflicts(sdb_filaments, ofd_filaments, build_ofd_links(ofd))
    baseline = previous is None
    new = conflicts if previous is None else new_conflicts(conflicts, previous.keys)
    log.info(
        "%d conflicts (%s); %d new%s",
        len(conflicts),
        {f: sum(1 for c in conflicts if c["field"] == f) for f in FIELDS},
        len(new),
        " (baseline run)" if baseline else "",
    )

    version = ofd.get("version")
    body = render_issue_body(conflicts, len(new), version, baseline, args.run_url)
    comment = None if baseline or not new else render_new_comment(new, version, args.run_url)

    if args.dry_run:
        print(body)
        if comment:
            print("\n----- new-conflicts comment -----\n" + comment)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    state = {"ofd_version": ofd.get("version"), "counts": counts, "keys": sorted(conflict_key(c) for c in conflicts)}
    (args.out / "state.json").write_text(json.dumps(state))
    (args.out / "issue-body.md").write_text(body)
    full_intro = f"All {len(conflicts)} current conflict(s) against OFD dataset {clean(version)}, untruncated."
    (args.out / "conflicts-full.md").write_text(render_digest("## Full conflict report", full_intro, conflicts, budget=None))
    new_path = args.out / "new-conflicts.md"
    new_path.unlink(missing_ok=True)
    if comment:
        new_path.write_text(comment)
    # The workflow keys its issue-comment step on this being a file; also machine-readable counts.
    (args.out / "summary.json").write_text(json.dumps({"total": len(conflicts), "new": len(new), "baseline": baseline}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
