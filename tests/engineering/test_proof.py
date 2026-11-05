"""The proof contract cannot be made to contradict itself (PC-REQ-016)."""

from __future__ import annotations

import math

import pytest

from wattson.engineering.proof import is_solved, make_proof


def _proof(**overrides: object):
    """A valid proof with ``overrides`` applied."""
    arguments: dict = {"converged": True, "feasibility_status": "feasible", "solver_name": "test",
                       "solver_distribution": "pandapower", "equation_residuals": {"r_mw": 1e-9}}
    return make_proof(**(arguments | overrides))


def test_a_non_converged_result_cannot_be_feasible() -> None:
    """Non-convergence is never reported as solved."""
    with pytest.raises(ValueError):
        _proof(converged=False)


def test_unknown_status_non_finite_residual_and_unbased_gap_are_rejected() -> None:
    """Every field that could carry a false claim is checked."""
    with pytest.raises(ValueError):
        _proof(feasibility_status="optimal")
    with pytest.raises(ValueError):
        _proof(equation_residuals={"r_mw": math.nan})
    with pytest.raises(ValueError):
        _proof(optimality_gap_or_bound=0.0)


def test_is_solved_needs_convergence_feasibility_and_small_residuals() -> None:
    """A success flag alone is not enough; residuals must exist and be small."""
    assert is_solved(_proof(), 1e-6)
    assert not is_solved(_proof(equation_residuals={"r_mw": 1.0}), 1e-6)
    assert not is_solved(_proof(equation_residuals={}), 1e-6)
    assert not is_solved(_proof(converged=False, feasibility_status="indeterminate"), 1e-6)
    assert _proof()["solver"]["version"] != "unknown"
