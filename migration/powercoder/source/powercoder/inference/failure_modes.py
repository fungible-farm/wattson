"""Classify every saved agent attempt into one known failure mode.

Post-processing only: reads a run directory's ``results.jsonl`` and per-task
``ex_*.jsonl`` journals, never calls a model, and re-solves nothing except
through the frozen ``solver-verified-domain-v1`` grader. Each attempt gets the
first matching cause in ``CAUSES`` order, so counts are deterministic and add
up to the attempts in the run. The modes themselves are documented in
AGENTS.md ("PowerCoder agent evaluation: known failure modes").

Usage:
    uv run python -m powercoder.inference.failure_modes training/unsloth/out/agent-domain-r3/base
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, TypedDict

from powercoder.datasets.audit import load_rows
from powercoder.inference.domain_scorecard import score_numerical
from powercoder.inference.evaluate_agent import relevant_evidence
from powercoder.inference.provenance import check as provenance_check

TASKS: Path = Path("benchmarks/power-agent-bench-lite/results/domain-held-out-20261020/examples.jsonl")
# Precedence order: a strict pass first, then grader disagreement, then the
# first defect that made the attempt fail.
CAUSES: tuple[str, ...] = (
    "pass",
    "fabricated",
    "solver_correct_strict_rejected",
    "contingency_dropped",
    "response_token_cap",
    "turn_budget_exhausted",
    "context_exhausted",
    "inference_timeout",
    "invalid_tool_protocol",
    "model_protocol_error",
    "empty_stop",
    "answered_wrong",
)
# The MCP schema allows integer-or-null; Qwen3.5 base often sends the string.
NONE_STRING_ERROR: str = "'None' is not of type"


class Attempt(TypedDict):
    """One attempt's cause plus the overlays that can co-occur with any cause."""

    example_id: str
    subfamily: str
    cause: str
    terminal: str
    turns: int
    truncated_turns: int
    max_prompt_tokens: int
    none_string_errors: int


def classify(task: dict[str, Any], result: dict[str, Any], events: list[dict[str, Any]]) -> Attempt:
    """Assign the attempt's single precedence-ordered cause.

    Args:
        task: Benchmark row (prompt, inputs, reference).
        result: The run's ``results.jsonl`` payload for this task.
        events: The task's raw journal.

    Returns:
        The attempt's cause and overlay counters.
    """
    responses = [e["payload"] for e in events if e["kind"] == "model_response"]
    finishes = [r["choices"][0]["finish_reason"] for r in responses]
    terminal = next((e["payload"].get("state", e["kind"]) if e["kind"] == "terminal" else e["kind"]
                     for e in reversed(events) if e["kind"] in ("terminal", "model_protocol_error")), "none")
    requests = [e["payload"]["function"] for e in events if e["kind"] == "tool_request"]
    errors = [c.get("text", "") for e in events if e["kind"] == "tool_response" and e["payload"]["result"].get("isError")
              for c in e["payload"]["result"].get("content", [])]
    answer = result["answer"]
    solver_ok = bool(answer) and score_numerical(task, answer)[0]
    dropped = any(r["name"] == "run_power_flow" and "contingency_line" in json.loads(r["arguments"] or "{}")
                  for r in requests)
    traced = provenance_check(answer, events, task["task"].get("prompt", ""))["traced"]
    if result["passed"]:
        cause = "pass"
    elif terminal == "answered" and answer and not traced:
        # A number not from the solver fails the attempt even when it is right.
        # Only a final answer counts: when the budget runs out, evaluate_agent
        # records the last turn's narration ("Bus 9 fails. I'll try bus 10.")
        # as the answer, and its numbers are not a reported result.
        cause = "fabricated"
    elif solver_ok:
        cause = "solver_correct_strict_rejected"
    elif terminal == "answered" and dropped and not relevant_evidence(task, answer, events):
        cause = "contingency_dropped"
    elif "length" in finishes:
        cause = "response_token_cap"
    elif terminal in ("turn_budget_exhausted", "context_exhausted", "inference_timeout",
                      "invalid_tool_protocol", "model_protocol_error"):
        cause = terminal
    elif terminal == "incomplete_generation":
        cause = "empty_stop"
    else:
        cause = "answered_wrong"
    return {"example_id": task["example_id"], "subfamily": task["subfamily"], "cause": cause,
            "terminal": terminal, "turns": len(responses), "truncated_turns": finishes.count("length"),
            "max_prompt_tokens": max((r["usage"]["prompt_tokens"] for r in responses), default=0),
            "none_string_errors": sum(NONE_STRING_ERROR in e for e in errors)}


def classify_run(directory: Path, tasks: Path = TASKS) -> list[Attempt]:
    """Classify every scored attempt in a (possibly still running) run directory."""
    rows = {row["example_id"]: row for row in load_rows(tasks)}
    attempts = []
    for scored in load_rows(directory / "results.jsonl"):
        result = scored["payload"]
        attempts.append(classify(rows[result["example_id"]], result,
                                 load_rows(directory / f"{result['example_id']}.jsonl")))
    return attempts


def main() -> None:
    """Print per-subfamily cause counts, overlays, and per-task rows as JSON."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tasks", type=Path, default=TASKS)
    args = parser.parse_args()
    attempts = classify_run(args.directory, args.tasks)
    counts = Counter(f"{a['subfamily']}:{a['cause']}" for a in attempts)
    print(json.dumps({
        "attempts": len(attempts),
        "causes": dict(sorted(counts.items())),
        "with_none_string_errors": sum(a["none_string_errors"] > 0 for a in attempts),
        "max_prompt_tokens": max((a["max_prompt_tokens"] for a in attempts), default=0),
        "tasks": attempts,
    }, indent=2))


if __name__ == "__main__":
    main()
