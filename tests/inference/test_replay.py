"""Replay re-derives a journal's tool responses and flags any that changed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wattson.inference.mcp_server import respond, tool_definitions
from wattson.inference.powermcp import PowerMCP
from wattson.inference.replay import replay

TRIAL: dict[str, Any] = {"network_id": "fixture-2bus-ok", "operation": "load_scale", "scale": 1.0, "element_id": 0,
                         "observation": "bus_voltage", "observation_id": 1, "max_iter": 50}


def _journal(tmp_path: Path, shown: str, arguments: dict[str, Any], is_error: bool = False) -> Path:
    tools = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.inputSchema}}
             for t in tool_definitions(study_tools=True)]
    events = [
        {"kind": "tools", "payload": tools},
        {"kind": "tool_request", "payload": {"id": "c1", "type": "function",
                                             "function": {"name": "run_study_trial", "arguments": json.dumps(arguments)}}},
        {"kind": "tool_response", "payload": {"tool_call_id": "c1", "result": {
            "content": [{"type": "text", "text": shown}], "isError": is_error}}},
    ]
    path = tmp_path / "ex.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def test_an_unchanged_response_replays_identically(tmp_path: Path) -> None:
    shown = respond(PowerMCP(), "run_study_trial", TRIAL, study_tools=True)
    assert [c["outcome"] for c in replay(_journal(tmp_path, shown, TRIAL))] == ["identical"]


def test_a_changed_number_is_found_by_path(tmp_path: Path) -> None:
    shown = json.loads(respond(PowerMCP(), "run_study_trial", TRIAL, study_tools=True))
    shown["data"]["metric"] += 1e-12
    (call,) = replay(_journal(tmp_path, json.dumps(shown), TRIAL))
    assert call["outcome"] == "differs" and call["diff_paths"] == ["/data/metric"]


def test_a_schema_refusal_is_reproduced(tmp_path: Path) -> None:
    bad = TRIAL | {"contingency_line": "None"}  # FM-7: invalid under every schema version
    (call,) = replay(_journal(tmp_path, "Input validation error: 'None' is not of type 'integer', 'null'", bad, is_error=True))
    assert call["outcome"] == "error_reproduced"
