"""Adversarial verifier tests; these are not model benchmark results.

Positive traces come from actual PowerMCP fixture executions. Mutations are
stored as JSON and must not gain credit by copying stored reference statuses,
rounding fractional counts to integers, or inventing unobserved measurements.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pandapower as pp
import pytest

from powercoder.distillation import filter as verifier
from powercoder.distillation import teacher

FIXTURES: Path = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def records() -> dict:
    """Load real replay observations without any hidden benchmark answers."""
    return json.loads((FIXTURES / "expected_mcp_replays.json").read_text())


@pytest.fixture(scope="module")
def attacks() -> dict:
    """Load the versioned adversarial candidate definitions."""
    return json.loads((FIXTURES / "adversarial_filter_cases.json").read_text())


def mutate_trajectory(answer: dict, mutation: dict) -> dict:
    """Apply one fixture mutation and optionally make its forged hash self-consistent."""
    candidate = copy.deepcopy(answer)
    target = candidate
    for key in mutation["path"][:-1]:
        target = target[key]
    key = mutation["path"][-1]
    target[key] = target[key] + mutation["offset"] if "offset" in mutation else mutation["value"]
    if mutation.get("rehash"):
        final = candidate["trajectory"][-1]
        serialized = json.dumps(final["result"], sort_keys=True, allow_nan=False)
        final["evidence"]["result_hash"] = "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()
    return candidate


def test_real_replays_do_not_require_reference_answers(records: dict) -> None:
    """Actual success, non-convergence, and missing-case traces survive independent replay."""
    for case in records["replays"]:
        assert "expected" not in case["example"]
        accepted, evidence, diagnostics = verifier._check_mcp_trajectory(case["example"], case["answer"])
        assert accepted, (case["name"], diagnostics)
        assert evidence["candidate_status"] == evidence["independent_status"]
        assert evidence["candidate_execution_proven"] is False
        json.dumps(evidence, allow_nan=False)


def test_trajectory_claims_are_checked_against_execution(records: dict, attacks: dict) -> None:
    """Reject forged claims even when a candidate computes its own matching payload hash."""
    case = records["replays"][0]
    for mutation in attacks["trajectory_mutations"]:
        candidate = mutate_trajectory(case["answer"], mutation)
        accepted, evidence, diagnostics = verifier._check_mcp_trajectory(case["example"], candidate)
        assert accepted == mutation["pass"], (mutation["name"], diagnostics)
        json.dumps(evidence, allow_nan=False)


def test_stdout_cannot_hide_ambiguous_or_unverified_claims(attacks: dict) -> None:
    """Only the one-answer output contract is eligible for numerical checking."""
    for case in attacks["stdout_cases"]:
        assert (verifier.parse_answer(case["stdout"]) is not None) == case["pass"], case["name"]


def test_invalid_fit_candidates_never_reach_solver(attacks: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Out-of-range or non-finite candidates are invalid even if a solver could match the target."""
    loader = Mock(side_effect=AssertionError("Invalid candidate reached the backend"))
    monkeypatch.setattr(verifier, "_get_base_net", loader)
    for value in attacks["invalid_fit_values"]:
        accepted, _evidence, diagnostics = verifier._check_fit(attacks["fit_task"], {"scale_factor": value})
        assert not accepted
        assert diagnostics
    loader.assert_not_called()


def test_iteration_budget_is_enforced_and_invalid_budgets_fail(attacks: dict) -> None:
    """A one-step budget cannot produce a three-step SUCCESS; malformed budgets never run."""
    backend = verifier.pm.PowerMCP()
    spec = attacks["replay_specs"][1]
    result = backend.run_power_flow(spec["case"], max_iter=spec["max_iter"])
    assert result.status.value == spec["status"]
    for value in attacks["invalid_iteration_budgets"]:
        result = backend.run_power_flow(spec["case"], max_iter=value)
        assert result.status == verifier.pm.ExecutionStatus.EXECUTION_FAILURE
        assert not result.data
        assert "positive integer" in result.error


def test_report_counts_and_boolean_claims_are_not_coerced(records: dict, attacks: dict) -> None:
    """Fractional counts cannot pass by int truncation, and string booleans are not truth values."""
    example, answer = records["report_example"], records["report_answer"]
    accepted, _evidence, diagnostics = verifier._check_pf_report(example, answer)
    assert accepted, diagnostics
    for mutation in attacks["report_mutations"]:
        candidate = copy.deepcopy(answer)
        key = mutation["field"]
        candidate[key] = candidate[key] + mutation["offset"] if "offset" in mutation else mutation["value"]
        accepted, evidence, diagnostics = verifier._check_pf_report(example, candidate)
        assert not accepted, (mutation["name"], diagnostics)
        json.dumps(evidence, allow_nan=False)


def test_failed_solve_cannot_support_numeric_claims(
    records: dict, attacks: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated non-convergence checks the report failure branch; real failure replay is tested above."""
    def fail_solve(_net):
        """Stand in for a solver failing before any measurements are available."""
        raise pp.LoadflowNotConverged("simulated solver failure")

    monkeypatch.setattr(verifier.gen, "_solve", fail_solve)
    accepted, evidence, diagnostics = verifier._check_pf_report(records["report_example"], attacks["failed_report"])
    assert accepted, diagnostics
    json.dumps(evidence, allow_nan=False)
    accepted, _evidence, diagnostics = verifier._check_pf_report(
        records["report_example"], attacks["fabricated_failed_report"]
    )
    assert not accepted, diagnostics


def test_updated_scaffold_emits_actual_results_and_evidence(records: dict) -> None:
    """Run the trusted scaffold through the full execute/parse/replay filter without a teacher API."""
    for case in records["replays"]:
        inputs = case["example"]["task"]["inputs"]
        code = teacher.SCAFFOLDS["mcp-trajectory"]
        code = code.replace("@@CASE@@", inputs["case"])
        code = code.replace("@@MAX_ITER@@", repr(inputs["max_iter"]))
        code = code.replace("@@CHECK_LIMITS@@", repr(inputs["check_limits"]))
        result = verifier.filter_example(case["example"], code)
        assert result.is_accepted, (case["name"], result.diagnostics)
