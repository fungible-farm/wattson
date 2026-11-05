"""Lab 10 self-checks: pinned sources, the local transport's translation, and witness scores."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LAB: Path = Path(__file__).resolve().parents[3] / "benchmarks" / "restorebench"
sys.path.insert(0, str(LAB))

import fetch_restorebench  # noqa: E402
from local_provider import to_chat, to_tools  # noqa: E402

FETCHED: bool = (fetch_restorebench.CHECKOUT / ".git").is_dir()


@pytest.mark.skipif(not FETCHED, reason="run `just lab10-fetch` first")
def test_checkout_is_the_pinned_commit_with_unchanged_files() -> None:
    """A different commit or an edited corpus would be a different benchmark."""
    assert fetch_restorebench.verify(json.loads(fetch_restorebench.EXPECTED.read_text())) == []


def test_tool_use_and_results_become_chat_tool_calls() -> None:
    """Converse toolUse/toolResult blocks map onto one assistant call and one tool message."""
    messages = [
        SimpleNamespace(role="system", content="S"),
        SimpleNamespace(role="user", content=[{"text": "card"}]),
        SimpleNamespace(role="assistant", content=[{"text": "checking"}, {"toolUse": {
            "toolUseId": "t1", "name": "run_ac_pf", "input": {"maneuver": None}}}]),
        SimpleNamespace(role="user", content=[{"toolResult": {"toolUseId": "t1", "status": "success",
                                                              "content": [{"json": {"converged": False}}]}},
                                              {"text": "continue"}]),
    ]
    assert to_chat(messages) == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "card"},
        {"role": "assistant", "content": "checking", "tool_calls": [{"id": "t1", "type": "function", "function": {
            "name": "run_ac_pf", "arguments": '{"maneuver": null}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": '{"converged": false}'},
        {"role": "user", "content": "continue"},
    ]


def test_converse_tool_specs_become_chat_functions() -> None:
    """The JSON schema travels unchanged under Chat Completions' ``parameters``."""
    schema = {"type": "object", "properties": {"maneuver": {"type": "object"}}}
    assert to_tools({"tools": [{"toolSpec": {"name": "run_ac_pf", "description": "d", "inputSchema": {"json": schema}}}]}) == [
        {"type": "function", "function": {"name": "run_ac_pf", "description": "d", "parameters": schema}}]


@pytest.mark.skipif(not FETCHED or not os.environ.get("RUN_SLOW_LABS"),
                    reason="~5 min of AC power flow; opt in with RUN_SLOW_LABS=1 after `just lab10-fetch`")
def test_witness_scores_match_the_committed_fixture() -> None:
    """Re-scores all 92 witnesses in RestoreBench's own environment."""
    run = subprocess.run([sys.executable, str(LAB / "restorebench_lab.py"), "--step", "check"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
