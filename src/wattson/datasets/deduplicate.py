"""M04 — dataset deduplication.

Usage:
    uv run python powercoder/datasets/deduplicate.py --examples <in.jsonl> --out <out.jsonl>

Two notions of duplicate, both reported:
  * exact:   the same example_id appears more than once (the generator seeds
             example_id from the task definition, so this should not happen).
  * task:    the same canonical task definition (family, subfamily, topology,
             seed, task.inputs) appears more than once. If the colliding
             records also disagree on their expected results, that is a
             NON-DETERMINISM defect (same inputs, different outputs) and is
             flagged distinctly from a benign exact duplicate.

First occurrence wins; the deduplicated file is written to --out. Exit code 0
iff no task-definition collisions with differing expected results (a hard
defect); exact duplicates are tolerated (and removed) but reported.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from wattson.datasets import common  # noqa: E402


def _task_key(example: dict[str, Any]) -> str:
    """Canonical key for the task definition (what example_id hashes)."""
    return common.canonical_json({
        "family": example.get("family"),
        "subfamily": example.get("subfamily"),
        "topology": example.get("topology"),
        "seed": example.get("seed"),
        "inputs": (example.get("task") or {}).get("inputs"),
    })


def _expected_key(example: dict[str, Any]) -> str:
    """Canonical key for the expected result (to detect non-determinism)."""
    return common.canonical_json(example.get("expected"))


def deduplicate(examples_path: Path, out_path: Path) -> dict[str, Any]:
    """Deduplicate an examples.jsonl file.

    Args:
        examples_path: Input examples.jsonl.
        out_path: Output path for the deduplicated file.

    Returns:
        A report dict: {total, unique, exact_dupes, task_collisions,
        nondeterministic, failures: [...]}.
    """
    by_id: dict[str, int] = defaultdict(int)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    examples: list[dict[str, Any]] = []

    with open(examples_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            example = json.loads(line)
            examples.append(example)
            by_id[example.get("example_id", "<missing>")] += 1
            by_task[_task_key(example)].append(example)

    exact_dupes = {eid: n for eid, n in by_id.items() if n > 1}

    # Task-definition collisions: same task, more than one record.
    task_collisions: dict[str, int] = {}
    nondeterministic: list[dict[str, Any]] = []
    for task_key, group in by_task.items():
        if len(group) > 1:
            task_collisions[task_key] = len(group)
            expected_keys = {_expected_key(e) for e in group}
            if len(expected_keys) > 1:
                nondeterministic.append({
                    "example_id": group[0].get("example_id"),
                    "family": group[0].get("family"),
                    "subfamily": group[0].get("subfamily"),
                    "topology": group[0].get("topology"),
                    "distinct_expected": len(expected_keys),
                })

    # First occurrence wins.
    seen_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for example in examples:
        eid = example.get("example_id", "<missing>")
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        deduped.append(example)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    common.write_jsonl(out_path, deduped)

    return {
        "total": len(examples),
        "unique": len(deduped),
        "exact_dupes": len(exact_dupes),
        "task_collisions": len(task_collisions),
        "nondeterministic": len(nondeterministic),
        "nondeterministic_detail": nondeterministic,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", required=True, type=Path, help="Input examples.jsonl")
    parser.add_argument("--out", required=True, type=Path, help="Output deduplicated examples.jsonl")
    args = parser.parse_args(argv)

    report = deduplicate(args.examples, args.out)

    print(f"[dedup] {args.examples} -> {args.out}")
    print(f"[dedup] total={report['total']} unique={report['unique']} "
          f"exact_dupes={report['exact_dupes']} task_collisions={report['task_collisions']} "
          f"nondeterministic={report['nondeterministic']}")

    if report["nondeterministic"]:
        print("[dedup] NON-DETERMINISM DEFECT (same task, differing expected results):")
        for item in report["nondeterministic"]:
            print(f"  {item['example_id']} [{item['family']}/{item['subfamily']}/{item['topology']}]: "
                  f"{item['distinct_expected']} distinct expected results")
        return 1

    if report["exact_dupes"]:
        print(f"[dedup] removed {report['exact_dupes']} exact duplicate example_id(s) (benign)")

    print("[dedup] PASS: no task-definition collisions with differing expected results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
