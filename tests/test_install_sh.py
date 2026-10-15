"""install.sh: target resolution, drift check, and that install.local is data, not code."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A private copy of the files install.sh needs, so install.local never touches the real checkout."""
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("install.sh", "ofd2spoolman.py"):
        shutil.copy(REPO / name, root / name)
    return root


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(repo / "install.sh"), *args], capture_output=True, text=True, check=False)


def test_no_target_is_usage_error(repo: Path) -> None:
    result = run(repo)
    assert result.returncode == 64
    assert "no target" in result.stdout + result.stderr


def test_target_without_value_is_usage_error(repo: Path) -> None:
    result = run(repo, "--target")
    assert result.returncode == 64
    assert "unbound" not in result.stderr


def test_install_then_up_to_date_then_drift(repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "deployed.py"
    assert run(repo, "--target", str(target)).returncode == 0
    text = target.read_text()
    assert "MANAGED by ofd-to-spoolman" in text
    assert target.stat().st_mode & 0o111
    again = run(repo, "--target", str(target))
    assert again.returncode == 0
    assert "up to date" in again.stdout
    target.write_text(text + "# edited in place\n")
    drift = run(repo, "--check", "--target", str(target))
    assert drift.returncode == 1
    assert "edited in place" in drift.stdout
    assert target.read_text().endswith("# edited in place\n")  # --check never writes


def test_check_missing_target(repo: Path, tmp_path: Path) -> None:
    assert run(repo, "--check", "--target", str(tmp_path / "nope.py")).returncode == 1


@pytest.mark.parametrize("fmt", ['TARGET="{}"', "TARGET='{}'", "TARGET={}"])
def test_install_local_target_with_spaces_and_quotes(repo: Path, tmp_path: Path, fmt: str) -> None:
    target = tmp_path / "dir with space" / "deployed.py"
    target.parent.mkdir()
    (repo / "install.local").write_text("# comment\nTARGET=/ignored/earlier\n" + fmt.format(target) + "\nOTHER=1\n")
    assert run(repo).returncode == 0
    assert target.is_file()


def test_install_local_tilde_expansion(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (repo / "install.local").write_text("TARGET=~/deployed.py\n")
    assert run(repo).returncode == 0
    assert (tmp_path / "deployed.py").is_file()


def test_target_flag_overrides_install_local(repo: Path, tmp_path: Path) -> None:
    (repo / "install.local").write_text(f"TARGET={tmp_path / 'local.py'}\n")
    flagged = tmp_path / "flag.py"
    assert run(repo, "--target", str(flagged)).returncode == 0
    assert flagged.is_file()
    assert not (tmp_path / "local.py").exists()


def test_install_local_is_not_executed(repo: Path, tmp_path: Path) -> None:
    pwned = tmp_path / "PWNED"
    target = tmp_path / "deployed.py"
    (repo / "install.local").write_text(f"touch {pwned}\n$(touch {pwned})\nSRC=/etc/hostname\nCHECK=1\nTARGET={target}\n")
    result = run(repo)
    assert result.returncode == 0
    assert not pwned.exists()
    assert "MANAGED by ofd-to-spoolman" in target.read_text()  # SRC/CHECK assignments were not honoured


def test_help_works_with_broken_install_local(repo: Path) -> None:
    (repo / "install.local").write_text("this is ( not valid shell\n")
    result = run(repo, "-h")
    assert result.returncode == 0
    assert "Usage" in result.stdout
