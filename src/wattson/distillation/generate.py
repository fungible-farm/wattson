"""Batch driver for the M05 Qwen3.8 distillation work package.

Iterates the M04 dataset, sends each structured task to the teacher, executes
the candidate in isolation, independently verifies it against the solver, and
runs the targeted-repair loop on any failure. Every processed example is
written (flushed) as one JSON line to results.jsonl as soon as it reaches a
terminal status, so an interrupted batch resumes without re-doing accepted
work: on restart the driver reads results.jsonl, builds the set of completed
example_ids, and skips them.

Usage:
    uv run python powercoder/distillation/generate.py \
        --examples powercoder/datasets/smoke/examples.jsonl \
        --model qwen3.8-27b-uncensored \
        --out-dir powercoder/distillation/out/qwen3.8-27b-uncensored \
        [--limit N] [--max-repair 2] [--base-url http://127.0.0.1:8000]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Bootstrap: this file is run as a script (uv run python powercoder/distillation/
# generate.py), so the repo root is NOT on sys.path. Add it before importing the
# powercoder package (namespace package, no __init__.py).
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wattson.distillation import filter as flt  # noqa: E402
from wattson.distillation import repair as rep  # noqa: E402
from wattson.distillation.teacher import TeacherClient  # noqa: E402

RESULTS_FILENAME: str = "results.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_examples(path: Path) -> list[dict[str, Any]]:
    """Load the M04 dataset (one JSON object per line)."""
    examples: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def load_completed(results_path: Path) -> dict[str, dict[str, Any]]:
    """Read prior results for resume. Returns {example_id: record}.

    Only records with a terminal status (accepted/rejected/quarantined) are
    considered completed; anything else is re-processed.
    """
    completed: dict[str, dict[str, Any]] = {}
    if not results_path.exists():
        return completed
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") in ("accepted", "rejected", "quarantined"):
                completed[record["example_id"]] = record
    return completed


def process_example(
    teacher: TeacherClient,
    example: dict[str, Any],
    max_repair: int,
) -> dict[str, Any]:
    """Generate, filter, and (if needed) repair one example.

    Returns a terminal record (status in {accepted, rejected, quarantined})
    with full provenance, the final code, the independent-check evidence, and
    the complete repair trajectory.
    """
    started = time.monotonic()
    initial_teacher = teacher.generate(example)
    initial_result = flt.filter_example(example, initial_teacher.code)

    outcome = rep.repair_example(
        teacher,
        example,
        initial_teacher.code,
        initial_result,
        initial_teacher,
        max_attempts=max_repair,
    )

    repair_trajectory = [
        {
            "attempt": step.attempt,
            "status": step.status,
            "prompt_tokens": step.prompt_tokens,
            "completion_tokens": step.completion_tokens,
            "elapsed_s": step.elapsed_s,
            "exec_wall_s": step.exec_wall_s,
            "exec_returncode": step.exec_returncode,
            "diagnostics": step.diagnostics,
            "evidence": step.evidence,
        }
        for step in outcome.steps
    ]

    return {
        "example_id": example["example_id"],
        "subfamily": example["subfamily"],
        "topology": example["topology"],
        "model": teacher.model,
        "status": outcome.final_status,
        "initial_code": initial_teacher.code,
        "final_code": outcome.final_code,
        "answer": outcome.final_answer,
        "evidence": outcome.final_evidence,
        "diagnostics": initial_result.diagnostics
        if not outcome.steps
        else outcome.steps[-1].diagnostics,
        "teacher_calls": outcome.total_teacher_calls,
        "prompt_tokens": outcome.total_prompt_tokens,
        "completion_tokens": outcome.total_completion_tokens,
        "repair_attempts": len(outcome.steps),
        "repair_trajectory": repair_trajectory,
        "exec_wall_s": outcome.total_exec_wall_s,
        "total_wall_s": time.monotonic() - started,
        "created_utc": _utc_now(),
    }


def run_batch(
    examples: list[dict[str, Any]],
    model: str,
    out_dir: Path,
    base_url: str,
    max_repair: int,
    limit: Optional[int],
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the distillation batch over `examples`, resuming from out_dir.

    Returns a summary dict (counts by status, totals) for the manifest.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / RESULTS_FILENAME
    completed = load_completed(results_path)
    if verbose:
        print(f"[generate] {len(examples)} examples, {len(completed)} already done")

    pending = [ex for ex in examples if ex["example_id"] not in completed]
    if limit is not None:
        pending = pending[:limit]
    if verbose:
        print(f"[generate] {len(pending)} pending for model={model}")

    counts = {"accepted": 0, "rejected": 0, "quarantined": 0}
    total_prompt = 0
    total_completion = 0
    total_exec_wall = 0.0

    # Seed the running totals from already-completed work so the manifest is
    # correct on a resumed run, not just for the incremental slice.
    for record in completed.values():
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        total_prompt += record.get("prompt_tokens", 0)
        total_completion += record.get("completion_tokens", 0)
        total_exec_wall += record.get("exec_wall_s", 0.0)

    with open(results_path, "a", encoding="utf-8") as out:
        with TeacherClient(model=model, base_url=base_url) as teacher:
            for i, example in enumerate(pending, 1):
                record = process_example(teacher, example, max_repair)
                out.write(json.dumps(record, sort_keys=True) + "\n")
                out.flush()
                counts[record["status"]] = counts.get(record["status"], 0) + 1
                total_prompt += record["prompt_tokens"]
                total_completion += record["completion_tokens"]
                total_exec_wall += record["exec_wall_s"]
                if verbose:
                    print(
                        f"[generate] {i}/{len(pending)} "
                        f"{example['example_id']} {record['subfamily']} "
                        f"-> {record['status']} "
                        f"(calls={record['teacher_calls']} "
                        f"tok={record['prompt_tokens']}+{record['completion_tokens']} "
                        f"{record['total_wall_s']:.1f}s)"
                    )

    return {
        "model": model,
        "base_url": base_url,
        "total_examples": len(examples),
        "processed_this_run": len(pending),
        "counts": counts,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_exec_wall_s": total_exec_wall,
        "results_path": str(results_path),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--examples",
        type=Path,
        default=REPO_ROOT / "fixtures/smoke/tasks/examples.jsonl",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--max-repair", type=int, default=rep.DEFAULT_MAX_REPAIR_ATTEMPTS)
    parser.add_argument("--limit", type=int, default=None,
                        help="process at most N pending examples (for smoke tests)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    examples = load_examples(args.examples)
    summary = run_batch(
        examples,
        model=args.model,
        out_dir=args.out_dir,
        base_url=args.base_url,
        max_repair=args.max_repair,
        limit=args.limit,
        verbose=not args.quiet,
    )
    print(f"[generate] done: {summary['counts']} "
          f"(prompt={summary['total_prompt_tokens']} "
          f"completion={summary['total_completion_tokens']} tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
