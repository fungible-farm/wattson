"""Precedence gates for the per-attempt failure-mode classifier."""

from __future__ import annotations

import json
from typing import Any

import pytest

from wattson.inference import failure_modes

TASK: dict[str, Any] = {"example_id": "ex_test", "subfamily": "pf-report",
                        "task": {"inputs": {"case": "snem-sa-2000", "contingency_line": 252}}}


def response(finish: str, content: str = "", prompt_tokens: int = 1000) -> dict[str, Any]:
    """A model_response journal event with the fields the classifier reads."""
    return {"kind": "model_response", "payload": {
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens}}}


def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "tool_request", "payload": {"id": "c1", "function": {"name": name, "arguments": json.dumps(arguments)}}}


def result(passed: bool = False, answer: str = "") -> dict[str, Any]:
    return {"example_id": "ex_test", "answer": answer, "passed": passed}


@pytest.fixture(autouse=True)
def no_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these gates CPU-only; the real grader is exercised by its own tests."""
    monkeypatch.setattr(failure_modes, "score_numerical", lambda _task, _answer: (False, {}, []))
    monkeypatch.setattr(failure_modes, "relevant_evidence", lambda *_args: False)
    monkeypatch.setattr(failure_modes, "provenance_check", lambda *_args: {"traced": True})


def test_contingency_silently_dropped_by_run_power_flow() -> None:
    events = [call("run_power_flow", {"network_id": "snem-sa-2000", "contingency_line": 252}),
              response("stop", "converged in 5 iterations"),
              {"kind": "terminal", "payload": {"state": "answered"}}]
    attempt = failure_modes.classify(TASK, result(answer="converged in 5 iterations"), events)
    assert attempt["cause"] == "contingency_dropped"


def test_token_cap_is_reported_with_its_truncated_turn() -> None:
    events = [response("tool_calls", prompt_tokens=1001), response("length", prompt_tokens=7583),
              {"kind": "terminal", "payload": {"state": "incomplete_generation"}}]
    attempt = failure_modes.classify(TASK, result(), events)
    assert (attempt["cause"], attempt["truncated_turns"], attempt["max_prompt_tokens"]) == ("response_token_cap", 1, 7583)


def test_solver_correct_answer_outranks_failure_causes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(failure_modes, "score_numerical", lambda _task, _answer: (True, {}, []))
    events = [response("stop", "report"), {"kind": "terminal", "payload": {"state": "answered"}}]
    assert failure_modes.classify(TASK, result(answer="report"), events)["cause"] == "solver_correct_strict_rejected"


def test_none_string_errors_are_counted_as_an_overlay() -> None:
    error = {"kind": "tool_response", "payload": {"result": {"isError": True, "content": [
        {"type": "text", "text": "Input validation error: 'None' is not of type 'integer', 'null'"}]}}}
    events = [error, error, {"kind": "terminal", "payload": {"state": "turn_budget_exhausted"}}]
    attempt = failure_modes.classify(TASK, result(), events)
    assert (attempt["cause"], attempt["none_string_errors"]) == ("turn_budget_exhausted", 2)


def test_inference_timeout_is_a_per_attempt_cause() -> None:
    events = [response("tool_calls"), {"kind": "terminal", "payload": {"state": "inference_timeout"}}]
    assert failure_modes.classify(TASK, result(), events)["cause"] == "inference_timeout"


def test_an_answer_with_numbers_no_tool_produced_is_fabricated(monkeypatch: pytest.MonkeyPatch) -> None:
    """User rule: a made-up number fails the attempt, ahead of every other cause."""
    from wattson.inference.provenance import check

    monkeypatch.setattr(failure_modes, "provenance_check", check)
    monkeypatch.setattr(failure_modes, "score_numerical", lambda _task, _answer: (True, {}, []))
    events = [response("stop", "converged in 5 iterations"), {"kind": "terminal", "payload": {"state": "answered"}}]
    assert failure_modes.classify(TASK, result(answer="converged in 5 iterations"), events)["cause"] == "fabricated"


def test_narration_left_by_an_exhausted_budget_is_not_fabricated(monkeypatch: pytest.MonkeyPatch) -> None:
    """r6b Qwen student ex_a98c21a0b943d419: the last turn's narration is not an answer."""
    from wattson.inference.provenance import check

    monkeypatch.setattr(failure_modes, "provenance_check", check)
    narration = "Bus 9 fails. I'll try bus 10."
    events = [response("tool_calls", narration), {"kind": "terminal", "payload": {"state": "turn_budget_exhausted"}}]
    assert failure_modes.classify(TASK, result(answer=narration), events)["cause"] == "turn_budget_exhausted"
