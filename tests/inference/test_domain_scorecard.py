"""Numerical scoring must survive presentation changes and reject false-safe text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattson.inference.domain_scorecard import parse_report, report_matches, score_numerical, solve_report

FIXTURE: Path = Path(__file__).parents[2] / "fixtures/evaluation_cases.json"
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


# --- v4: a report is judged on its values, not on the phrases that label them -----------------------------------

EXPECTED_REPORT = {"outcome": "converged", "iterations": 5.0, "min_v": 0.9070123, "max_v": 1.0941456, "max_load": 2.6603, "violations": 39.0}


def test_v4_accepts_correct_values_whatever_the_wording() -> None:
    """The plain control's report that v2 scored as missing every field (it says "converges" and "voltage-violation count")."""
    from wattson.inference.domain_scorecard import report_matches, report_matches_normalised

    answer = ("With line 141 out of service on 'snem-sa-2000', the power flow converges in 5 iterations. Min bus voltage = "
              "0.9070 pu, max bus voltage = 1.0941 pu, max line loading = 2.66%, voltage-violation count = 39.")
    assert not report_matches(answer, EXPECTED_REPORT), "v2 reads values only after its fixed labels"
    assert report_matches_normalised(answer, EXPECTED_REPORT, "snem-sa-2000")


def test_v4_still_rejects_wrong_values_and_too_much_rounding() -> None:
    from wattson.inference.domain_scorecard import report_matches_normalised

    base = "Converged in 5 iterations, min bus voltage 0.9070 pu, max bus voltage 1.0941 pu, max line loading 2.66%, 39 violations."
    assert report_matches_normalised(base, EXPECTED_REPORT)
    assert not report_matches_normalised(base.replace("0.9070", "0.90"), EXPECTED_REPORT), "0.90 is 7e-3 from 0.9070"
    assert not report_matches_normalised(base.replace("39", "40"), EXPECTED_REPORT)
    assert not report_matches_normalised(base.replace("5 iterations", "6 iterations"), EXPECTED_REPORT)
    assert not report_matches_normalised("The power flow did not converge.", EXPECTED_REPORT)


def test_v4_normalises_float_representation_error() -> None:
    from wattson.inference.domain_scorecard import normalise_number, report_matches_normalised

    assert normalise_number("0.90") == normalise_number("0.9000") == normalise_number("0.9000000000001")
    assert normalise_number("1,039") == normalise_number("1039.0")
    answer = "5 iterations; 0.9070000000001; 1.09414999999; 2.6603000001; 39"
    assert report_matches_normalised(answer, EXPECTED_REPORT)


def test_v4_needs_a_distinct_number_for_each_value_and_ignores_identifiers() -> None:
    from wattson.inference.domain_scorecard import answer_numbers, report_matches_normalised

    expected = {**EXPECTED_REPORT, "iterations": 5.0, "violations": 5.0}
    one_five = "converged in 5 iterations; min 0.9070; max 1.0941; loading 2.66"
    assert not report_matches_normalised(one_five, expected), "one written 5 cannot be both the iterations and the violations"
    assert report_matches_normalised(one_five + "; 5 violations", expected)
    assert [str(n) for n in answer_numbers("On snem-1803 with line 5 out of service, 7 iterations", "snem-1803")] == ["7.0000"]


def test_v4_treats_0_8992_and_0_9000_as_the_same_voltage() -> None:
    """User decision 2026-10-30: for comparison purposes a voltage within 1e-3 pu is the same voltage."""
    from wattson.inference.domain_scorecard import report_matches_normalised

    expected = {"outcome": "converged", "iterations": 5.0, "min_v": 0.8992493, "max_v": 1.1196930, "max_load": 97.8421, "violations": 234.0}
    for answer in ("5 iterations, min 0.8992, max 1.1197, loading 97.84, 234 violations.",
                   "5 iterations, min 0.9000, max 1.1200, loading 97.84, 234 violations.",
                   "5 iterations, min 0.90, max 1.12, loading 97.8, 234 violations."):
        assert report_matches_normalised(answer, expected), answer
    assert not report_matches_normalised("5 iterations, min 0.91, max 1.12, loading 97.84, 234 violations.", expected)
