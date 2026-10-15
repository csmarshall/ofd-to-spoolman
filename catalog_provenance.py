#!/usr/bin/env python3
"""Glue the nightly catalog workflow runs around ofd2spoolman.py (#24). Standard library only.

  annotate  add the upstream commits and the converter commit to state.json, the catalog's one provenance record
  subject   print the structured commit subject for the catalog commit, from state.json
  settle    say whether the freshly built catalog is worth a commit: its filaments.json or materials.json bytes
            changed, or the OFD version did. Anything else (run_at, a moved SpoolmanDB deployment sha, the
            converter commit) is provenance churn: the committed state.json is put back and nothing is committed

Commit lookups are best effort: a failure reuses the committed value for the same OFD tag, else records null (and
the subject says "unknown"), instead of failing the run, because provenance is context for people and must never
block a good catalog from being published.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

OFD_REPO = "OpenFilamentCollective/open-filament-database"
SPOOLMANDB_REPO = "Donkie/SpoolmanDB"
STATE_FILE = "state.json"
SHORT_SHA = 7
DATA_FILES = ("filaments.json", "materials.json")
# How many recent SpoolmanDB deployments to look through for a successful one.
DEPLOYMENTS_TO_SCAN = 10
# Everything a GitHub API lookup can raise that must not fail a run that built a good catalog: HTTPError/URLError/
# timeouts are OSError, a truncated body is http.client.IncompleteRead (an HTTPException, not an OSError), and a
# surprising response shape is KeyError/IndexError/TypeError/ValueError (JSONDecodeError).
LOOKUP_ERRORS = (OSError, http.client.HTTPException, KeyError, IndexError, TypeError, ValueError)
# OFD dataset versions look like 2026.10.13; anything else is not interpolated into an API path.
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")

log = logging.getLogger("catalog_provenance")

Api = Callable[[str], Any]


def github_api(path: str) -> Any:
    """GET https://api.github.com/<path>, authenticated with GITHUB_TOKEN when it is set."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ofd-to-spoolman-catalog"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def ofd_commit(version: str, api: Api) -> str | None:
    """The commit OFD tagged as `dataset-v<version>`, or None if it cannot be resolved."""
    if not VERSION_PATTERN.match(version):
        log.warning("OFD version %r is not a plain version string; not looking up its commit", version)
        return None
    try:
        obj = api(f"repos/{OFD_REPO}/git/ref/tags/dataset-v{version}")["object"]
        if obj["type"] == "tag":  # annotated tag: one more hop to the commit
            obj = api(f"repos/{OFD_REPO}/git/tags/{obj['sha']}")["object"]
        return str(obj["sha"])
    except LOOKUP_ERRORS as exc:
        log.warning("could not resolve OFD tag dataset-v%s: %s", version, exc)
        return None


def spoolmandb_commit(api: Api) -> str | None:
    """The commit of SpoolmanDB's latest *successful* github-pages deployment, i.e. what its Pages site actually served."""
    try:
        deployments = api(f"repos/{SPOOLMANDB_REPO}/deployments?environment=github-pages&per_page={DEPLOYMENTS_TO_SCAN}")
        for deployment in deployments:  # newest first
            statuses = api(f"repos/{SPOOLMANDB_REPO}/deployments/{int(deployment['id'])}/statuses?per_page=1")
            if statuses and statuses[0]["state"] == "success":  # the newest status is the deployment's current state
                return str(deployment["sha"])
    except LOOKUP_ERRORS as exc:
        log.warning("could not resolve SpoolmanDB's deployed commit: %s", exc)
        return None
    log.warning("no successful SpoolmanDB github-pages deployment among the latest %d", DEPLOYMENTS_TO_SCAN)
    return None


def write_json_atomically(path: Path, data: dict[str, Any]) -> None:
    """Rewrite state.json in the same format ofd2spoolman.write_state uses, via temp file + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def annotate(state_path: Path, converter_commit: str, api: Api, committed: dict[str, Any] | None = None) -> None:
    """Add `sources` (upstream commits) and `converter` (this repo's commit) to the state file.

    A lookup that fails never overwrites known-good provenance: when `committed` (the state at HEAD) is for the same
    OFD tag, its value is reused instead of null.
    """
    state = json.loads(state_path.read_text())
    version = state.get("ofd_version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{state_path} has no ofd_version to look up")
    tag = f"dataset-v{version}"
    known = (committed or {}).get("sources") or {}
    if (known.get("ofd") or {}).get("tag") != tag:
        known = {}
    ofd = ofd_commit(version, api) or (known.get("ofd") or {}).get("commit")
    spoolmandb = spoolmandb_commit(api) or (known.get("spoolmandb") or {}).get("commit")
    state["sources"] = {"ofd": {"tag": tag, "commit": ofd}, "spoolmandb": {"commit": spoolmandb}}
    state["converter"] = {"commit": converter_commit}
    write_json_atomically(state_path, state)


def short(sha: Any) -> str:
    return sha[:SHORT_SHA] if isinstance(sha, str) and sha else "unknown"


def subject(state: dict[str, Any]) -> str:
    """`catalog: OFD 2026.10.13 (db31453) + SpoolmanDB 8f1a99f + ofd2spoolman 3a3f2a2`, so `git log --oneline -- catalog/` is an index."""
    sources = state.get("sources") or {}
    ofd = short((sources.get("ofd") or {}).get("commit"))
    sdb = short((sources.get("spoolmandb") or {}).get("commit"))
    converter = short((state.get("converter") or {}).get("commit"))
    return f"catalog: OFD {state.get('ofd_version') or 'unknown'} ({ofd}) + SpoolmanDB {sdb} + ofd2spoolman {converter}"


def committed_bytes(repo: Path, relative: str) -> bytes | None:
    """The file's content at HEAD, or None if HEAD does not have it."""
    done = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{relative}"], capture_output=True, check=False)
    return done.stdout if done.returncode == 0 else None


def settle(catalog: Path, repo: Path) -> bool:
    """True if `catalog` is worth a commit: a data file changed (or appeared or vanished), or the OFD version moved.

    Otherwise state.json is restored from HEAD, so provenance-only churn (run_at, SpoolmanDB's deployment sha,
    the converter commit) leaves a clean tree. A new state.json that cannot be parsed raises (non-zero exit);
    an unparseable committed one is replaced, since the next commit repairs it.
    """
    relative_dir = catalog.resolve().relative_to(repo.resolve()).as_posix()
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD", "--", relative_dir], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    present = {f"{relative_dir}/{p.name}" for p in catalog.iterdir() if p.is_file() and not p.name.startswith(".")}
    if present != set(tracked):
        return True
    for name in DATA_FILES:
        if (repo / relative_dir / name).read_bytes() != committed_bytes(repo, f"{relative_dir}/{name}"):
            return True
    state_path = f"{relative_dir}/{STATE_FILE}"
    old = committed_bytes(repo, state_path)
    if old is None:
        return True
    new_state = json.loads((repo / state_path).read_text())
    try:
        old_state = json.loads(old)
    except ValueError:
        log.warning("committed %s is not valid JSON; committing the new one", state_path)
        return True
    if version_key(old_state) != version_key(new_state):
        return True
    (repo / state_path).write_bytes(old)
    return False


def version_key(state: dict[str, Any]) -> tuple[Any, Any]:
    """The OFD dataset version and tag: the provenance that, when it moves, deserves a commit by itself."""
    return state.get("ofd_version"), ((state.get("sources") or {}).get("ofd") or {}).get("tag")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p_annotate = sub.add_parser("annotate", help="add upstream and converter commits to state.json")
    p_annotate.add_argument("--state", type=Path, required=True)
    p_annotate.add_argument("--converter-commit", required=True)
    p_annotate.add_argument("--repo", type=Path, default=Path(), help="git checkout whose HEAD holds the committed state.json")
    p_subject = sub.add_parser("subject", help="print the catalog commit subject")
    p_subject.add_argument("--state", type=Path, required=True)
    p_settle = sub.add_parser("settle", help="print 'changed' or 'unchanged' versus HEAD")
    p_settle.add_argument("--dir", type=Path, required=True)
    p_settle.add_argument("--repo", type=Path, default=Path())
    args = ap.parse_args(argv)
    if args.command == "annotate":
        relative = args.state.resolve().relative_to(args.repo.resolve()).as_posix()
        previous = committed_bytes(args.repo, relative)
        try:
            committed = json.loads(previous) if previous else None
        except ValueError:
            committed = None
        annotate(args.state, args.converter_commit, github_api, committed if isinstance(committed, dict) else None)
    elif args.command == "subject":
        print(subject(json.loads(args.state.read_text())))
    else:
        print("changed" if settle(args.dir, args.repo) else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
