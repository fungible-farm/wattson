"""Arithmetic exposure counts numbers a model wrote that no prompt, tool reply or printed result gave it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wattson.inference import arithmetic_exposure as ae

PROMPT: str = "Find s in [0.9, 1.1] so the voltage at bus 405 equals 1.034339 pu (within 0.0015)."


def journal(path: Path, turns: list[dict[str, Any]]) -> Path:
    """A journal: the start, then (assistant message, tool reply text) pairs."""
    events: list[dict[str, Any]] = [{"kind": "start", "payload": {"messages": [{"role": "system", "content": "s"},
                                                                                 {"role": "user", "content": PROMPT}]}}]
    for turn in turns:
        events.append({"kind": "model_response", "payload": {"choices": [{"message": turn["message"]}]}})
        if "reply" in turn:
            events.append({"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": turn["reply"]}]}}})
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def call(**arguments: Any) -> dict[str, Any]:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"function": {"name": "run_study_trial", "arguments": json.dumps(arguments)}}]}


def test_a_midpoint_the_model_computed_and_sent_is_untraced(tmp_path: Path) -> None:
    turns = [{"message": call(scale=0.9, observation_id=405), "reply": json.dumps({"data": {"metric": 1.0301}})},
             {"message": call(scale=1.0375, observation_id=405),
              "reply": json.dumps({"data": {"study": {"scale": 1.0375}, "metric": 1.0344}})},
             {"message": {"role": "assistant", "content": "1.0375"}}]
    result = ae.exposure(journal(tmp_path / "a.jsonl", turns))
    assert result["by_place"]["arguments"] == 1 and result["examples"]["arguments"] == ["1.0375"]
    assert result["by_place"]["answer"] == 0, "the answer repeats a scale the solver's reply echoed back"


def test_the_prompt_a_reply_and_a_printed_result_are_sources(tmp_path: Path) -> None:
    code = "TARGET = 1.034339\nTOL = 0.0015\nfor _ in range(40):\n    pass\nprint(0.5)"
    turns = [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"function": {"name": "run_python", "arguments": json.dumps({"code": code})}}]},
              "reply": json.dumps({"data": {"stdout": "scale=1.037500 residual=+0.000301\n"}})},
             {"message": {"role": "assistant", "content": "1.037500",
                          "reasoning": "The program printed scale 1.037500 with residual +0.000301."}}]
    result = ae.exposure(journal(tmp_path / "b.jsonl", turns))
    assert result["untraced"] == 0, result


def test_a_residual_worked_out_in_the_reasoning_is_untraced(tmp_path: Path) -> None:
    turns = [{"message": {"role": "assistant", "content": None, "reasoning": "1.0362 minus 1.0343 is 0.0019, above the tolerance.",
                          "tool_calls": [{"function": {"name": "run_python", "arguments": json.dumps({"code": "print(1)"})}}]},
              "reply": json.dumps({"data": {"stdout": "1.0362\n"}})}]
    result = ae.exposure(journal(tmp_path / "c.jsonl", turns))
    assert result["by_place"]["reasoning"] >= 1 and "0.0019" in result["examples"]["reasoning"]


def test_a_limit_stated_in_the_tool_schema_is_a_source(tmp_path: Path) -> None:
    path = journal(tmp_path / "d.jsonl", [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"function": {"name": "run_python", "arguments": json.dumps({"code": "LOW = 0.85"})}}]},
        "reply": json.dumps({"data": {"stdout": ""}})}])
    assert ae.exposure(path)["untraced"] == 1, "0.85 is not in the prompt of this journal"
    events = path.read_text().splitlines()
    events.insert(1, json.dumps({"kind": "tools", "payload": [{"function": {"parameters": {"properties": {"scale": {"minimum": 0.85}}}}}]}))
    path.write_text("\n".join(events) + "\n")
    assert ae.exposure(path)["untraced"] == 0
