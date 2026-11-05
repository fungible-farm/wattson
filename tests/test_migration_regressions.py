"""Counterexamples found by independent review, kept separate from happy paths."""
from __future__ import annotations

import re
import os
import shlex
from pathlib import Path
import subprocess

import pytest

from wattson.datasets.audit import task_key
from wattson.datasets.split import assign_splits

ROOT: Path = Path(__file__).resolve().parents[1]
# The reviewer used ten seeded IDs for one task; seeds must not create held-out tasks.
DUPLICATE_ROWS: int = 10


def test_duplicate_task_inputs_never_cross_split_boundaries() -> None:
    rows = [{"example_id": f"seed-{index}", "family": "powerflow", "subfamily": "pf-report",
             "topology": "snem-sa-2000", "task": {"prompt": "same report", "inputs": {"network_id": "snem-sa-2000"}},
             "lifecycle": {}} for index in range(DUPLICATE_ROWS)]
    buckets = assign_splits(rows)
    memberships: dict[str, set[str]] = {}
    for split, examples in buckets.items():
        for example in examples:
            memberships.setdefault(task_key(example), set()).add(split)
    assert sum(map(len, buckets.values())) == DUPLICATE_ROWS
    assert all(len(splits) == 1 for splits in memberships.values())


def test_open_snapshot_accepts_equivalent_offset_instants(tmp_path: Path) -> None:
    pytest.importorskip("rdflib")
    from rdflib import Graph
    from wattson.semantic.snapshots import SnapshotStore, capture

    snapshot = capture(Graph(), "2026-11-05T00:00:00+00:00", "review fixture", "study")
    store = SnapshotStore(tmp_path / "snapshots.jsonl")
    store.append(snapshot)
    assert store.at("2026-11-05T11:30:00+11:00") == snapshot
    assert store.at("2026-11-05T00:30:00") == snapshot  # documented legacy UTC convention


@pytest.mark.parametrize("launcher", ["launch_agent_study.sh", "launch_powercoder_comparison.sh"])
def test_dotted_run_stop_pattern_cannot_match_another_run(launcher: str, tmp_path: Path) -> None:
    source = (ROOT / "scripts" / launcher).read_text()
    assignment = next(line for line in source.splitlines() if line.startswith("ESCAPED_RUN="))
    stop = next(line for line in source.splitlines() if line.startswith("STOP_CONTAINERS="))
    result = subprocess.run(["bash", "-c", 'RUN=review.run1\n' + assignment + '\n' + stop + '\nprintf "%s" "$STOP_CONTAINERS"'],
                            check=True, text=True, capture_output=True)
    # Execute the shell boundary too: an unquoted backslash is stripped by sh.
    # The fake engine records final argv and returns no IDs, so no real stop runs.
    engine = tmp_path / "podman"
    engine.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$WATTSON_REVIEW_ARGV"\n')
    engine.chmod(0o755)
    captured = tmp_path / "argv.txt"
    environment = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                   "WATTSON_REVIEW_ARGV": str(captured)}
    subprocess.run(shlex.split(result.stdout), env=environment, check=True, timeout=30)
    arguments = captured.read_text().splitlines()
    pattern = arguments[arguments.index("--filter") + 1].removeprefix("name=")
    assert re.match(pattern, "wattson-review.run1-own")
    assert not re.match(pattern, "wattson-reviewXrun1-unrelated")
