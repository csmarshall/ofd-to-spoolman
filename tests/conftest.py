"""Shared fixtures: tiny OFD / SpoolmanDB inputs and a runner that never touches the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import ofd2spoolman

FIXTURES = Path(__file__).parent / "fixtures"


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


def fetcher_for(payloads: dict[str, bytes]) -> Callable[[str], bytes]:
    """A fetch replacement that serves `payloads` and fails loudly on any other URL."""

    def fetch(url: str) -> bytes:
        return payloads[url]

    return fetch


@pytest.fixture
def run(sources: dict[str, bytes]) -> Callable[..., int]:
    """run(out_dir, *flags, payloads=None) -> exit code of main() against the fixtures."""

    def _run(out_dir: Path, *flags: str, payloads: dict[str, bytes] | None = None) -> int:
        return ofd2spoolman.main(["--out", str(out_dir), *flags], fetcher=fetcher_for(sources if payloads is None else payloads))

    return _run
