"""Stored trajectories must render exactly the prompt the evaluation serves."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from wattson.datasets import trajectories
from wattson.datasets.trajectories import write_in_order
from wattson.inference import powermcp as pm
from wattson.inference.mcp_server import tool_definitions


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
    from wattson.datasets.unsolvable import element_ids

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
    from wattson.datasets.unsolvable import absent_id

    ids = [985, 1632, 1633, 1700, 2000]
    drawn = {absent_id(ids, f"row-{i}") for i in range(20)}
    assert not drawn & set(ids) and len(drawn) > 10
    assert absent_id([1, 2, 3], "x") > 3


@NEEDS_DATA
def test_an_unsolvable_fit_is_checked_once_then_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wattson.datasets.unsolvable import grade as grade_refusal, make_unsolvable

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


def python_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: dict, fault: str | None, thinking: bool = False,
                 v2: bool = False) -> dict:
    """A python-mode trajectory, built and graded by the real harness."""
    journals = tmp_path / f"journals-{row['example_id']}-{fault}-{thinking}"
    journals.mkdir()
    trajectory, reason = asyncio.run(trajectories.build(row, journals, fault, False, True, thinking, v2))
    assert reason == "kept", reason
    return trajectory


@NEEDS_DATA
def test_a_python_fit_is_one_program_and_answers_a_scale_the_program_really_ran(tmp_path: Path,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """The model sends no number to a tool: one run_python call, then the printed scale, graded by the real harness."""
    trajectory = python_built(tmp_path, monkeypatch, LINE_FIT, None)
    calls = [m["tool_calls"][0]["function"] for m in trajectory["messages"] if m.get("tool_calls")]
    assert [c["name"] for c in calls] == ["run_python"]
    code = json.loads(calls[0]["arguments"])["code"]
    assert "(low + high) / 2" in code, "the midpoint is computed by the program"
    tool = json.loads(json.loads(next(m for m in trajectory["messages"] if m["role"] == "tool")["content"])["content"][0]["text"])
    answer = trajectory["messages"][-1]["content"]
    assert f"scale={answer} " in tool["data"]["stdout"]
    assert any(trial["arguments"]["scale"] == float(answer) for trial in tool["data"]["trials"])
    assert all("reasoning_content" not in m for m in trajectory["messages"]), "thinking is off unless asked for"


@NEEDS_DATA
def test_scripted_thinking_reaches_every_assistant_turn_and_holds_no_computed_number(tmp_path: Path,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    trajectory = python_built(tmp_path, monkeypatch, LINE_FIT, None, thinking=True)
    assistant = [m for m in trajectory["messages"] if m["role"] == "assistant"]
    assert len(assistant) == 2 and all(m.get("reasoning_content") for m in assistant)
    printed = trajectory["messages"][-1]["content"]
    numbers = set(re.findall(r"\d+\.\d+", " ".join(m["reasoning_content"] for m in assistant)))
    prompt = trajectory["messages"][1]["content"] + json.dumps(LINE_FIT["task"]["inputs"])
    tool = json.loads(json.loads(next(m for m in trajectory["messages"] if m["role"] == "tool")["content"])["content"][0]["text"])
    printed_numbers = set(re.findall(r"\d+\.\d+", tool["data"]["stdout"])) | {printed}
    assert all(n in prompt or n in printed_numbers for n in numbers), numbers


@NEEDS_DATA
def test_a_python_refusal_needs_the_solver_to_have_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wattson.datasets.unsolvable import checked_absent_element, make_unsolvable

    row = make_unsolvable(LINE_FIT)
    trajectory = python_built(tmp_path, monkeypatch, row, None)
    first = next(m for m in trajectory["messages"] if m.get("tool_calls"))["tool_calls"][0]["function"]
    plain = python_built(tmp_path, monkeypatch, LINE_FIT, None)
    plain_first = next(m for m in plain["messages"] if m.get("tool_calls"))["tool_calls"][0]["function"]
    assert first["name"] == plain_first["name"] == "run_python"
    assert "(low + high) / 2" in first["arguments"], "an unsolvable task starts with the ordinary search program"
    tool = next(m for m in trajectory["messages"] if m["role"] == "tool")
    events = [{"kind": "tool_response", "payload": {"result": {"content": [{"type": "text",
              "text": json.loads(tool["content"])["content"][0]["text"]}]}, "tool_call_id": "x"}}]
    assert checked_absent_element(row, events)
    claimed = {"status": "ok", "error": None, "data": {"stdout": "Unknown observation element: absent", "trials": []}}
    events[0]["payload"]["result"]["content"][0]["text"] = json.dumps(claimed)
    assert not checked_absent_element(row, events), "a program that only prints the refusal has not asked the solver"


def test_a_scripted_thought_may_only_repeat_numbers_the_task_or_a_tool_said() -> None:
    """Three students were refused because their scripted reasoning said a number or phrase the prompt did not (0041)."""
    prompt = "Find s so the voltage at bus 405 equals 1.034339 (within 0.0015)."
    reply = json.dumps({"content": [{"type": "text", "text": json.dumps({"data": {"stdout": "scale=1.0625 residual=-0.0003\\n"}})}]})
    good = [{"role": "system", "content": "s"}, {"role": "user", "content": prompt},
            {"role": "assistant", "content": None, "reasoning_content": "Target 1.034339 within 0.0015."},
            {"role": "tool", "content": reply},
            {"role": "assistant", "content": "1.0625", "reasoning_content": "It printed scale 1.0625 with residual -0.0003."}]
    trajectories.check_thoughts(good)
    bad = [*good[:4], {"role": "assistant", "content": "1.0625", "reasoning_content": "The residual is 0.0019."}]
    with pytest.raises(RuntimeError, match="0.0019"):
        trajectories.check_thoughts(bad)
    wrong_element = [*good[:2], {"role": "assistant", "content": None, "reasoning_content": "At line 56 the value is 1.034339."},
                     *good[3:]]
    with pytest.raises(RuntimeError, match="56"):
        trajectories.check_thoughts(wrong_element)


def tool_data(trajectory: dict, index: int = 0) -> dict:
    """The decoded ``data`` of the index-th tool reply of a trajectory."""
    message = [m for m in trajectory["messages"] if m["role"] == "tool"][index]
    return json.loads(json.loads(message["content"])["content"][0]["text"])["data"]


def program_of(trajectory: dict, index: int = 0) -> str:
    calls = [m["tool_calls"][0]["function"] for m in trajectory["messages"] if m.get("tool_calls")]
    return json.loads(calls[index]["arguments"]).get("code", "")


@NEEDS_DATA
def test_v2_fit_checks_the_named_elements_exist_before_it_searches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trajectory = python_built(tmp_path, monkeypatch, LINE_FIT, None, thinking=True, v2=True)
    code = program_of(trajectory)
    assert "solver.exists(CASE, kind, element)" in code and "NAMED = [('line', 144)]" in code
    assert "scale=scale" in code
    assert "checks the named elements exist" in next(m for m in trajectory["messages"] if m["role"] == "assistant")["reasoning_content"]
    assert tool_data(trajectory)["checks"][0]["exists"] is True


@NEEDS_DATA
def test_v2_refusal_is_a_printed_fact_from_the_existence_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wattson.datasets.unsolvable import make_unsolvable

    row = make_unsolvable(LINE_FIT)
    bad = row["task"]["inputs"]["absent_id"]
    trajectory = python_built(tmp_path, monkeypatch, row, None, thinking=True, v2=True)
    data = tool_data(trajectory)
    assert data["stdout"].startswith(f"ABSENT line {bad} in case"), data["stdout"]
    assert data["trials"] == [] and data["checks"][0]["exists"] is False, "no search ran: the check ended it"
    assert "(low + high) / 2" in program_of(trajectory), "the first turn is the ordinary program"
    assert f"ABSENT line {bad}" in [m for m in trajectory["messages"] if m["role"] == "assistant"][-1]["reasoning_content"]


@NEEDS_DATA
def test_v2_recovers_from_a_call_without_scale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trajectory = python_built(tmp_path, monkeypatch, LINE_FIT, "missing_scale", thinking=True, v2=True)
    assert "scale=scale" not in program_of(trajectory, 0) and "scale=scale" in program_of(trajectory, 1)
    assistant = [m for m in trajectory["messages"] if m["role"] == "assistant"]
    assert "The solver refused the call" in assistant[1]["reasoning_content"]


@NEEDS_DATA
def test_v2_report_answers_the_tools_own_sentence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trajectories, "grade", lambda _row, _journal: {"passed": True})  # the fixture row has no reference
    trajectory = python_built(tmp_path, monkeypatch, REPORT, None, thinking=True, v2=True)
    reply = tool_data(trajectory)
    assert trajectory["messages"][-1]["content"] == reply["report_text"], "copied, not rounded"
