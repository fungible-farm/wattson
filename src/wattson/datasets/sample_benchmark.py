"""Draw a stratified, interleaved evaluation sample from a frozen benchmark.

The frozen held-out set (``build_benchmark.py``) is stored in subfamily order:
26 reports, then 120 load-scale fits, 120 line-rating fits and 40 dispatch fits.
A run stopped part-way therefore saw only the first subfamilies, and the 306-task
run cost about a day per model on GB10 (r4/r5). This samples the same number of
tasks from every subfamily with a recorded seed, then interleaves them so any
prefix of the run covers every subfamily about equally.

Equal allocation is deliberate: proportional allocation of 100 would give
reports only 8 tasks. A pass rate over this sample is therefore the
equal-weighted mean of the subfamily rates, not an estimate of the 306-task
pass rate, and the manifest says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from wattson.datasets import common
from wattson.datasets.audit import audit_rows, load_rows, prompt_key, task_key

# 4 subfamilies x 25 = 100 tasks; pf-report has 26 in the frozen set, so 25 is the
# largest equal allocation.
PER_SUBFAMILY: int = 25
# Separate from the training seed (42) and the benchmark seed (20261020).
SAMPLE_SEED: int = 20261022


def independent(rows: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """Drop rows that share a prompt or task with ``excluded``, then keep one row per task.

    The smoke dataset's validation split repeats report tasks (one base case per
    network, N-1 lines drawn twice) and shares 6 of them with the training split,
    so a smoke gate drawn from it unfiltered would partly test memorised tasks.

    Returns:
        The kept rows (lowest ``example_id`` of each task), the number dropped as
        overlapping ``excluded``, and the number dropped as duplicates.
    """
    excluded_prompts = {prompt_key(row) for row in excluded}
    excluded_tasks = {task_key(row) for row in excluded}
    kept_prompts: set[str] = set()
    kept_tasks: set[str] = set()
    kept, overlapping, duplicates = [], 0, 0
    for row in sorted(rows, key=lambda row: row["example_id"]):
        prompt, task = prompt_key(row), task_key(row)
        if prompt in excluded_prompts or task in excluded_tasks:
            overlapping += 1
        elif prompt in kept_prompts or task in kept_tasks:
            duplicates += 1
        else:
            kept.append(row)
            kept_prompts.add(prompt)
            kept_tasks.add(task)
    return kept, overlapping, duplicates


def equal_allocation(available: dict[str, int], total: int) -> dict[str, int]:
    """Split ``total`` as evenly as availability allows (water-filling).

    A subfamily with fewer rows than its share contributes all of them, and the
    shortfall goes to the others; remaining single tasks go to subfamilies in
    sorted-name order. The validation split's 3 dispatch fits make this matter
    for the 20-task smoke set.

    Raises:
        ValueError: if fewer than ``total`` rows exist in all.
    """
    if sum(available.values()) < total:
        raise ValueError(f"Only {sum(available.values())} rows for a {total}-task sample")
    allocation = {name: 0 for name in available}
    remaining = total
    while remaining:
        open_names = sorted(name for name in available if allocation[name] < available[name])
        share = max(remaining // len(open_names), 1)
        for name in open_names:
            take = min(share, available[name] - allocation[name], remaining)
            allocation[name] += take
            remaining -= take
    return allocation


def sample(rows: list[dict[str, Any]], allocation: dict[str, int], seed: int) -> list[dict[str, Any]]:
    """Return seeded draws per subfamily, interleaved round-robin.

    Args:
        rows: Frozen rows, each with ``subfamily`` and ``example_id``.
        allocation: Tasks to draw from each named subfamily; others are ignored.
        seed: Seed for the draws and the within-subfamily order.

    Returns:
        The sampled rows: draw i of every subfamily (in sorted-name order) before
        draw i + 1 of any; a subfamily that runs out simply drops out of the cycle.

    Raises:
        ValueError: if a subfamily has fewer rows than its allocation.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["subfamily"]].append(row)
    rng = random.Random(seed)
    drawn: dict[str, list[dict[str, Any]]] = {}
    for subfamily in sorted(allocation):
        members = sorted(groups[subfamily], key=lambda row: row["example_id"])
        if len(members) < allocation[subfamily]:
            raise ValueError(f"{subfamily} has {len(members)} tasks, fewer than {allocation[subfamily]}")
        drawn[subfamily] = rng.sample(members, allocation[subfamily])
    longest = max(allocation.values())
    return [drawn[name][i] for i in range(longest) for name in sorted(drawn) if i < len(drawn[name])]


def main() -> int:
    """Write the sample and a manifest tying it to the frozen source by hash."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Frozen examples.jsonl")
    parser.add_argument("--out-dir", type=Path, required=True)
    sizes = parser.add_mutually_exclusive_group()
    sizes.add_argument("--per-subfamily", type=int, default=PER_SUBFAMILY)
    sizes.add_argument("--total", type=int, help="Equal allocation of this many tasks, capped by availability")
    parser.add_argument("--skip-subfamily", nargs="*", default=[], help="Subfamilies the benchmark does not contain")
    parser.add_argument("--exclude", type=Path, help="Rows sharing a prompt or task with this file are dropped")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen sample directory")
    rows = [row for row in load_rows(args.source) if row["subfamily"] not in args.skip_subfamily]
    excluded = load_rows(args.exclude) if args.exclude else []
    rows, overlapping, duplicates = independent(rows, excluded)
    available = {name: sum(row["subfamily"] == name for row in rows) for name in {row["subfamily"] for row in rows}}
    allocation = (equal_allocation(available, args.total) if args.total
                  else {name: args.per_subfamily for name in available})
    selected = sample(rows, allocation, args.seed)
    examples = common.write_jsonl(args.out_dir / "examples.jsonl", selected)
    manifest = {
        "dataset": "nem-powercoder-stratified-sample",
        "source": str(args.source),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "seed": args.seed,
        "allocation_by_subfamily": allocation,
        "skipped_subfamilies": sorted(args.skip_subfamily),
        "excluded_source": str(args.exclude) if args.exclude else None,
        "excluded_sha256": hashlib.sha256(args.exclude.read_bytes()).hexdigest() if args.exclude else None,
        "dropped_overlapping": overlapping, "dropped_duplicates": duplicates,
        "n_tasks": len(selected),
        "allocation": "equal per subfamily where availability allows; pass rates weight subfamilies "
                      "by this allocation, not by the source's composition",
        "order": "seeded draw per subfamily, interleaved round-robin in sorted subfamily order",
        "example_ids": [row["example_id"] for row in selected],
        "examples_sha256": hashlib.sha256(examples.read_bytes()).hexdigest(),
    }
    audit = audit_rows(selected, excluded)
    if not audit["clean"]:
        raise ValueError(f"Sample is not independent of the excluded rows: {audit}")
    manifest["independence_audit"] = audit
    common.write_json(args.out_dir / "manifest.json", manifest)
    print(json.dumps({key: value for key, value in manifest.items() if key != "example_ids"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
