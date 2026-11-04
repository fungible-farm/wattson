"""Numerical scoring must survive presentation changes and reject false-safe text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powercoder.inference.domain_scorecard import parse_report, report_matches, score_numerical, solve_report

FIXTURE: Path = Path(__file__).parents[2] / "training/unsloth/fixtures/evaluation_cases.json"
# Existing committed evaluation fixture's measured report fields, reused only
# for parser tests; this fixture does not claim benchmark performance.
EXPECTED: dict = {"outcome": "converged", "iterations": 5, "min_v": 0.907,
                  "max_v": 1.0941, "max_load": 2.66, "violations": 40}
STRUCTURED: dict = {"converged": True, "iterations": 5, "min_bus_voltage_pu": 0.907,
                    "max_bus_voltage_pu": 1.0941, "max_line_loading_percent": 2.66,
                    "voltage_violations": 40}
MARKDOWN: str = """**Convergence Status:** Converged (with constraint violations)
**Iterations:** 5
**Bus Voltage (pu):**
- Minimum: 0.9070 pu
- Maximum: 1.0941 pu
**Line Loading:**
- Maximum: 2.66%
**Voltage Violations:** 40 buses
"""


def test_plain_markdown_and_json_have_identical_numerical_score() -> None:
    """The teacher's prose style must not create a spurious student advantage."""
    plain = json.loads(FIXTURE.read_text())["comparisons"][-1]["reference"]
    document = json.dumps(STRUCTURED)
    for answer in (plain, MARKDOWN, document, "```json\n" + document + "\n```"):
        assert report_matches(answer, EXPECTED)
        assert parse_report(answer) == EXPECTED


@pytest.mark.parametrize("failure", ["Power flow did not converge.", "Converged: false.",
                                     "Convergence status: failed.", "Power flow failed to execute."])
def test_false_safe_and_contradictory_reports_rejected(failure: str) -> None:
    """Correct copied numbers cannot override an explicit failure declaration."""
    assert not report_matches(failure + "\n" + MARKDOWN, EXPECTED)


def test_json_failure_has_precedence_over_converged_flag() -> None:
    """Conflicting machine-readable status fields must fail closed too."""
    for status in ("non_convergence", "execution_failure"):
        assert not report_matches(json.dumps({**STRUCTURED, "status": status}), EXPECTED)


def test_missing_wrong_nonfinite_and_boolean_fields_rejected() -> None:
    """All fields require finite numerical values within the documented bounds."""
    for change in ({"iterations": True}, {"min_bus_voltage_pu": float("nan")},
                   {"voltage_violations": 39}, {"max_line_loading_percent": 20.0}):
        assert not report_matches(json.dumps({**STRUCTURED, **change}), EXPECTED)
    missing = dict(STRUCTURED)
    del missing["max_bus_voltage_pu"]
    assert not report_matches(json.dumps(missing), EXPECTED)


def test_report_grader_executes_actual_pandapower() -> None:
    """Re-solve a named tiny solver fixture, never a synthetic NEM result."""
    task = {"subfamily": "pf-report", "task": {"inputs": {"case": "fixture-2bus-ok", "contingency_line": None}}}
    expected, evidence = solve_report(task)
    assert expected["outcome"] == "converged"
    assert evidence["solver"] == "pandapower"
    answer = {"converged": True, **{key: expected[value] for key, value in (
        ("iterations", "iterations"), ("min_bus_voltage_pu", "min_v"), ("max_bus_voltage_pu", "max_v"),
        ("max_line_loading_percent", "max_load"), ("voltage_violations", "violations"))}}
    passed, verification, diagnostics = score_numerical(task, json.dumps(answer))
    assert passed and not diagnostics
    assert verification["solver_fields"] == expected
