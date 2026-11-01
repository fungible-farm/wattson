"""Tests for agent_mode.py: prompt construction, grading with stub server, URL handling.

No GPU or real model needed. Uses the parity_check stub server to replay scripted
responses through the real harness.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
from wattson import fetch

SNEMSA = fetch.case_dir() / "snemSA.m"
if not SNEMSA.exists():
    pytest.skip("snemSA.m not downloaded", allow_module_level=True)

from pathlib import Path
import importlib.util

LAB_DIR = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("agent_mode", LAB_DIR / "agent_mode.py")
agent_mode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_mode)


@pytest.fixture
def constants():
    """Load run.py's constants without running the script."""
    return agent_mode._load_run_py_constants()


def test_prompt_contains_required_values(constants) -> None:
    """The built prompt contains bus 2008, 0.9422, 0.002 and [0.9, 1.1]."""
    task = agent_mode.build_task_row(constants)
    prompt = task["task"]["prompt"]
    assert "bus 2008" in prompt
    assert "0.9422" in prompt
    assert "0.002" in prompt
    assert "[0.9, 1.1]" in prompt


def test_prompt_reads_from_run_py_constants(constants) -> None:
    """The prompt values come from run.py's constants, not hardcoded here."""
    task = agent_mode.build_task_row(constants)
    prompt = task["task"]["prompt"]
    # These are the actual values from run.py
    assert str(constants["TARGET_BUS"]) in prompt
    assert str(constants["FIELD_SCADA_VOLTAGE_PU"]) in prompt
    assert str(constants["FIT_TOLERANCE_PU"]) in prompt


def test_stub_no_tool_call_fails(tmp_path: Path, constants) -> None:
    """A right number with no tool call exits 1 (tool evidence is required)."""
    from wattson.training.parity_check import stub_server

    captured: list = []
    server = stub_server([{"role": "assistant", "content": "0.925000"}], captured)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        out_dir = tmp_path / "out1"
        code = agent_mode.main([
            "--base-url", url, "--model", "stub",
            "--out", str(out_dir),
        ])
    finally:
        server.shutdown()

    assert code == 1, "should fail: no tool call made"


def test_stub_wrong_scale_fails(tmp_path: Path, constants) -> None:
    """A number far from the fit exits 1."""
    from wattson.training.parity_check import stub_server

    captured: list = []
    server = stub_server([{"role": "assistant", "content": "1.05"}], captured)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        out_dir = tmp_path / "out2"
        code = agent_mode.main([
            "--base-url", url, "--model", "stub",
            "--out", str(out_dir),
        ])
    finally:
        server.shutdown()

    assert code == 1, "should fail: scale 1.05 is far from the fit"


def test_server_root_accepts_both_forms() -> None:
    """Both http://h:8000 and http://h:8000/v1 give the same root."""
    import wattson.cli

    assert wattson.cli.server_root("http://h:8000") == "http://h:8000"
    assert wattson.cli.server_root("http://h:8000/v1") == "http://h:8000"
    assert wattson.cli.server_root("http://h:8000/") == "http://h:8000"
    assert wattson.cli.server_root("http://h:8000/v1/") == "http://h:8000"


# --- regression tests added in review: the delegated version counted 0 solver calls for every run_python solution, because it read `trials`
# --- from the top level of the tool reply; in the real reply they are under "data". None of its tests covered count_solver_calls.

import json


def journal_with(events: list[dict], path: Path) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def python_reply(trials: int) -> dict:
    """A run_python tool reply in the shape the real MCP server returns."""
    body = {"status": "ok", "data": {"stdout": "", "trials": [{"arguments": {}, "response": {}}] * trials}, "error": None}
    return {"content": [{"type": "text", "text": json.dumps(body)}]}


def test_solver_calls_are_counted_from_the_real_run_python_reply_shape(tmp_path: Path) -> None:
    module = agent_mode
    events = [{"kind": "tool_request", "payload": {"id": "c1", "function": {"name": "run_python"}}},
              {"kind": "tool_response", "payload": {"tool_call_id": "c1", "result": python_reply(6)}},
              {"kind": "tool_request", "payload": {"id": "c2", "function": {"name": "run_study_trial"}}},
              {"kind": "tool_response", "payload": {"tool_call_id": "c2", "result": {"content": [{"type": "text", "text": "{}"}]}}}]
    assert module.count_solver_calls(journal_with(events, tmp_path / "j.jsonl")) == 7  # 6 trials inside the program + 1 direct study


def test_an_unreadable_run_python_reply_counts_as_one_call_not_zero(tmp_path: Path) -> None:
    module = agent_mode
    events = [{"kind": "tool_request", "payload": {"id": "c1", "function": {"name": "run_python"}}},
              {"kind": "tool_response", "payload": {"tool_call_id": "c1", "result": {"content": [{"type": "text", "text": "not json"}]}}}]
    assert module.count_solver_calls(journal_with(events, tmp_path / "j.jsonl")) == 1


def test_the_prompt_has_no_unformatted_placeholder() -> None:
    module = agent_mode
    prompt = module.build_task_row(module._load_run_py_constants())["task"]["prompt"]
    assert "{" not in prompt and "snem-sa-2000" in prompt


def test_how_an_attempt_ended_is_reported(tmp_path: Path) -> None:
    module = agent_mode
    events = [{"kind": "terminal", "payload": {"state": "guard_refused"}}]
    assert module.attempt_ended(journal_with(events, tmp_path / "j.jsonl")) == "guard_refused"
