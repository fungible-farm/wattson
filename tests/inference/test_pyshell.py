"""The Python shell runs model-written code, records its solver trials, and the grader reads them (backlog 0041)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from wattson.inference import pyshell
from wattson.inference.evaluate_agent import relevant_evidence
from wattson.inference.provenance import tried_scales

CASE: str = "snem-1803"
TARGET_SCALE: float = 1.0375
FIT_TASK: dict[str, Any] = {"subfamily": "pf-load-scale-fit",
                            "task": {"inputs": {"case": CASE, "element": "bus 405"}}}


def stub(arguments: dict[str, Any]) -> dict[str, Any]:
    """A solver stand-in: metric rises with scale; the reply has the shape run_study_trial returns."""
    scale = arguments["scale"]
    study = {**arguments, "network_id": arguments["network_id"], "contingency_line": None}
    return {"status": "success", "error": None, "evidence": {"big": "block"},
            "data": {"study": study, "converged": True, "metric": 1.0 + scale / 10, "metric_valid": True,
                     "metric_unit": "pu", "iterations": 4}}


BISECT: str = """
import solver
lo, hi = 0.9, 1.1
for _ in range(5):
    mid = (lo + hi) / 2
    reply = solver.trial(network_id="snem-1803", operation="load_scale", scale=mid, observation="bus_voltage",
                         observation_id=405)
    if reply["data"]["metric"] > 1.1:
        hi = mid
    else:
        lo = mid
print(mid)
"""


def journal_of(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    """A one-reply journal holding what run_python returned to the model."""
    text = json.dumps({"status": outcome["status"], "data": {"stdout": outcome["stdout"], "trials": outcome["trials"]},
                       "error": outcome["error"]})
    return [{"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": text}]}}}]


def test_code_does_the_arithmetic_and_every_trial_is_recorded() -> None:
    outcome = pyshell.run_python(BISECT, stub)
    assert outcome["status"] == "ok" and outcome["error"] is None
    assert len(outcome["trials"]) == 5
    assert outcome["trials"][0]["arguments"]["scale"] == pytest.approx(1.0)
    assert float(outcome["stdout"]) == pytest.approx(outcome["trials"][-1]["arguments"]["scale"])
    assert "evidence" not in outcome["trials"][0]["response"], "the evidence block is left out of the result"
    assert outcome["trials"][0]["arguments"]["max_iter"] == pyshell.DEFAULT_MAX_ITER


def test_imports_outside_the_allowlist_are_refused() -> None:
    for source in ("import os", "import subprocess", "from pathlib import Path", "__import__('socket')"):
        outcome = pyshell.run_python(source, stub)
        assert outcome["status"] == "error" and "not allowed" in outcome["error"], source
    assert pyshell.run_python("import math\nprint(math.sqrt(16))", stub)["stdout"] == "4.0\n"


def test_files_and_dynamic_code_are_unavailable() -> None:
    for source in ("open('/etc/passwd')", "eval('1+1')", "exec('x=1')"):
        outcome = pyshell.run_python(source, stub)
        assert outcome["status"] == "error" and "NameError" in outcome["error"], source


def test_a_runaway_loop_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pyshell, "WALL_TIMEOUT_S", 2.0)
    outcome = pyshell.run_python("while True:\n    pass", stub)
    assert outcome["status"] == "timeout" and "TimeoutError" in outcome["error"]


def test_the_trial_limit_stops_a_search_that_never_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pyshell, "MAX_TRIALS_PER_RUN", 3)
    source = "import solver\nwhile True:\n    solver.trial(network_id='snem-1803', operation='load_scale', scale=1.0)"
    outcome = pyshell.run_python(source, stub)
    assert len(outcome["trials"]) == 3 and outcome["status"] == "error" and "SolverError" in outcome["error"]


def test_a_solver_failure_reaches_the_code_as_an_exception() -> None:
    def refuse(_arguments: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("no such element")

    source = ("import solver\ntry:\n    solver.trial(network_id='snem-1803', operation='load_scale', scale=1.0)\n"
              "except solver.SolverError as error:\n    print('caught', error)")
    outcome = pyshell.run_python(source, refuse)
    assert outcome["status"] == "ok" and "caught ValueError: no such element" in outcome["stdout"]
    assert outcome["trials"][0]["response"]["status"] == "execution_failure"


def test_the_grader_counts_trials_run_in_code_as_tried_and_as_evidence() -> None:
    outcome = pyshell.run_python(BISECT, stub)
    events = journal_of(outcome)
    scales = tried_scales(events)
    assert len(scales) == 5 and scales[-1] == pytest.approx(float(outcome["stdout"]))
    answer = f"{float(outcome['stdout']):.6f}"
    task = {**FIT_TASK, "task": {"inputs": {**FIT_TASK["task"]["inputs"], "element": "bus 405"}}}
    assert relevant_evidence(task, answer, events)


def test_a_scale_the_code_only_printed_was_not_tried() -> None:
    """Printing a value is not measuring it: the answer must be a scale a trial actually ran."""
    outcome = pyshell.run_python("import solver\nprint(1.0375)", stub)
    assert outcome["trials"] == []
    events = journal_of(outcome)
    assert tried_scales(events) == []
    assert not relevant_evidence(FIT_TASK, "1.037500", events)
