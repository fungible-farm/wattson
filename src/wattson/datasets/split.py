"""M04 — dataset splitting: partition by topology+family, reserve benchmark
cases, and produce a deterministic train/validation/test split.

Usage:
    uv run python powercoder/datasets/split.py --examples <in.jsonl> --out-dir <dir>

Design (the "benchmark cases reserved and uncontaminated" acceptance criterion):
  * Benchmark reservation is by TOPOLOGY (network case). Every example whose
    topology is in BENCHMARK_TOPOLOGIES is held out as benchmark. Because the
    reservation is by case, a benchmark topology can never leak into
    train/validation/test — there is no contamination by construction.
  * The remaining (non-benchmark) examples are partitioned by (topology,
    family); within each partition the examples are deterministically shuffled
    (seeded by SPLIT_SEED + topology + family) and assigned to
    train / validation / test by TRAIN_VAL_TEST_FRACTIONS.
  * Each example's lifecycle.split is set to its assigned split; the split
    files and a split manifest are written to --out-dir.

Determinism: identical input (same example_ids) reproduces the identical
split, because the shuffle seed and the ordering are fully determined by the
data, not by wall-clock time or process state.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from wattson.datasets import common  # noqa: E402
from wattson.datasets.audit import task_key  # noqa: E402

# Network cases (topologies) reserved as the held-out benchmark set. A
# benchmark topology never appears in train/validation/test, so there is no
# contamination by construction. fixture-2bus-ok is the small, fully
# characterised 2-bus case — an appropriate held-out reference.
BENCHMARK_TOPOLOGIES: frozenset[str] = frozenset({"fixture-2bus-ok"})

# Train / validation / test fractions for the non-benchmark examples. Must sum
# to 1.0; test is the remainder (1.0 - train - validation).
TRAIN_FRACTION: float = 0.70
VAL_FRACTION: float = 0.15
TEST_FRACTION: float = 1.0 - TRAIN_FRACTION - VAL_FRACTION

# Fixed seed for the deterministic within-partition shuffle.
SPLIT_SEED: int = 1234


def assign_splits(examples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Assign a lifecycle.split to every example and bucket them.

    Args:
        examples: Parsed training-example records (mutated in place: each
            record's lifecycle.split is set).

    Returns:
        A dict {split_name: [examples]} for benchmark/train/validation/test.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        "benchmark": [],
        "train": [],
        "validation": [],
        "test": [],
    }

    # 1. Benchmark reservation by topology.
    non_benchmark: list[dict[str, Any]] = []
    for example in examples:
        topology = example.get("topology")
        if topology in BENCHMARK_TOPOLOGIES:
            example.setdefault("lifecycle", {})["split"] = "benchmark"
            buckets["benchmark"].append(example)
        else:
            non_benchmark.append(example)

    # 2. Partition the rest by (topology, family); deterministic shuffle;
    #    assign by fraction.
    partitions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for example in non_benchmark:
        key = (example.get("topology", ""), example.get("family", ""))
        partitions.setdefault(key, []).append(example)

    for (topology, family), group in partitions.items():
        # Seeded row IDs do not prove task independence (FM-21). Shuffle whole
        # groups of identical task inputs; no group may cross a split boundary.
        task_groups: dict[str, list[dict[str, Any]]] = {}
        for example in sorted(group, key=lambda e: e.get("example_id", "")):
            task_groups.setdefault(task_key(example), []).append(example)
        grouped = [task_groups[key] for key in sorted(task_groups)]
        rng = random.Random(f"{SPLIT_SEED}|{topology}|{family}")
        rng.shuffle(grouped)

        n = len(grouped)
        n_train = int(round(n * TRAIN_FRACTION))
        n_val = int(round(n * VAL_FRACTION))
        # test is the remainder so the fractions always cover every example.
        for i, task_group in enumerate(grouped):
            if i < n_train:
                split = "train"
            elif i < n_train + n_val:
                split = "validation"
            else:
                split = "test"
            for example in task_group:
                example.setdefault("lifecycle", {})["split"] = split
                buckets[split].append(example)

    return buckets


def split_dataset(examples_path: Path, out_dir: Path) -> dict[str, Any]:
    """Split an examples.jsonl file and write the split files + manifest.

    Args:
        examples_path: Input examples.jsonl.
        out_dir: Directory to write train/validation/test/benchmark .jsonl and
            split_manifest.json.

    Returns:
        A report dict: {total, per_split counts, out_dir}.
    """
    examples = common.load_jsonl(examples_path)
    buckets = assign_splits(examples)

    out_dir.mkdir(parents=True, exist_ok=True)
    for split_name, rows in buckets.items():
        common.write_jsonl(out_dir / f"{split_name}.jsonl", rows)

    manifest = {
        "total_examples": len(examples),
        "benchmark_topologies": sorted(BENCHMARK_TOPOLOGIES),
        "train_val_test_fractions": {
            "train": TRAIN_FRACTION,
            "validation": VAL_FRACTION,
            "test": TEST_FRACTION,
        },
        "split_seed": SPLIT_SEED,
        "counts": {name: len(rows) for name, rows in buckets.items()},
        "git_commit": common.git_commit(),
        "created_utc": common.utc_now_iso(),
    }
    common.write_json(out_dir / "split_manifest.json", manifest)

    return {
        "total": len(examples),
        "counts": manifest["counts"],
        "out_dir": str(out_dir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", required=True, type=Path, help="Input examples.jsonl")
    parser.add_argument("--out-dir", required=True, type=Path, help="Output directory for split files")
    args = parser.parse_args(argv)

    report = split_dataset(args.examples, args.out_dir)

    print(f"[split] {args.examples} -> {args.out_dir}")
    print(f"[split] total={report['total']} counts={report['counts']}")
    print(f"[split] benchmark topologies reserved: {sorted(BENCHMARK_TOPOLOGIES)}")

    # Contamination check: no benchmark topology may appear in train/val/test.
    for split_name in ("train", "validation", "test"):
        path = args.out_dir / f"{split_name}.jsonl"
        if path.exists():
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                example = json.loads(line)
                if example.get("topology") in BENCHMARK_TOPOLOGIES:
                    print(f"[split] CONTAMINATION: {example.get('example_id')} "
                          f"(topology {example.get('topology')}) in {split_name}")
                    return 1

    print("[split] PASS: no benchmark contamination")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
