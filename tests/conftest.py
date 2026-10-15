"""Shared fixtures: tiny OFD / SpoolmanDB inputs and a runner that never touches the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import ofd2spoolman

FIXTURES = Path(__file__).parent / "fixtures"

# One hour after the fixture's generated_at, so OFD is never "stalled" unless a test moves the clock.
FIXTURE_NOW = datetime(2026, 10, 13, 4, 1, 57, tzinfo=UTC)


@pytest.fixture
def ofd() -> dict[str, Any]:
    """The trimmed OFD all.json (fresh copy per test; convert/merge mutate their inputs)."""
    data: dict[str, Any] = json.loads((FIXTURES / "ofd_all.json").read_text())
    return data


@pytest.fixture
def sources() -> dict[str, bytes]:
    """URL -> payload for every source the script fetches."""
    return {
        ofd2spoolman.OFD_URL: (FIXTURES / "ofd_all.json").read_bytes(),
        ofd2spoolman.SPOOLMANDB_URL + "filaments.json": (FIXTURES / "sdb" / "filaments.json").read_bytes(),
        ofd2spoolman.SPOOLMANDB_URL + "materials.json": (FIXTURES / "sdb" / "materials.json").read_bytes(),
    }


def fetcher_for(payloads: dict[str, bytes], last_modified: dict[str, str] | None = None) -> Callable[[str], ofd2spoolman.Fetched]:
    """A fetch replacement that serves `payloads` (with optional per-URL Last-Modified) and fails loudly on any other URL."""

    def fetch(url: str) -> ofd2spoolman.Fetched:
        return ofd2spoolman.Fetched(payloads[url], (last_modified or {}).get(url))

    return fetch


@pytest.fixture
def run(sources: dict[str, bytes]) -> Callable[..., int]:
    """run(out_dir, *flags, payloads=None, now=FIXTURE_NOW, last_modified=None) -> exit code of main() against the fixtures."""

    def _run(
        out_dir: Path,
        *flags: str,
        payloads: dict[str, bytes] | None = None,
        now: datetime = FIXTURE_NOW,
        last_modified: dict[str, str] | None = None,
    ) -> int:
        fetcher = fetcher_for(sources if payloads is None else payloads, last_modified)
        return ofd2spoolman.main(["--out", str(out_dir), *flags], fetcher=fetcher, now=now)

    return _run
