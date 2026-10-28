"""Audit held-out tasks by actual inputs and prompts, rather than seeded IDs.

An example ID includes its random seed, so different IDs alone do not prove
task independence. This audit rejects repeated prompts, repeated task inputs,
and overlap with any training or teacher-correction source supplied to it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Read nonempty JSONL records, preserving the complete original rows."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def prompt_key(row: dict[str, Any]) -> str:
    """Normalize whitespace so formatting differences cannot hide leakage."""
    return " ".join(row["task"]["prompt"].split())


def task_key(row: dict[str, Any]) -> str:
    """Identify the solver inputs independently of random seeds and answers."""
    payload = {
        "subfamily": row["subfamily"],
        "topology": row["topology"],
        "inputs": row["task"]["inputs"],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def audit_rows(
    candidates: list[dict[str, Any]], excluded: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return measured uniqueness and contamination counts; fail closed."""
    ids = Counter(row["example_id"] for row in candidates)
    prompts = Counter(prompt_key(row) for row in candidates)
    tasks = Counter(task_key(row) for row in candidates)
    excluded_ids = {row["example_id"] for row in excluded}
    excluded_prompts = {prompt_key(row) for row in excluded}
    excluded_tasks = {task_key(row) for row in excluded}
    overlaps = [
        row["example_id"] for row in candidates
        if row["example_id"] in excluded_ids
        or prompt_key(row) in excluded_prompts
        or task_key(row) in excluded_tasks
    ]
    duplicate_ids = sum(count - 1 for count in ids.values())
    duplicate_prompts = sum(count - 1 for count in prompts.values())
    duplicate_tasks = sum(count - 1 for count in tasks.values())
    return {
        "rows": len(candidates),
        "unique_prompts": len(prompts),
        "unique_tasks": len(tasks),
        "duplicate_ids": duplicate_ids,
        "duplicate_prompts": duplicate_prompts,
        "duplicate_tasks": duplicate_tasks,
        "overlap_ids": overlaps,
        "clean": bool(candidates) and not (
            duplicate_ids or duplicate_prompts or duplicate_tasks or overlaps
        ),
    }


def main() -> int:
    """Audit split files before loading a model or reporting model accuracy."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, nargs="+", required=True)
    parser.add_argument("--exclude", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    candidates = [row for path in args.candidates for row in load_rows(path)]
    excluded = [row for path in args.exclude for row in load_rows(path)]
    report = audit_rows(candidates, excluded)
    report["source_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in args.candidates + args.exclude
    }
    payload = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
    print(payload, end="")
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
