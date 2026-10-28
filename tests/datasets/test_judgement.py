"""The judgement suite (0008): its graders re-solve, and its prompts remove what the template relies on."""

from __future__ import annotations

import json

import pytest

from wattson.datasets import judgement as jg
from wattson.inference import powermcp as pm

HAS_CASES: bool = all((pm.REPO_ROOT / pm.CASE_FILES[c]).exists() for c in (jg.CASE, jg.HELD_OUT_CASE))
needs_cases = pytest.mark.skipif(not HAS_CASES, reason="needs data/ (scripts/fetch_csiro_nem_data.py)")


@needs_cases
def test_worst_contingency_grading_re_solves_and_names_the_worst_line() -> None:
    task = jg.worst_contingency_task(0)
    assert task is not None
    worst = task["expected"]["structured"]["worst_line"]
    others = [c for c in task["task"]["inputs"]["candidate_lines"] if c != worst]
    assert jg.grade_worst_contingency(task, task["expected"]["answer"])[0]
    assert not jg.grade_worst_contingency(task, task["expected"]["answer"].replace(f"Line {worst},", f"Line {others[0]},"))[0]
    loadings = sorted(task["expected"]["structured"]["loadings"].values(), reverse=True)
    assert loadings[0] - loadings[1] >= jg.RANKING_MARGIN_PERCENT


def test_tool_evidence_needs_a_converged_study_of_every_candidate() -> None:
    task = {"task": {"inputs": {"case": "c", "candidate_lines": [1, 2]}}}
    response = lambda line: {"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": json.dumps(
        {"data": {"converged": True, "study": {"network_id": "c", "operation": "none", "contingency_line": line}}})}]}}}
    assert jg.studied_every_candidate(task, [response(1), response(2)])
    assert not jg.studied_every_candidate(task, [response(1)])


@needs_cases
def test_open_fits_state_no_interval_and_held_out_reports_solve() -> None:
    from wattson.datasets import generate

    lists = json.loads((generate.REPO_ROOT / "src/wattson/datasets/element_lists.json").read_text())
    fit = jg.open_fit_task("pf-load-scale-fit", 0, lists["load_scale_buses"], generate.EndpointCache())
    assert "[" not in fit["task"]["prompt"] and fit["task"]["inputs"]["scale_lo"] < fit["task"]["inputs"]["scale_hi"]
    held = jg.held_out_task(0)
    assert held["topology"] == jg.HELD_OUT_CASE and held["lifecycle"]["status"] == "verified"
