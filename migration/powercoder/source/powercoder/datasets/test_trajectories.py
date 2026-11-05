"""Stored trajectories must render exactly the prompt the evaluation serves."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from powercoder.datasets import trajectories
from powercoder.datasets.trajectories import write_in_order
from powercoder.inference import powermcp as pm
from powercoder.inference.mcp_server import tool_definitions


def served_tools() -> list[dict]:
    """The tool list exactly as tool_probe builds it from the MCP server."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.inputSchema}}
            for t in tool_definitions(study_tools=True)]


def test_stored_tools_keep_the_served_key_order(tmp_path: Path) -> None:
    """Sorted keys changed every tool block the students were trained on (FM-23)."""
    tools = served_tools()
    path = write_in_order(tmp_path / "t.jsonl", [{"example_id": "e", "subfamily": "pf-report", "fault": None,
                                                  "tools": tools, "messages": []}])
    stored = json.loads(path.read_text())["tools"]
    assert json.dumps(stored) == json.dumps(tools)
    assert list(stored[0]) == ["type", "function"] and list(stored[0]["function"])[0] == "name"


CASE: str = "snem-sa-2000"
NEEDS_DATA = pytest.mark.skipif(not (pm.REPO_ROOT / pm.CASE_FILES[CASE]).exists(),
                                reason="needs data/ (scripts/fetch_csiro_nem_data.py)")
REPORT: dict = {"example_id": "ex_report", "subfamily": "pf-report", "lifecycle": {"split": "train"},
                "task": {"prompt": f"Run an AC power flow on case '{CASE}' with line 94 out of service (N-1 contingency) "
                                   "and report the result.", "inputs": {"case": CASE, "contingency_line": 94}}}
LINE_FIT: dict = {"example_id": "ex_line_fit", "subfamily": "pf-line-rating-fit", "topology": CASE,
                  "lifecycle": {"split": "train", "status": "verified"},
                  "task": {"prompt": f"Using the power flow solver, find the scale factor s in [0.9, 1.1] applied to line 144 "
                                     f"on case '{CASE}' such that the line loading equals 0.398714 percent (within 0.0016 "
                                     "percent). Return only the scale factor.",
                           "inputs": {"case": CASE, "element": "line 144", "scale_lo": 0.9, "scale_hi": 1.1,
                                      "target": 0.398714, "tolerance": 0.0016}}}


def calls_of(trajectory: dict) -> list[dict]:
    return [json.loads(m["tool_calls"][0]["function"]["arguments"])
            for m in trajectory["messages"] if m["role"] == "assistant" and m.get("tool_calls")]


def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: dict, fault: str | None) -> dict:
    journals = tmp_path / "journals"
    journals.mkdir(exist_ok=True)
    # Grading needs the row's reference; the full build grades every kept row with grade().
    monkeypatch.setattr(trajectories, "grade", lambda _row, _journal: {"passed": True})
    trajectory, reason = asyncio.run(trajectories.build(row, journals, fault))
    assert reason == "kept", reason
    return trajectory


@NEEDS_DATA
def test_a_report_sends_no_placeholder_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IR 0.2.0: the trained report call carries no element, observation or scale."""
    (call,) = calls_of(built(tmp_path, monkeypatch, REPORT, None))
    assert call == {"network_id": CASE, "operation": "none", "contingency_line": 94, "max_iter": trajectories.TRIAL_MAX_ITER}


@NEEDS_DATA
def test_invented_observation_recovery_drops_the_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bad id is absent from the network, drawn per row (no fixed number), and the fix omits it."""
    from powercoder.datasets.unsolvable import element_ids

    trajectory = built(tmp_path, monkeypatch, REPORT, "invented_observation")
    first, second = calls_of(trajectory)
    assert first["observation_id"] not in element_ids(CASE, "bus")
    assert "observation_id" not in second and second["contingency_line"] == 94
    note = [m for m in trajectory["messages"] if m["role"] == "assistant" and m.get("tool_calls")][1]["content"]
    assert note == trajectories.RECOVERY_NOTES["invented_observation"].format(bad=first["observation_id"])


@NEEDS_DATA
def test_wrong_element_recovery_returns_to_the_task_s_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = calls_of(built(tmp_path, monkeypatch, LINE_FIT, "wrong_element"))
    assert calls[0]["element_id"] == calls[0]["observation_id"] != 144
    assert all(c["element_id"] == 144 for c in calls[1:])


def test_absent_ids_differ_by_row_and_are_never_present() -> None:
    from powercoder.datasets.unsolvable import absent_id

    ids = [985, 1632, 1633, 1700, 2000]
    drawn = {absent_id(ids, f"row-{i}") for i in range(20)}
    assert not drawn & set(ids) and len(drawn) > 10
    assert absent_id([1, 2, 3], "x") > 3


@NEEDS_DATA
def test_an_unsolvable_fit_is_checked_once_then_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from powercoder.datasets.unsolvable import grade as grade_refusal, make_unsolvable

    row = make_unsolvable(LINE_FIT)
    bad = row["task"]["inputs"]["absent_id"]
    assert f"line {bad}" in row["task"]["prompt"] and "line 144" not in row["task"]["prompt"]
    trajectory = built(tmp_path, monkeypatch, row, None)
    (call,) = calls_of(trajectory)
    assert call["element_id"] == bad
    answer = trajectory["messages"][-1]["content"]
    assert grade_refusal(row, answer)[0]
    assert not grade_refusal(row, "0.950000")[0], "a value is not a refusal"


def test_recovery_variants_are_added_per_family() -> None:
    report = {"subfamily": "pf-report", "task": {"inputs": {"contingency_line": 3}}}
    assert [trajectories.fault_for(report, index) for index in (0, 2)] == ["run_power_flow_contingency"] * 2
    assert trajectories.extra_faults(report) == ["invented_observation"]
    assert trajectories.extra_faults({"subfamily": "pf-load-scale-fit"}) == ["wrong_element"]
