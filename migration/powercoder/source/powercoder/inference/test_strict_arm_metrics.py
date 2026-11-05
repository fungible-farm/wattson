"""strict_arm_metrics counts per call and per turn, from journals alone."""

from __future__ import annotations

import json
from pathlib import Path

from powercoder.inference import strict_arm_metrics as sam


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_counts_schema_errors_none_strings_off_task_calls_and_tokens(tmp_path: Path) -> None:
    root = tmp_path / "arm"
    cell = root / "qwen-base-strict-parameter-first-auto"
    cell.mkdir(parents=True)
    (cell / "summary.json").write_text("{}")
    write(cell / "results.jsonl", [{"kind": "result", "payload": {"example_id": "ex_a", "passed": True}}])
    response = lambda finish, reasoning: {"kind": "model_response", "payload": {
        "choices": [{"finish_reason": finish, "message": {}}],
        "usage": {"completion_tokens": reasoning + 10, "completion_tokens_details": {"reasoning_tokens": reasoning}}}}
    call = lambda args: {"kind": "tool_request", "payload": {"function": {"name": "run_study_trial", "arguments": json.dumps(args)}}}
    error = {"kind": "tool_response", "payload": {"result": {"isError": True, "content": [
        {"type": "text", "text": "Input validation error: 'None' is not of type 'integer', 'null'"}]}}}
    write(cell / "ex_a.jsonl", [response("tool_calls", 100), call({"network_id": "other", "contingency_line": None}), error,
                                response("length", 300)])
    (root / "x-qwen-base-strict-parameter-server.log").write_text("non-default args: {'tool_strict_level': 'parameter'}")
    tasks = tmp_path / "tasks.jsonl"
    write(tasks, [{"example_id": "ex_a", "task": {"inputs": {"case": "snem-sa-2000"}}}])
    [metrics] = sam.arm_metrics(root, tasks)["qwen-base"]
    assert (metrics["passes"], metrics["tool_calls"], metrics["schema_errors"], metrics["none_strings"],
            metrics["off_task_trials"], metrics["turns"], metrics["truncated_turns"]) == (1, 1, 1, 1, 1, 2, 1)
    assert (metrics["first_turn_median_reasoning_tokens"], metrics["server_strict_level"]) == (100.0, "parameter")
