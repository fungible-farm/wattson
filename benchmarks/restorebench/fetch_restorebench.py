"""Fetch PowerAgentBench at a pinned commit and verify the RestoreBench files this lab uses.

Only RestoreBench (``benchmarks/steady/level_3``) is used: it is the one part of
the repository with a licence (code MIT, scenario corpora CC BY 4.0; see its
``NOTICE`` and ``dataset/LICENSE``). The repository root has none, which is
filed upstream as https://github.com/Power-Agent/PowerAgentBench/issues/11, so
nothing outside level_3 is copied, run or redistributed here. The checkout
lives in the gitignored ``data/`` directory, like the CSIRO case files
(``scripts/fetch_csiro_nem_data.py``); nothing from it is vendored.

The pinned commit and the sha256 of every file the lab relies on are recorded
in ``expected_sources.json``; a mismatch fails rather than silently running a
different benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

LAB: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = LAB.parents[1]
UPSTREAM: str = "https://github.com/Power-Agent/PowerAgentBench"
CHECKOUT: Path = REPO_ROOT / ".artifacts" / "restorebench-source"
EXPECTED: Path = LAB / "expected_sources.json"
LEVEL_3: str = "benchmarks/steady/level_3"
# The files whose content this lab depends on: the lockfile its environment is
# built from, the licences it relies on, and each corpus's manifests.
PINNED_FILES: tuple[str, ...] = (
    f"{LEVEL_3}/LICENSE",
    f"{LEVEL_3}/NOTICE",
    f"{LEVEL_3}/dataset/LICENSE",
    f"{LEVEL_3}/pyproject.toml",
    f"{LEVEL_3}/uv.lock",
    f"{LEVEL_3}/dataset/ieee118/manifest.json",
    f"{LEVEL_3}/dataset/ieee118/evaluation_manifest.json",
    f"{LEVEL_3}/dataset/ieee118/private/witnesses.json",
    f"{LEVEL_3}/dataset/pegase89/manifest.json",
    f"{LEVEL_3}/dataset/pegase89/evaluation_manifest.json",
    f"{LEVEL_3}/dataset/pegase89/private/witnesses.json",
)


def git(*args: str, cwd: Path = CHECKOUT) -> str:
    """Run git and return stdout; a failure raises with git's own message."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def sha256(path: Path) -> str:
    """Hash one checked-out file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(commit: str) -> None:
    """Shallow-fetch exactly ``commit`` into CHECKOUT (idempotent once present)."""
    if not (CHECKOUT / ".git").is_dir():
        CHECKOUT.mkdir(parents=True)
        git("init", "--quiet")
        git("remote", "add", "origin", UPSTREAM)
    if not _has_head() or git("rev-parse", "HEAD") != commit:
        git("fetch", "--quiet", "--depth", "1", "origin", commit)
        git("checkout", "--quiet", "--detach", "FETCH_HEAD")


def _has_head() -> bool:
    """True once the checkout has any commit (a fresh ``git init`` has none)."""
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=CHECKOUT,
                          capture_output=True).returncode == 0


def verify(expected: dict) -> list[str]:
    """Return every mismatch between the checkout and the recorded commit and hashes."""
    problems = []
    head = git("rev-parse", "HEAD")
    if head != expected["commit"]:
        problems.append(f"HEAD is {head}, expected {expected['commit']}")
    for relative, digest in expected["sha256"].items():
        path = CHECKOUT / relative
        if not path.is_file():
            problems.append(f"missing {relative}")
        elif sha256(path) != digest:
            problems.append(f"changed {relative}")
    return problems


def main() -> int:
    """Fetch and verify; ``--record`` writes expected_sources.json from the checkout instead."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", metavar="COMMIT",
                        help="Pin this commit and record the current hashes (run once, then commit the JSON)")
    args = parser.parse_args()
    if args.record:
        fetch(args.record)
        recorded = {"upstream": UPSTREAM, "commit": git("rev-parse", "HEAD"),
                    "sha256": {relative: sha256(CHECKOUT / relative) for relative in PINNED_FILES}}
        EXPECTED.write_text(json.dumps(recorded, indent=2) + "\n")
        print(f"recorded {EXPECTED.relative_to(REPO_ROOT)} at {recorded['commit']}")
        return 0
    expected = json.loads(EXPECTED.read_text())
    fetch(expected["commit"])
    problems = verify(expected)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if not problems:
        print(f"PASS PowerAgentBench {expected['commit'][:12]}: {len(expected['sha256'])} pinned files match")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
