"""Spoolman caps filament names at 64 characters (#30). Both inputs are passed through unchanged (ADR 0009); over-long names only warn."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

import ofd2spoolman
from ofd2spoolman import NAME_MAX_LENGTH


def test_name_limit_is_spoolmans() -> None:
    assert NAME_MAX_LENGTH == 64


def test_spoolmandb_over_64_warns_and_is_not_altered(
    run: Callable[..., int], sources: dict[str, bytes], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = ofd2spoolman.SPOOLMANDB_URL + "filaments.json"
    sdb = json.loads(sources[url])
    sdb[1]["name"] = "S" * 70  # the SpoolmanDB-only entry, so it is in the output
    sources[url] = json.dumps(sdb).encode()
    with caplog.at_level(logging.WARNING):
        assert run(tmp_path) == 0
    out = json.loads((tmp_path / "filaments.json").read_text())
    assert {f["id"]: f for f in out}[sdb[1]["id"]]["name"] == "S" * 70  # output is sorted by id, so find it by id
    assert "SpoolmanDB name exceeds" in caplog.text and "64" in caplog.text


def test_names_within_the_limit_do_not_warn(run: Callable[..., int], tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert run(tmp_path) == 0
    assert "exceeds" not in caplog.text
