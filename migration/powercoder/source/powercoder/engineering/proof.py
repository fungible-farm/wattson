"""The numerical proof contract every G2 engineering tool returns (PC-REQ-016).

A solver success flag alone is not proof (PRD-0010 §4.2): FM-17 (a status that is
constant whatever the trial) and FM-25 (a refusal where the reference solved) both came
from trusting one flag. The contract therefore carries the quantities a reviewer needs
to re-check a result -- independently recomputed equation residuals and constraint
violations, a feasibility verdict with four distinguishable values, and an optimality
claim that is ``None`` unless a bound actually exists.
"""

from __future__ import annotations

import math
from importlib import metadata
from typing import Literal, TypedDict

FeasibilityStatus = Literal["feasible", "infeasible", "indeterminate", "failed"]
"""Four distinguishable outcomes (backlog 0016 exit criteria).

- ``feasible``: a solution was found and independently re-checked within tolerance.
- ``infeasible``: a solver *proved* no solution exists (e.g. an LP infeasibility certificate).
- ``indeterminate``: no solution was found, but infeasibility was not proved
  (non-convergence of an iterative method is this, not ``infeasible``).
- ``failed``: the study could not be run (bad input, solver exception, unsupported model).
"""

FEASIBILITY_STATUSES: tuple[FeasibilityStatus, ...] = ("feasible", "infeasible", "indeterminate", "failed")


class ConstraintViolation(TypedDict):
    """One limit breached by a reported solution, recomputed from its raw values."""

    element_type: str  # pandapower table, e.g. "bus", "line", "trafo", "gen"
    element_id: int  # pandapower index in that table
    quantity: str  # e.g. "vm_pu", "p_mw", "loading_percent"
    value: float
    limit: float
    kind: str  # "above_max" or "below_min"


class SolverInfo(TypedDict):
    """Which numerical engine produced the result."""

    name: str
    version: str


class NumericalProof(TypedDict):
    """PC-REQ-016 numerical proof contract.

    ``equation_residuals`` maps a named equation set to the maximum absolute residual of
    the reported solution in it (units in the name, e.g. ``"nodal_p_balance_mw"``).
    ``optimality_gap_or_bound`` is ``None`` when no certificate or bound exists; AC OPF
    must never be called globally optimal without one (PC-REQ-011).
    """

    converged: bool
    equation_residuals: dict[str, float]
    constraint_violations: list[ConstraintViolation]
    feasibility_status: FeasibilityStatus
    optimality_gap_or_bound: float | None
    optimality_basis: str | None  # what the gap/bound is, e.g. "LP duality (HiGHS)"
    solver_iterations: int | None
    conditioning: dict[str, float] | None
    validation_warnings: list[str]
    solver: SolverInfo


def package_version(distribution: str) -> str:
    """Installed version of ``distribution``, or ``"unknown"`` if it has no metadata."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unknown"


def make_proof(
    *,
    converged: bool,
    feasibility_status: FeasibilityStatus,
    solver_name: str,
    solver_distribution: str,
    equation_residuals: dict[str, float] | None = None,
    constraint_violations: list[ConstraintViolation] | None = None,
    optimality_gap_or_bound: float | None = None,
    optimality_basis: str | None = None,
    solver_iterations: int | None = None,
    conditioning: dict[str, float] | None = None,
    validation_warnings: list[str] | None = None,
) -> NumericalProof:
    """Build a proof contract and enforce its internal consistency.

    Why: the contract's value is that its fields cannot contradict each other. A
    non-converged result reported ``feasible`` or a non-finite residual would be exactly
    the "solver flag as proof" failure PC-REQ-016 forbids, so those raise here instead of
    being returned.

    Raises:
        ValueError: on an unknown status, a ``feasible`` verdict without convergence,
            a non-finite residual, or a gap without a stated basis.
    """
    residuals = dict(equation_residuals or {})
    if feasibility_status not in FEASIBILITY_STATUSES:
        raise ValueError(f"unknown feasibility_status {feasibility_status!r}")
    if feasibility_status == "feasible" and not converged:
        raise ValueError("a non-converged result cannot be reported feasible")
    if converged and any(not math.isfinite(value) for value in residuals.values()):
        raise ValueError("a converged result must have finite equation residuals")
    if optimality_gap_or_bound is not None and not optimality_basis:
        raise ValueError("an optimality gap or bound must name its basis")
    return NumericalProof(
        converged=converged,
        equation_residuals=residuals,
        constraint_violations=list(constraint_violations or []),
        feasibility_status=feasibility_status,
        optimality_gap_or_bound=optimality_gap_or_bound,
        optimality_basis=optimality_basis,
        solver_iterations=solver_iterations,
        conditioning=conditioning,
        validation_warnings=list(validation_warnings or []),
        solver=SolverInfo(name=solver_name, version=package_version(solver_distribution)),
    )


def is_solved(proof: NumericalProof, residual_tolerance: float) -> bool:
    """True only when the proof supports calling the result a solution.

    Requires convergence, a ``feasible`` verdict, at least one residual, and every
    residual within ``residual_tolerance``. Constraint violations do not make a result
    unsolved (a solved power flow can violate limits, FM-17); they are reported, not hidden.
    """
    return (
        proof["converged"]
        and proof["feasibility_status"] == "feasible"
        and bool(proof["equation_residuals"])
        and all(value <= residual_tolerance for value in proof["equation_residuals"].values())
    )
