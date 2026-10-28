"""The v0.1.0 smoke check, stages 4 and 5 (README.md): no model, no GPU.

``oracle``  runs the oracle (the scripted procedure that writes the training programs) through the real tool server, the
            real Python shell and the real grader for the shipped smoke tasks plus a planning question and a refusal. Every
            one must be kept; a drop means the solver, the shell and the grader disagree on this machine.
``rescore`` re-scores the 20 recorded model attempts in ``fixtures/smoke/run`` from scratch (re-solving the networks) and
            requires each task's result under both scorers to equal ``fixtures/expected.json``. This is the check that
            floating-point, library and interpreter differences do not change a score.
``rescore --write-expected`` writes ``fixtures/expected.json`` from this machine (maintainers only).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any

from wattson.datasets import planning, trajectories
from wattson.datasets.audit import load_rows
from wattson.datasets.unsolvable import make_unsolvable
from wattson.inference.domain_scorecard import SCORER_VERSION, SCORER_VERSION_NORMALISED, rescore_run

ROOT: Path = Path(__file__).resolve().parents[2]
TASKS: Path = ROOT / "fixtures/smoke/tasks/examples.jsonl"
RUN: Path = ROOT / "fixtures/smoke/run"
EXPECTED: Path = ROOT / "fixtures/expected.json"
# The network the planning question is drawn from: it has distinct outage loadings (backlog 0041), unlike ieee118.
PLANNING_CASE: str = "pegase89"


def oracle() -> int:
    """Build and grade the oracle trajectory of every smoke task, a planning question and a refusal."""
    rows = load_rows(TASKS)
    fits = [r for r in rows if r["subfamily"].endswith("-fit")]
    jobs: list[tuple[dict[str, Any], str | None]] = [(r, None) for r in rows]
    jobs.append((make_unsolvable(fits[0]), None))
    plan = planning.impactful_task(PLANNING_CASE, 0, 0, "test", set())
    if plan is None:
        raise SystemExit("could not draw a planning question")
    jobs.append((plan, None))
    with tempfile.TemporaryDirectory() as work:
        outcomes = asyncio.run(trajectories.build_all(jobs, Path(work), False, True, True, True))
    dropped = [(row["subfamily"], reason) for (row, _), (kept, reason) in zip(jobs, outcomes) if kept is None]
    print(f"oracle trajectories: {len(jobs) - len(dropped)} of {len(jobs)} built and graded")
    for subfamily, reason in dropped:
        print(f"  DROPPED {subfamily}: {reason}", file=sys.stderr)
    return 1 if dropped else 0


def rescore(write: bool) -> int:
    """Re-score the shipped run; compare each task to the expected result."""
    _summary, rows = rescore_run(RUN)
    got = {r["example_id"]: {"pass": bool(r["pass"]), "pass_norm": bool(r["pass_norm"])} for r in rows}
    meta = {"scorers": {"pass": SCORER_VERSION, "pass_norm": SCORER_VERSION_NORMALISED}, "n_tasks": len(got),
            "n_pass": sum(v["pass"] for v in got.values()), "n_pass_norm": sum(v["pass_norm"] for v in got.values())}
    print(f"python {sys.version.split()[0]} on {platform.machine()}: {meta['n_pass']} of {len(got)} pass, "
          f"{meta['n_pass_norm']} of {len(got)} pass_norm")
    if write:
        EXPECTED.write_text(json.dumps({**meta, "tasks": got}, indent=2, sort_keys=True) + "\n")
        print("wrote", EXPECTED)
        return 0
    expected = json.loads(EXPECTED.read_text())
    wrong = {k: (expected["tasks"].get(k), v) for k, v in got.items() if expected["tasks"].get(k) != v}
    missing = sorted(set(expected["tasks"]) - set(got))
    for key, (want, have) in wrong.items():
        print(f"  MISMATCH {key}: expected {want}, got {have}", file=sys.stderr)
    for key in missing:
        print(f"  MISSING {key}", file=sys.stderr)
    return 1 if wrong or missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["oracle", "rescore"])
    parser.add_argument("--write-expected", action="store_true")
    arguments = parser.parse_args()
    return oracle() if arguments.stage == "oracle" else rescore(arguments.write_expected)


if __name__ == "__main__":
    raise SystemExit(main())
