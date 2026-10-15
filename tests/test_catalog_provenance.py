"""Tests for catalog_provenance: the glue the nightly workflow runs around ofd2spoolman.py."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import catalog_provenance as cp

OFD_COMMIT = "db31453ba8a2afc656fa925639b253343cb83659"
SDB_COMMIT = "8f1a99f9cda7a58ca3118d6bd28c707647caa3db"
CONVERTER = "3a3f2a2c0ffee00000000000000000000000000a"


DEPLOYMENTS = f"repos/{cp.SPOOLMANDB_REPO}/deployments?environment=github-pages&per_page={cp.DEPLOYMENTS_TO_SCAN}"


def status_path(deployment_id: int) -> str:
    return f"repos/{cp.SPOOLMANDB_REPO}/deployments/{deployment_id}/statuses?per_page=1"


def fake_api(overrides: dict[str, Any] | None = None) -> Any:
    """A GitHub API stand-in keyed by request path; any path not listed raises, like a 404."""
    responses: dict[str, Any] = {
        f"repos/{cp.OFD_REPO}/git/ref/tags/dataset-v2026.10.13": {"object": {"type": "commit", "sha": OFD_COMMIT}},
        DEPLOYMENTS: [{"id": 11, "sha": SDB_COMMIT}],
        status_path(11): [{"state": "success"}],
    }
    responses.update(overrides or {})

    def api(path: str) -> Any:
        if path not in responses:
            raise OSError(f"404 {path}")
        if isinstance(responses[path], Exception):
            raise responses[path]
        return responses[path]

    return api


def write_state(path: Path, **fields: Any) -> None:
    path.write_text(json.dumps({"run_at": "2026-10-13T04:01:57+00:00", "ofd_version": "2026.10.13", **fields}, indent=2))


def _all_down() -> Any:
    """An API where every lookup fails."""

    def api(path: str) -> Any:
        raise http.client.IncompleteRead(b"")

    return api


class TestResolve:
    def test_ofd_commit_from_lightweight_tag(self) -> None:
        assert cp.ofd_commit("2026.10.13", fake_api()) == OFD_COMMIT

    def test_ofd_commit_dereferences_annotated_tag(self) -> None:
        tag_sha = "a" * 40
        api = fake_api(
            {
                f"repos/{cp.OFD_REPO}/git/ref/tags/dataset-v2026.10.13": {"object": {"type": "tag", "sha": tag_sha}},
                f"repos/{cp.OFD_REPO}/git/tags/{tag_sha}": {"object": {"type": "commit", "sha": OFD_COMMIT}},
            }
        )
        assert cp.ofd_commit("2026.10.13", api) == OFD_COMMIT

    def test_unknown_tag_is_none_not_a_crash(self) -> None:
        assert cp.ofd_commit("1999.01.01", fake_api()) is None

    def test_spoolmandb_commit(self) -> None:
        assert cp.spoolmandb_commit(fake_api()) == SDB_COMMIT

    def test_spoolmandb_no_deployments_is_none(self) -> None:
        assert cp.spoolmandb_commit(fake_api({DEPLOYMENTS: []})) is None

    def test_spoolmandb_picks_newest_successful_deployment(self) -> None:
        """Newest is a failure, the next two succeeded: the answer is the middle one (kills a [0] or [-1] mutant)."""
        api = fake_api(
            {
                DEPLOYMENTS: [{"id": 3, "sha": "3" * 40}, {"id": 2, "sha": "2" * 40}, {"id": 1, "sha": "1" * 40}],
                status_path(3): [{"state": "failure"}],
                status_path(2): [{"state": "success"}],
                status_path(1): [{"state": "success"}],
            }
        )
        assert cp.spoolmandb_commit(api) == "2" * 40

    def test_spoolmandb_newest_successful_beats_older_successful(self) -> None:
        api = fake_api(
            {
                DEPLOYMENTS: [{"id": 3, "sha": "3" * 40}, {"id": 1, "sha": "1" * 40}],
                status_path(3): [{"state": "success"}],
                status_path(1): [{"state": "success"}],
            }
        )
        assert cp.spoolmandb_commit(api) == "3" * 40

    def test_spoolmandb_no_successful_deployment_is_none(self) -> None:
        api = fake_api({DEPLOYMENTS: [{"id": 3, "sha": "3" * 40}], status_path(3): [{"state": "in_progress"}]})
        assert cp.spoolmandb_commit(api) is None

    @pytest.mark.parametrize("error", [http.client.IncompleteRead(b"par"), json.JSONDecodeError("x", "", 0), TimeoutError("slow")])
    def test_transport_errors_are_swallowed_not_raised(self, error: Exception) -> None:
        api = fake_api({DEPLOYMENTS: error, f"repos/{cp.OFD_REPO}/git/ref/tags/dataset-v2026.10.13": error})
        assert cp.spoolmandb_commit(api) is None
        assert cp.ofd_commit("2026.10.13", api) is None

    def test_github_api_http_error_becomes_a_null_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(req: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(req.full_url, 403, "rate limit exceeded", None, None)  # type: ignore[arg-type]

        monkeypatch.setattr(urllib.request, "urlopen", refuse)
        with pytest.raises(urllib.error.HTTPError):
            cp.github_api("repos/x/y")
        assert cp.ofd_commit("2026.10.13", cp.github_api) is None
        assert cp.spoolmandb_commit(cp.github_api) is None

    def test_version_with_odd_characters_is_refused_not_interpolated(self) -> None:
        assert cp.ofd_commit("../../x?y=1", fake_api()) is None


class TestAnnotate:
    def test_adds_sources_and_converter_and_keeps_the_rest(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        write_state(state, counts={"ofd_filaments": 5})
        cp.annotate(state, CONVERTER, fake_api())
        data = json.loads(state.read_text())
        assert data["sources"] == {
            "ofd": {"tag": "dataset-v2026.10.13", "commit": OFD_COMMIT},
            "spoolmandb": {"commit": SDB_COMMIT},
        }
        assert data["converter"] == {"commit": CONVERTER}
        assert data["counts"] == {"ofd_filaments": 5}

    def test_lookup_failure_records_null_and_still_succeeds(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        write_state(state)
        cp.annotate(state, CONVERTER, fake_api({f"repos/{cp.OFD_REPO}/git/ref/tags/dataset-v2026.10.13": OSError("rate limited")}))
        data = json.loads(state.read_text())
        assert data["sources"]["ofd"]["commit"] is None
        assert data["sources"]["spoolmandb"]["commit"] == SDB_COMMIT

    def committed(self, tag: str = "dataset-v2026.10.13") -> dict[str, Any]:
        return {"sources": {"ofd": {"tag": tag, "commit": OFD_COMMIT}, "spoolmandb": {"commit": SDB_COMMIT}}}

    def test_failed_lookups_reuse_committed_values_for_the_same_tag(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        write_state(state)
        cp.annotate(state, CONVERTER, _all_down(), self.committed())
        data = json.loads(state.read_text())
        assert data["sources"] == {"ofd": {"tag": "dataset-v2026.10.13", "commit": OFD_COMMIT}, "spoolmandb": {"commit": SDB_COMMIT}}

    def test_committed_values_for_another_tag_are_not_reused(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        write_state(state)
        cp.annotate(state, CONVERTER, _all_down(), self.committed("dataset-v2026.10.12"))
        data = json.loads(state.read_text())
        assert data["sources"]["ofd"]["commit"] is None
        assert data["sources"]["spoolmandb"]["commit"] is None

    def test_fresh_lookup_wins_over_committed(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        write_state(state)
        stale = self.committed()
        stale["sources"]["spoolmandb"]["commit"] = "0" * 40
        cp.annotate(state, CONVERTER, fake_api(), stale)
        assert json.loads(state.read_text())["sources"]["spoolmandb"]["commit"] == SDB_COMMIT

    def test_missing_ofd_version_is_an_error(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        state.write_text("{}")
        with pytest.raises(ValueError, match="ofd_version"):
            cp.annotate(state, CONVERTER, fake_api())


class TestSubject:
    def test_structured_subject(self) -> None:
        state = {
            "ofd_version": "2026.10.13",
            "sources": {"ofd": {"commit": OFD_COMMIT}, "spoolmandb": {"commit": SDB_COMMIT}},
            "converter": {"commit": CONVERTER},
        }
        assert cp.subject(state) == "catalog: OFD 2026.10.13 (db31453) + SpoolmanDB 8f1a99f + ofd2spoolman 3a3f2a2"

    def test_missing_commits_say_unknown(self) -> None:
        assert cp.subject({"ofd_version": "2026.10.13"}) == "catalog: OFD 2026.10.13 (unknown) + SpoolmanDB unknown + ofd2spoolman unknown"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return done.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo whose HEAD already holds a committed catalog/."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "filaments.json").write_text('[\n{"id":"a"}\n]\n')
    (catalog / "materials.json").write_text("[]\n")
    write_state(catalog / "state.json", counts={"ofd_filaments": 1})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


class TestSettle:
    def test_only_run_at_moved_is_unchanged_and_state_is_restored(self, repo: Path) -> None:
        committed = (repo / "catalog" / "state.json").read_bytes()
        data = json.loads(committed)
        data["run_at"] = "2026-10-14T04:30:00+00:00"
        (repo / "catalog" / "state.json").write_text(json.dumps(data, indent=2))
        assert cp.settle(repo / "catalog", repo) is False
        assert (repo / "catalog" / "state.json").read_bytes() == committed
        assert git(repo, "status", "--porcelain") == ""

    def test_provenance_only_churn_is_unchanged(self, repo: Path) -> None:
        """SpoolmanDB's deployment sha moving (a docs commit) or a new converter commit must not make a commit."""
        committed = (repo / "catalog" / "state.json").read_bytes()
        data = json.loads(committed)
        data["sources"] = {"ofd": {"tag": "dataset-v2026.10.13", "commit": OFD_COMMIT}, "spoolmandb": {"commit": SDB_COMMIT}}
        data["converter"] = {"commit": CONVERTER}
        (repo / "catalog" / "state.json").write_text(json.dumps(data, indent=2))
        git(repo, "commit", "-qam", "annotated")
        data["sources"]["spoolmandb"]["commit"] = "f" * 40
        data["converter"]["commit"] = "e" * 40
        data["run_at"] = "2026-10-14T04:30:00+00:00"
        (repo / "catalog" / "state.json").write_text(json.dumps(data, indent=2))
        assert cp.settle(repo / "catalog", repo) is False
        assert git(repo, "status", "--porcelain") == ""

    def test_changed_filaments_is_changed(self, repo: Path) -> None:
        (repo / "catalog" / "filaments.json").write_text('[\n{"id":"a"},\n{"id":"b"}\n]\n')
        assert cp.settle(repo / "catalog", repo) is True

    def test_changed_materials_is_changed(self, repo: Path) -> None:
        (repo / "catalog" / "materials.json").write_text('[\n{"material":"PLA"}\n]\n')
        assert cp.settle(repo / "catalog", repo) is True

    def test_new_ofd_version_is_changed_even_with_identical_data(self, repo: Path) -> None:
        state = repo / "catalog" / "state.json"
        data = json.loads(state.read_text())
        data["ofd_version"] = "2026.10.14"
        state.write_text(json.dumps(data, indent=2))
        assert cp.settle(repo / "catalog", repo) is True

    def test_new_file_is_changed(self, repo: Path) -> None:
        (repo / "catalog" / "extra.json").write_text("[]")
        assert cp.settle(repo / "catalog", repo) is True

    def test_hidden_leftover_is_ignored(self, repo: Path) -> None:
        (repo / "catalog" / ".filaments.json.abcd1234").write_text("junk")
        assert cp.settle(repo / "catalog", repo) is False

    def test_first_ever_catalog_is_changed(self, tmp_path: Path) -> None:
        git(tmp_path, "init", "-q")
        (tmp_path / "catalog").mkdir()
        (tmp_path / "catalog" / "filaments.json").write_text("[]\n")
        git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "root")
        assert cp.settle(tmp_path / "catalog", tmp_path) is True

    def test_deleted_file_is_changed(self, repo: Path) -> None:
        (repo / "catalog" / "materials.json").unlink()
        assert cp.settle(repo / "catalog", repo) is True

    def test_corrupt_new_state_fails_instead_of_reporting_a_result(self, repo: Path) -> None:
        (repo / "catalog" / "state.json").write_text("{not json")
        with pytest.raises(ValueError, match="Expecting"):
            cp.main(["settle", "--dir", str(repo / "catalog"), "--repo", str(repo)])

    def test_corrupt_new_state_exits_non_zero_as_a_process(self, repo: Path) -> None:
        (repo / "catalog" / "state.json").write_text("{not json")
        done = subprocess.run(
            [sys.executable, "-I", cp.__file__, "settle", "--dir", str(repo / "catalog"), "--repo", str(repo)],
            capture_output=True,
            text=True,
        )
        assert done.returncode != 0
        assert done.stdout.strip() == ""

    def test_corrupt_committed_state_is_replaced(self, repo: Path) -> None:
        (repo / "catalog" / "state.json").write_text("{not json")
        git(repo, "commit", "-qam", "corrupt")
        write_state(repo / "catalog" / "state.json")
        assert cp.settle(repo / "catalog", repo) is True


class TestCli:
    def test_subject_subcommand_prints_the_subject(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        state = tmp_path / "state.json"
        write_state(state, converter={"commit": CONVERTER})
        assert cp.main(["subject", "--state", str(state)]) == 0
        assert capsys.readouterr().out.strip().startswith("catalog: OFD 2026.10.13 (unknown)")

    def test_settle_subcommand_prints_changed_or_unchanged(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert cp.main(["settle", "--dir", str(repo / "catalog"), "--repo", str(repo)]) == 0
        assert capsys.readouterr().out.strip() == "unchanged"
        (repo / "catalog" / "filaments.json").write_text("[]\n")
        assert cp.main(["settle", "--dir", str(repo / "catalog"), "--repo", str(repo)]) == 0
        assert capsys.readouterr().out.strip() == "changed"
