"""Manifest + teacher-comparison reporting for the M05 distillation work package.

Builds a per-batch manifest (acceptance rate, solver call counts, rejection
reasons, generation/validation cost) from a results.jsonl, and a side-by-side
teacher-comparison report from two per-batch manifests. The comparison is what
the mission asks for: "compare the Qwen3.8-Flash-Next and Qwen3.8-27B teachers
on the same held-out set" and identify which is the better teacher.

Usage:
    # per-batch manifest
    uv run python powercoder/distillation/manifest.py \
        --results powercoder/distillation/out/<model>/results.jsonl \
        --out powercoder/distillation/out/<model>/manifest.json

    # teacher comparison (two per-batch manifests)
    uv run python powercoder/distillation/manifest.py \
        --compare <modelA>/manifest.json <modelB>/manifest.json \
        --out powercoder/distillation/out/comparison.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
RESULTS_FILENAME: str = "results.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def load_results(results_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def classify_reason(record: dict[str, Any]) -> str:
    """Bucket a terminal record into a coarse rejection reason.

    This is a deterministic classifier over the record's status + diagnostics
    text (no LLM inference), so the rejection-reason report is reproducible.
    """
    if record["status"] == "accepted":
        return "accepted"
    diags = " ".join(record.get("diagnostics", []))
    if "could not execute" in diags or "timed out" in diags or "non-zero exit" in diags:
        return "execution_error"
    if "no parseable answer" in diags:
        return "no_parseable_answer"
    if "out of range" in diags:
        return "out_of_range"
    if "did not converge" in diags or "non-converged" in diags:
        return "non_convergence"
    return "numerical_mismatch"


def build_manifest(
    records: list[dict[str, Any]],
    model: str,
    dataset_content_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate a list of terminal records into a per-batch manifest."""
    counts = {"accepted": 0, "rejected": 0, "quarantined": 0}
    by_subfamily: dict[str, dict[str, int]] = {}
    reasons: dict[str, int] = {}
    total_teacher_calls = 0
    total_prompt = 0
    total_completion = 0
    total_exec_wall = 0.0
    total_wall = 0.0
    total_repair_attempts = 0

    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        sub = r.get("subfamily", "unknown")
        by_subfamily.setdefault(sub, {"accepted": 0, "rejected": 0, "quarantined": 0})
        by_subfamily[sub][r["status"]] = by_subfamily[sub].get(r["status"], 0) + 1
        reason = classify_reason(r)
        reasons[reason] = reasons.get(reason, 0) + 1
        total_teacher_calls += r.get("teacher_calls", 0)
        total_prompt += r.get("prompt_tokens", 0)
        total_completion += r.get("completion_tokens", 0)
        total_exec_wall += r.get("exec_wall_s", 0.0)
        total_wall += r.get("total_wall_s", 0.0)
        total_repair_attempts += r.get("repair_attempts", 0)

    total = len(records)
    accepted = counts.get("accepted", 0)
    acceptance_rate = (accepted / total) if total else 0.0

    # Every teacher call is followed by exactly one candidate execution and one
    # independent-check solve, so the solver-call count equals the teacher-call
    # count (1 initial + repair attempts per example).
    return {
        "model": model,
        "created_utc": _utc_now(),
        "git_commit": _git_commit(),
        "dataset_content_hash": dataset_content_hash,
        "total_examples": total,
        "counts": counts,
        "acceptance_rate": acceptance_rate,
        "counts_by_subfamily": by_subfamily,
        "rejection_reasons": reasons,
        "total_teacher_calls": total_teacher_calls,
        "total_solver_calls": total_teacher_calls,
        "total_repair_attempts": total_repair_attempts,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "total_exec_wall_s": total_exec_wall,
        "total_wall_s": total_wall,
    }


def compare_models(
    manifest_a: dict[str, Any],
    manifest_b: dict[str, Any],
) -> dict[str, Any]:
    """Side-by-side teacher comparison on the same held-out set."""
    a, b = manifest_a, manifest_b
    rate_a = a.get("acceptance_rate", 0.0)
    rate_b = b.get("acceptance_rate", 0.0)
    if rate_a > rate_b:
        better = a["model"]
    elif rate_b > rate_a:
        better = b["model"]
    else:
        better = "tie"

    # Per-subfamily acceptance, aligned on the union of subfamilies.
    subs = sorted(set(a.get("counts_by_subfamily", {})) | set(b.get("counts_by_subfamily", {})))
    per_subfamily = {}
    for sub in subs:
        sa = a.get("counts_by_subfamily", {}).get(sub, {})
        sb = b.get("counts_by_subfamily", {}).get(sub, {})
        tot_a = sum(sa.values()) or 1
        tot_b = sum(sb.values()) or 1
        per_subfamily[sub] = {
            a["model"]: {
                "accepted": sa.get("accepted", 0),
                "total": sum(sa.values()),
                "rate": sa.get("accepted", 0) / tot_a,
            },
            b["model"]: {
                "accepted": sb.get("accepted", 0),
                "total": sum(sb.values()),
                "rate": sb.get("accepted", 0) / tot_b,
            },
        }

    return {
        "created_utc": _utc_now(),
        "models": [a["model"], b["model"]],
        "better_teacher": better,
        "acceptance_rate": {a["model"]: rate_a, b["model"]: rate_b},
        "total_tokens": {a["model"]: a.get("total_tokens", 0), b["model"]: b.get("total_tokens", 0)},
        "total_teacher_calls": {a["model"]: a.get("total_teacher_calls", 0), b["model"]: b.get("total_teacher_calls", 0)},
        "total_exec_wall_s": {a["model"]: a.get("total_exec_wall_s", 0.0), b["model"]: b.get("total_exec_wall_s", 0.0)},
        "rejection_reasons": {a["model"]: a.get("rejection_reasons", {}), b["model"]: b.get("rejection_reasons", {})},
        "per_subfamily": per_subfamily,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--model", default=None,
                        help="model name (inferred from results if omitted)")
    parser.add_argument("--dataset-hash", default=None,
                        help="M04 dataset content_hash for provenance")
    parser.add_argument("--compare", nargs=2, type=Path, default=None,
                        metavar=("MANIFEST_A", "MANIFEST_B"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.compare:
        ma = json.loads(args.compare[0].read_text())
        mb = json.loads(args.compare[1].read_text())
        report = compare_models(ma, mb)
        payload = report
    else:
        if not args.results:
            parser.error("--results required unless --compare is given")
        records = load_results(args.results)
        model = args.model or (records[0]["model"] if records else "unknown")
        payload = build_manifest(records, model, args.dataset_hash)

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
        print(f"[manifest] wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
