"""AC OPF under the proof contract: feasibility re-verified independently, optimality never claimed (PC-REQ-011).

pandapower's ``runopp`` (PIPS interior point) returns a local optimum of a non-convex
problem. There is no global certificate here, so ``optimality_gap_or_bound`` stays None and
the result says so (PC-REQ-011: AC OPF must never be called globally optimal without one).
What *is* checked independently is feasibility: the OPF's setpoints (generator P, generator and
slack voltages) are applied to a fresh copy and re-solved as an ordinary AC power flow.
The re-solve must reproduce the OPF's voltages, satisfy Kirchhoff's laws, and keep every
limit **the case itself declares** for its OPF (bus ``min/max_vm_pu``, branch
``max_loading_percent``, generator P and Q limits). Those are recomputed from the
re-solve's result tables. A limit within tolerance of its bound is reported as binding.
"""

from __future__ import annotations

import copy
import math
from typing import TypedDict

import numpy as np
import pandapower as pp

from wattson.engineering.ac_analysis import KCL_RESIDUAL_TOLERANCE_MVA
from wattson.engineering.contingency import kcl_residual_mva
from wattson.engineering.proof import ConstraintViolation, NumericalProof, make_proof
from wattson.inference import powermcp as pm

# The re-solve must reproduce the OPF's bus voltages to this (pu): both solve the same
# equations to pandapower's 1e-8 pu mismatch tolerance, so 1e-6 pu is a generous margin.
VOLTAGE_MATCH_TOLERANCE_PU: float = 1e-6
# Limit tolerance for the interior-point solution (PIPS stops at ~1e-6 relative); a value
# within this of its bound is binding, beyond it a violation. Units follow each quantity.
LIMIT_TOLERANCE: dict[str, float] = {"vm_pu": 1e-4, "loading_percent": 1e-2, "p_mw": 1e-3, "q_mvar": 1e-3}
MAX_ITERATIONS: int = 100


class Limit(TypedDict):
    """One limit at or beyond its bound in the re-solved OPF operating point."""

    element_type: str
    element_id: int
    quantity: str
    value: float
    bound: float
    side: str  # "max" or "min"


class AcOpfResult(TypedDict):
    """AC OPF: objective, binding limits, the feasibility re-check and its proof."""

    solved: bool
    objective: float | None
    binding: list[Limit]
    max_voltage_mismatch_pu: float | None
    proof: NumericalProof


def _limits(net: pp.pandapowerNet) -> tuple[list[Limit], list[ConstraintViolation]]:
    """Every case-declared OPF limit that binds or is violated, from ``net``'s result tables."""
    binding: list[Limit] = []
    violations: list[ConstraintViolation] = []

    def check(element_type: str, element: int, quantity: str, value: float, low: float | None, high: float | None) -> None:
        tol = LIMIT_TOLERANCE[quantity]
        for bound, side in ((high, "max"), (low, "min")):
            if bound is None or not math.isfinite(bound) or not math.isfinite(value):
                continue
            beyond = value - bound if side == "max" else bound - value
            if beyond > tol:
                violations.append(ConstraintViolation(element_type=element_type, element_id=element, quantity=quantity,
                                                      value=value, limit=bound, kind="above_max" if side == "max" else "below_min"))
            elif beyond > -tol:
                binding.append(Limit(element_type=element_type, element_id=element, quantity=quantity, value=value,
                                     bound=bound, side=side))

    for bus in net.bus.index[net.bus.in_service]:
        check("bus", int(bus), "vm_pu", float(net.res_bus.vm_pu[bus]),
              float(net.bus.min_vm_pu[bus]) if "min_vm_pu" in net.bus else None,
              float(net.bus.max_vm_pu[bus]) if "max_vm_pu" in net.bus else None)
    for table in ("line", "trafo"):
        if len(net[table]) and "max_loading_percent" in net[table]:
            for idx in net[table].index[net[table].in_service]:
                check(table, int(idx), "loading_percent", float(net[f"res_{table}"].loading_percent[idx]), None,
                      float(net[table].max_loading_percent[idx]))
    for idx in net.gen.index[net.gen.in_service]:
        row = net.gen.loc[idx]
        check("gen", int(idx), "p_mw", float(net.res_gen.p_mw[idx]), row.get("min_p_mw"), row.get("max_p_mw"))
        check("gen", int(idx), "q_mvar", float(net.res_gen.q_mvar[idx]), row.get("min_q_mvar"), row.get("max_q_mvar"))
    return binding, violations


def run_ac_opf(net: pp.pandapowerNet) -> AcOpfResult:
    """Solve the AC OPF of a copy of ``net`` and re-verify its feasibility by an AC power flow."""
    work = copy.deepcopy(net)
    try:
        pp.runopp(work, init="pf", calculate_voltage_angles=True)
        converged = bool(work.OPF_converged)
    except Exception as error:  # noqa: BLE001 -- pandapower raises OPFNotConverged and others
        converged, failure = False, f"{type(error).__name__}: {error}"
    if not converged:
        return AcOpfResult(solved=False, objective=None, binding=[], max_voltage_mismatch_pu=None,
                           proof=make_proof(converged=False, feasibility_status="indeterminate",
                                            solver_name="pandapower.runopp (PIPS)", solver_distribution="pandapower",
                                            validation_warnings=[locals().get("failure", "OPF did not converge"),
                                                                 "no feasibility verdict: PIPS gives no infeasibility certificate"]))
    check = copy.deepcopy(net)
    check.gen["p_mw"] = work.res_gen.p_mw.reindex(check.gen.index).to_numpy()
    check.gen["vm_pu"] = work.res_bus.vm_pu.reindex(check.gen.bus).to_numpy()
    check.ext_grid["vm_pu"] = work.res_bus.vm_pu.reindex(check.ext_grid.bus).to_numpy()
    check.ext_grid["va_degree"] = work.res_bus.va_degree.reindex(check.ext_grid.bus).to_numpy()
    pm._runpp_counted(check, numba=True, max_iteration=MAX_ITERATIONS, calculate_voltage_angles=True)
    mismatch = float(np.nanmax(np.abs(check.res_bus.vm_pu.to_numpy() - work.res_bus.vm_pu.reindex(check.res_bus.index).to_numpy())))
    residual = kcl_residual_mva(check)
    binding, violations = _limits(check)
    reproduced = mismatch <= VOLTAGE_MATCH_TOLERANCE_PU and residual <= KCL_RESIDUAL_TOLERANCE_MVA
    status = "feasible" if reproduced and not violations else "infeasible" if reproduced else "indeterminate"
    warnings = ["local optimum from an interior-point solver: no global optimality certificate (PC-REQ-011)"]
    if not reproduced:
        warnings.append(f"the re-solve did not reproduce the OPF (|dV| {mismatch:.3g} pu, residual {residual:.3g} MVA)")
    proof = make_proof(converged=True, feasibility_status=status, solver_name="pandapower.runopp (PIPS)",
                       solver_distribution="pandapower",
                       equation_residuals={"kcl_residual_mva": residual, "voltage_mismatch_vs_resolve_pu": mismatch},
                       constraint_violations=violations, validation_warnings=warnings)
    return AcOpfResult(solved=status == "feasible", objective=float(work.res_cost), binding=binding,
                       max_voltage_mismatch_pu=mismatch, proof=proof)
