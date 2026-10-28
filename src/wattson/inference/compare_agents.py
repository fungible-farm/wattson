"""Compare complete, identically budgeted domain runs with paired uncertainty."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from wattson.datasets.audit import load_rows
from wattson.inference.evaluate_agent import frozen_task_count
from wattson.training.unsloth.evaluate import DOMAIN_IMPROVEMENT_THRESHOLD, uncertainty


def verified_run(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Require all benchmark IDs in order, with unchanged raw evidence journals."""
    summary = json.loads((directory / "summary.json").read_text())
    records = load_rows(directory / "results.jsonl")
    if any(row["kind"] != "result" for row in records):
        raise ValueError("Unexpected score journal event")
    results = [row["payload"] for row in records]
    task_path = Path(next(iter(summary["inputs"])))
    expected_ids = [row["example_id"] for row in load_rows(task_path)]
    count = frozen_task_count(task_path)
    if (not summary["complete"] or not summary["independence_audit"]["clean"]
        or summary["n_tasks"] != count or len(results) != count
        or [row["example_id"] for row in results] != expected_ids):
        raise ValueError("Incomplete or mismatched domain run")
    for path, expected_hash in summary["inputs"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Benchmark/training source changed since the run")
    for row in results:
        journal = directory / f"{row['example_id']}.jsonl"
        if hashlib.sha256(journal.read_bytes()).hexdigest() != row["journal_sha256"]:
            raise ValueError("Raw evidence changed since scoring")
    if (summary["n_pass"] != sum(row["passed"] for row in results)
        or summary["pass_at_1"] != sum(row["passed"] for row in results) / len(results)):
        raise ValueError("Summary disagrees with per-task results")
    return summary, results


# Evaluation conditions a run records beside its budgets; runs from before a field
# existed used its default. A comparison spanning a difference must name it.
CONDITIONS: dict[str, str] = {"first_turn_tool_choice": "auto", "server_tool_strict_level": "auto",
                              # evaluate_prompted.py's procedure-in-the-prompt baseline (G0).
                              "system_prompt_variant": "evaluation",
                              # evaluate_agent --namespace-tools adds ls/get (backlog 0026).
                              "tool_set": "study"}


def compare(base_dir: Path, student_dir: Path, allowed_differences: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Measure the actual delta, refusing incomplete or unequal conditions.

    ``allowed_differences`` names the CONDITIONS this comparison deliberately
    spans (the strict-tool-calling arm's own comparisons); any other difference,
    and any difference in budgets or inputs, is refused.
    """
    base, first = verified_run(base_dir)
    student, second = verified_run(student_dir)
    for field in ("inputs", "max_turns", "max_tool_calls", "max_tokens", "temperature", "attempts_per_task", "inference_timeout_s"):
        if base[field] != student[field]:
            raise ValueError(f"Unequal evaluation conditions: {field}")
    conditions = {field: {"base": base.get(field, default), "student": student.get(field, default)}
                  for field, default in CONDITIONS.items()}
    for field, values in conditions.items():
        if values["base"] != values["student"] and field not in allowed_differences:
            raise ValueError(f"Unequal evaluation conditions: {field}")
    first_paired = [{"example_id": r["example_id"], "pass": r["passed"]} for r in first]
    second_paired = [{"example_id": r["example_id"], "pass": r["passed"]} for r in second]
    base_rate = sum(r["passed"] for r in first) / len(first)
    student_rate = sum(r["passed"] for r in second) / len(second)
    delta = student_rate - base_rate
    interval = uncertainty(first_paired, second_paired)
    return {"n_tasks": len(first), "base_model": base["model"], "student_model": student["model"],
            "conditions": conditions,
            "first_turn_tool_choice": conditions["first_turn_tool_choice"],
            "base_pass_at_1": base_rate, "student_pass_at_1": student_rate,
            "delta_pass_at_1": delta, "delta_percentage_points": delta * 100,
            "domain_threshold_met": delta >= DOMAIN_IMPROVEMENT_THRESHOLD,
            # The point estimate alone can clear 10 pp by noise on 100 tasks; this
            # requires the paired 95% interval's lower bound to clear it.
            "domain_threshold_met_ci_lower": interval["delta"][0] >= DOMAIN_IMPROVEMENT_THRESHOLD,
            "uncertainty": interval,
            "by_subfamily": {family: {
                "n_tasks": sum(r["subfamily"] == family for r in first),
                "base_pass": sum(r["passed"] for r in first if r["subfamily"] == family),
                "student_pass": sum(r["passed"] for r in second if r["subfamily"] == family),
            } for family in sorted({r["subfamily"] for r in first})},
            "g5_met": False, "g5_pending": "General Python and critical fabrication suites remain separate gates"}


def main() -> None:
    """Write a new comparison without overwriting previous measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--student-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.base_dir, args.student_dir)
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
