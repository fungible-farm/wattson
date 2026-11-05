"""The ledger counts errors by class and costs recovered and unrecovered time (PC-REQ-4)."""

from __future__ import annotations

import json
from typing import Any

from wattson.inference.error_ledger import ledger

REPORT: dict[str, Any] = {"network_id": "snem-sa-2000", "operation": "none", "max_iter": 50}


def turn(seconds: float, tokens: int) -> dict[str, Any]:
    return {"kind": "model_response", "payload": {"elapsed_seconds": seconds, "usage": {"completion_tokens": tokens}}}


def call(i: int, arguments: dict[str, Any], status: str, error: str | None = None) -> list[dict[str, Any]]:
    text = json.dumps({"status": status, "error": error})
    return [{"kind": "tool_request", "payload": {"id": f"c{i}", "function": {"name": "run_study_trial",
                                                                              "arguments": json.dumps(arguments)}}},
            {"kind": "tool_response", "payload": {"tool_call_id": f"c{i}", "tool_seconds": 1.0,
                                                  "result": {"content": [{"type": "text", "text": text}]}}}]


def test_a_recovered_unknown_id_costs_recovery_not_lost_time() -> None:
    events = ([turn(10, 100)] + call(1, REPORT | {"observation_id": 999}, "execution_failure", "Unknown observation element 999")
              + [turn(5, 50)] + call(2, REPORT, "success") + [turn(2, 20), {"kind": "terminal", "payload": {"state": "answered"}}])
    row = ledger(events)
    assert row["errors"] == {"unknown_id": 1} and (row["recovered"], row["unrecovered"]) == (1, 0)
    assert row["recovery_s"] == 6.0 and row["recovery_tokens"] == 50
    assert row["unrecoverable_s"] == 0.0 and row["total_s"] == 19.0 and row["tool_time_journaled"]


def test_an_id_counting_loop_is_unrecoverable_from_its_first_error() -> None:
    events = [turn(10, 100)]
    for i in range(3):
        events += call(i, REPORT | {"observation_id": i}, "execution_failure", f"Unknown observation element {i}") + [turn(4, 40)]
    events.append({"kind": "terminal", "payload": {"state": "turn_budget_exhausted"}})
    row = ledger(events)
    assert row["errors"] == {"unknown_id": 3, "turn_budget_exhausted": 1} and row["unrecovered"] == 3
    assert row["unrecoverable_s"] == row["total_s"] == 25.0, "an unanswered attempt loses all of its time"


def test_an_answered_attempt_loses_only_the_time_after_its_first_unrecovered_error() -> None:
    events = ([turn(10, 100)] + call(1, REPORT, "success") + [turn(3, 30)]
              + call(2, REPORT | {"contingency_line": 7}, "non_convergence") + [turn(2, 20)]
              + [{"kind": "terminal", "payload": {"state": "answered"}}])
    row = ledger(events)
    assert row["errors"] == {"solver": 1} and row["unrecoverable_s"] == 2.0 and row["unrecoverable_tokens"] == 20
