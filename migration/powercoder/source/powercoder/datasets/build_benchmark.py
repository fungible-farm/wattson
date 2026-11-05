"""Generate a frozen held-out domain set using the existing M04 real solver.

This extends the lab benchmark data pipeline. It covers the M04 power-flow
families only; Python, agent robustness, and critical negative suites remain
separate M09 requirements. Neither row count nor a new random seed alone is
accepted as proof of independence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from powercoder.datasets import common, generate
from powercoder.datasets.audit import audit_rows, load_rows, prompt_key, task_key

# Mission M09 minimum independent held-out domain tasks.
MIN_TASKS: int = 200
# Separate from the smoke/training factory's seed 42; recorded in the manifest.
BENCHMARK_SEED: int = 20261020


def main() -> int:
    """Generate with the solver, remove overlaps, audit, then freeze artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exclude", nargs="+", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=BENCHMARK_SEED)
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen benchmark directory")
    excluded = [row for path in args.exclude for row in load_rows(path)]
    seen_prompts = {prompt_key(row) for row in excluded}
    seen_tasks = {task_key(row) for row in excluded}
    elements_path = common.REPO_ROOT / "powercoder/datasets/element_lists.json"
    elements = json.loads(elements_path.read_text())
    candidates = generate.generate_dataset(args.base_seed, generate.DEFAULT_COUNTS, elements)
    retained = []
    for row in candidates:
        if row["lifecycle"]["status"] != "verified":
            continue
        prompt, task = prompt_key(row), task_key(row)
        if prompt in seen_prompts or task in seen_tasks:
            continue
        seen_prompts.add(prompt)
        seen_tasks.add(task)
        row["lifecycle"]["split"] = "benchmark"
        retained.append(row)
    audit = audit_rows(retained, excluded)
    if not audit["clean"] or len(retained) < MIN_TASKS:
        raise ValueError(f"Insufficient independent held-out tasks: {audit}")
    manifest = generate.build_manifest(retained, args.base_seed, generate.DEFAULT_COUNTS)
    manifest.update({
        "dataset": "nem-powercoder-held-out-domain",
        "scope": "M04 power-flow families; broader M09 suites still required",
        "candidate_count": len(candidates),
        "independence_audit": audit,
        "excluded_sources": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in args.exclude
        },
        "element_lists_sha256": hashlib.sha256(elements_path.read_bytes()).hexdigest(),
        "counts_by_subfamily": dict(Counter(row["subfamily"] for row in retained)),
    })
    common.write_jsonl(args.out_dir / "examples.jsonl", retained)
    manifest["examples_sha256"] = hashlib.sha256(
        (args.out_dir / "examples.jsonl").read_bytes()
    ).hexdigest()
    common.write_json(args.out_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
