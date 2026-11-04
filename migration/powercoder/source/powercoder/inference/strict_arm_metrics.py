"""Per-call and per-turn metrics for the strict-tool-calling 2x2 (backlog 0011, follow-up 1).

The 2x2's 20-task pass rates can only show large effects, and r6b showed that every model
already calls a tool first and that the students make no schema errors. The questions
that remain are per call and per turn, so this reads each cell's journals and reports:

- schema-validation errors per tool call, and ``"None"`` strings among them (what
  ``--tool-strict-level parameter`` should make impossible);
- completion and reasoning tokens per turn, and turns cut off by the token cap (whether
  forcing the first call shortens the reasoning before it, FM-2);
- well-formed ``run_study_trial`` calls that study something other than the task asked
  (wrong case or contingency): the "useless forced call" 0011 asked to count;
- for each cell's server, whether its startup arguments carry the strict level, i.e.
  evidence that the grammar setting was in force.

Each non-baseline cell is reported next to its model's own (auto, auto) cell. Read-only:
journals and server logs, never a model.

Usage:
    uv run python -m powercoder.inference.strict_arm_metrics training/unsloth/out/agent-study-r6b-strict
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any, TypedDict

from powercoder.datasets.audit import load_rows

SCHEMA_ERROR: str = "Input validation error"
NONE_STRING: str = "'None' is not of type"
CELL: re.Pattern[str] = re.compile(r"(?P<model>.+)-strict-(?P<level>auto|parameter)-first-(?P<choice>auto|required)$")
STRICT_FLAG: re.Pattern[str] = re.compile(r"'tool_strict_level': '(\w+)'")


class CellMetrics(TypedDict):
    """One cell's per-call and per-turn measurements."""

    cell: str
    attempts: int
    passes: int
    tool_calls: int
    schema_errors: int
    none_strings: int
    off_task_trials: int
    turns: int
    truncated_turns: int
    median_completion_tokens: float
    median_reasoning_tokens: float
    first_turn_median_reasoning_tokens: float
    server_strict_level: str | None


def server_level(root: Path, model: str, level: str) -> str | None:
    """The strict level the cell's vLLM server logged in its non-default args, if any."""
    log = next(root.glob(f"*-{model}-strict-{level}-server.log"), None)
    if log is None:
        return None
    found = STRICT_FLAG.search(log.read_text(errors="replace"))
    return found.group(1) if found else "auto"


def cell_metrics(root: Path, directory: Path, tasks: dict[str, dict[str, Any]]) -> CellMetrics:
    """Measure one cell from its journals and results."""
    match = CELL.match(directory.name)
    # results.jsonl rows are {"kind": "result", "payload": {...}} records.
    rows = [r.get("payload", r) for r in load_rows(directory / "results.jsonl")] \
        if (directory / "results.jsonl").exists() else []
    results = {r["example_id"]: r for r in rows if "example_id" in r}
    calls = errors = nones = off_task = turns = truncated = 0
    completion, reasoning, first_reasoning = [], [], []
    journals = sorted(directory.glob("ex_*.jsonl"))
    for journal in journals:
        inputs = tasks.get(journal.stem, {})
        first = True
        for event in load_rows(journal):
            if event["kind"] == "model_response":
                turns += 1
                usage = event["payload"].get("usage") or {}
                completion.append(usage.get("completion_tokens") or 0)
                thought = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                reasoning.append(thought)
                if first:
                    first_reasoning.append(thought)
                    first = False
                truncated += event["payload"]["choices"][0]["finish_reason"] == "length"
            elif event["kind"] == "tool_request":
                calls += 1
                function = event["payload"]["function"]
                arguments = json.loads(function["arguments"] or "{}")
                if function["name"] == "run_study_trial" and isinstance(arguments, dict) and (
                        arguments.get("network_id") != inputs.get("case")
                        or arguments.get("contingency_line") != inputs.get("contingency_line")):
                    off_task += 1
            elif event["kind"] == "tool_response" and event["payload"]["result"].get("isError"):
                text = " ".join(c.get("text", "") for c in event["payload"]["result"].get("content", []))
                errors += SCHEMA_ERROR in text
                nones += NONE_STRING in text
    median = lambda values: float(statistics.median(values)) if values else 0.0
    return {"cell": directory.name, "attempts": len(journals),
            "passes": sum(bool(r.get("passed")) for r in results.values()), "tool_calls": calls,
            "schema_errors": errors, "none_strings": nones, "off_task_trials": off_task, "turns": turns,
            "truncated_turns": truncated, "median_completion_tokens": median(completion),
            "median_reasoning_tokens": median(reasoning), "first_turn_median_reasoning_tokens": median(first_reasoning),
            "server_strict_level": server_level(root, match["model"], match["level"]) if match else None}


def arm_metrics(root: Path, tasks_path: Path) -> dict[str, list[CellMetrics]]:
    """Every finished cell, grouped by model, the (auto, auto) baseline first."""
    tasks = {r["example_id"]: r["task"].get("inputs", {}) for r in load_rows(tasks_path)}
    order = ["auto-first-auto", "auto-first-required", "parameter-first-auto", "parameter-first-required"]
    by_model: dict[str, list[CellMetrics]] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir() and CELL.match(p.name)):
        if not (directory / "summary.json").exists():
            continue
        by_model.setdefault(CELL.match(directory.name)["model"], []).append(cell_metrics(root, directory, tasks))
    for cells in by_model.values():
        cells.sort(key=lambda c: order.index(c["cell"].split("-strict-")[1]))
    return by_model


def main() -> None:
    """Print each finished cell's metrics as JSON."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--tasks", type=Path, default=Path("training/unsloth/out/smoke-validation-20-20261022/examples.jsonl"))
    args = parser.parse_args()
    print(json.dumps(arm_metrics(args.root, args.tasks), indent=1))


if __name__ == "__main__":
    main()
