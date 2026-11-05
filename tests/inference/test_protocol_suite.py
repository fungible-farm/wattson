"""The protocol suite scores run_study_trial calls by the Rust IR's verdict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wattson.inference.protocol_suite import measure

VALID: dict[str, Any] = {"network_id": "snem-sa-2000", "operation": "none", "scale": 1, "element_id": 0,
                         "observation": "bus_voltage", "observation_id": 985, "max_iter": 50}


def _event(i: int, tool: str, arguments: dict[str, Any], text: str, is_error: bool = False) -> list[dict[str, Any]]:
    return [{"kind": "tool_request", "payload": {"id": f"c{i}", "function": {"name": tool, "arguments": json.dumps(arguments)}}},
            {"kind": "tool_response", "payload": {"tool_call_id": f"c{i}", "result": {
                "content": [{"type": "text", "text": text}], "isError": is_error}}}]


def test_valid_rate_repairs_tool_errors_and_other_tools(tmp_path: Path) -> None:
    ok, unknown = json.dumps({"status": "success"}), json.dumps({"status": "execution_failure", "error": "Unknown observation element"})
    events = (_event(1, "run_study_trial", VALID | {"contingency_line": "None"}, "Input validation error", is_error=True)
              + _event(2, "run_study_trial", VALID, ok)
              + _event(3, "run_study_trial", VALID | {"observation_id": 7}, unknown)
              + _event(4, "run_power_flow", {"network_id": "snem-sa-2000"}, ok))
    journal = tmp_path / "ex_1.jsonl"
    journal.write_text("".join(json.dumps(e) + "\n" for e in events))
    result = measure([journal])
    assert (result["calls"], result["valid"], result["invalid"]) == (3, 2, 1)
    assert (result["repaired"], result["tool_errors"], result["outside_ir"]) == (1, 1, 1)
    assert not result["meets_exit_criterion"]
    assert result["errors"] == [("invalid: contingency_line must be a non-negative integer", 1)]
