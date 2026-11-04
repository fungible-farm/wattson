"""AC power flow under the proof contract, and V/Q sensitivities with conditioning (G2: PC-REQ-010, -013).

- :func:`ac_power_flow` solves (pandapower Newton-Raphson, via ``powermcp._runpp_counted``)
  and returns the proof contract: iterations, the Kirchhoff residual recomputed
  independently from the result tables (``contingency.kcl_residual_mva``), every limit
  violation recomputed from the raw values, and the condition number of the solved Jacobian.
- :func:`vq_sensitivity` gives dV/dQ at PQ buses from the reduced Jacobian
  J_R = J_QV - J_Qtheta J_Ptheta^-1 J_PV, cross-checked against an independent central finite
  difference: re-solving with the bus's reactive load moved by +/- ``FD_STEP_MVAR``.
- :func:`qv_modal` returns J_R's smallest eigenvalue (positive: voltage-stable at this
  operating point; near zero: close to a Q-V stability limit) and the buses that
  participate most in that mode. The criterion assumes inductive branches: a negative mode
  at the ends of a negative-reactance (series-compensated) branch is flagged, not read as
  instability.

The Jacobian is pandapower's own, read from its solver's internal case after the solve. Its
order is [theta at pv+pq; V at pq] by [P at pv+pq; Q at pq]. Bus mapping assumes that internal
case keeps pandapower's bus order, so networks whose internal case renumbers buses
(out-of-service or fused buses) raise :class:`UnsupportedModel` rather than mislabel results.
"""

from __future__ import annotations

import copy
import math
from typing import TypedDict

import numpy as np
import pandapower as pp
import scipy.sparse as sp

from powercoder.engineering.contingency import _ac_violations, kcl_residual_mva
from powercoder.engineering.dc_model import UnsupportedModel
from powercoder.engineering.proof import NumericalProof, make_proof
from powercoder.inference import powermcp as pm

MAX_ITERATIONS: int = 100
# Kirchhoff residual a solved AC power flow must meet (MVA); pandapower's own convergence
# tolerance is 1e-8 pu of mismatch, so 1e-6 MVA leaves room for float noise in result tables.
KCL_RESIDUAL_TOLERANCE_MVA: float = 1e-6
# Finite-difference step for the dV/dQ cross-check: small against snemSA's loads (tens of
# MW/Mvar) so the linearisation error, O(step^2) for a central difference, stays far below
# the agreement tolerance.
FD_STEP_MVAR: float = 0.1
# Relative agreement required between the Jacobian dV/dQ and the finite difference.
FD_REL_TOLERANCE: float = 1e-3
# Buses reported as participating in the critical Q-V mode.
PARTICIPATING_BUSES_SHOWN: int = 5


class AcPowerFlow(TypedDict):
    """A solved AC power flow and its proof."""

    min_vm_pu: float | None
    max_vm_pu: float | None
    max_line_loading_percent: float | None
    proof: NumericalProof


class VqSensitivity(TypedDict):
    """dV/dQ at one PQ bus: from the Jacobian and from an independent finite difference."""

    bus: int
    dv_dq_pu_per_mvar_jacobian: float
    dv_dq_pu_per_mvar_finite_difference: float
    relative_difference: float
    agree: bool


class QvMode(TypedDict):
    """The reduced Jacobian's smallest eigenvalue and the buses most involved in it."""

    min_eigenvalue: float
    participating_buses: list[tuple[int, float]]
    reduced_jacobian_condition: float
    warnings: list[str]


def ac_power_flow(net: pp.pandapowerNet) -> AcPowerFlow:
    """Solve a copy of ``net`` and return its results with the proof contract."""
    work = copy.deepcopy(net)
    try:
        iterations = pm._runpp_counted(work, numba=True, max_iteration=MAX_ITERATIONS)
    except pp.LoadflowNotConverged as error:
        return AcPowerFlow(min_vm_pu=None, max_vm_pu=None, max_line_loading_percent=None,
                           proof=make_proof(converged=False, feasibility_status="indeterminate",
                                            solver_name="pandapower.runpp (Newton-Raphson)", solver_distribution="pandapower",
                                            validation_warnings=[f"LoadflowNotConverged: {error}"]))
    residual = kcl_residual_mva(work)
    jacobian = work._ppc["internal"]["J"]
    condition = float(np.linalg.cond(jacobian.toarray()))
    violations = list(_ac_violations(work).values())
    vm = work.res_bus.vm_pu.astype(float)
    finite = vm[np.isfinite(vm)]
    status = "feasible" if residual <= KCL_RESIDUAL_TOLERANCE_MVA and not violations else \
        "infeasible" if residual <= KCL_RESIDUAL_TOLERANCE_MVA else "indeterminate"
    warnings = [] if residual <= KCL_RESIDUAL_TOLERANCE_MVA else \
        [f"Kirchhoff residual {residual:.3g} MVA exceeds {KCL_RESIDUAL_TOLERANCE_MVA} MVA"]
    proof = make_proof(converged=True, feasibility_status=status, solver_name="pandapower.runpp (Newton-Raphson)",
                       solver_distribution="pandapower", equation_residuals={"kcl_residual_mva": residual},
                       constraint_violations=violations, solver_iterations=iterations,
                       conditioning={"jacobian_condition_number": condition}, validation_warnings=warnings)
    loading = work.res_line.loading_percent.astype(float) if len(work.line) else None
    return AcPowerFlow(min_vm_pu=float(finite.min()) if len(finite) else None,
                       max_vm_pu=float(finite.max()) if len(finite) else None,
                       max_line_loading_percent=float(loading[np.isfinite(loading)].max()) if loading is not None else None,
                       proof=proof)


def _solved_internal(net: pp.pandapowerNet) -> tuple[pp.pandapowerNet, dict]:
    """A solved copy and its internal case; refuses renumbered internal cases."""
    work = copy.deepcopy(net)
    pm._runpp_counted(work, numba=True, max_iteration=MAX_ITERATIONS)
    internal = work._ppc["internal"]
    lookup = work._pd2ppc_lookups["bus"]
    if len(internal["bus"]) != len(work._ppc["bus"]) or not np.array_equal(np.sort(lookup[work.bus.index]),
                                                                            np.arange(len(work.bus))):
        raise UnsupportedModel("the solver's internal case renumbers buses (out-of-service or fused buses)")
    return work, internal


def _reduced_jacobian(internal: dict) -> tuple[np.ndarray, np.ndarray]:
    """J_R = J_QV - J_Qtheta J_Ptheta^-1 J_PV (pu/pu) and the internal pq bus order of its rows."""
    jacobian = sp.csr_matrix(internal["J"])
    pv, pq = np.asarray(internal["pv"]), np.asarray(internal["pq"])
    n_theta = len(pv) + len(pq)
    j_p_theta, j_p_v = jacobian[:n_theta, :n_theta], jacobian[:n_theta, n_theta:]
    j_q_theta, j_q_v = jacobian[n_theta:, :n_theta], jacobian[n_theta:, n_theta:]
    correction = j_q_theta @ sp.linalg.spsolve(sp.csc_matrix(j_p_theta), j_p_v.toarray())
    return np.asarray(j_q_v.toarray() - correction), pq


def vq_sensitivity(net: pp.pandapowerNet, bus: int) -> VqSensitivity:
    """dV/dQ at PQ bus ``bus`` (pu per Mvar of reactive injection), cross-checked by finite difference.

    Raises:
        UnsupportedModel: if ``bus`` is not a PQ bus, or the internal case renumbers buses.
    """
    work, internal = _solved_internal(net)
    reduced, pq = _reduced_jacobian(internal)
    row = int(work._pd2ppc_lookups["bus"][bus])
    if row not in set(pq.tolist()):
        raise UnsupportedModel(f"bus {bus} is not a PQ bus: its voltage is controlled, so dV/dQ is not defined")
    k = int(np.flatnonzero(pq == row)[0])
    unit = np.zeros(len(pq))
    unit[k] = 1.0
    jacobian_value = float(np.linalg.solve(reduced, unit)[k]) / float(internal["baseMVA"])

    def voltage_with_load_q(delta_mvar: float) -> float:
        moved = copy.deepcopy(net)
        pp.create_load(moved, bus, p_mw=0.0, q_mvar=delta_mvar, name="vq-sensitivity probe")
        pm._runpp_counted(moved, numba=True, max_iteration=MAX_ITERATIONS)
        return float(moved.res_bus.vm_pu[bus])

    # A load of +dQ is a reactive injection of -dQ, hence the sign.
    fd_value = (voltage_with_load_q(-FD_STEP_MVAR) - voltage_with_load_q(FD_STEP_MVAR)) / (2 * FD_STEP_MVAR)
    rel = abs(jacobian_value - fd_value) / max(abs(fd_value), 1e-12)
    return VqSensitivity(bus=bus, dv_dq_pu_per_mvar_jacobian=jacobian_value, dv_dq_pu_per_mvar_finite_difference=fd_value,
                         relative_difference=rel, agree=rel <= FD_REL_TOLERANCE)


def qv_modal(net: pp.pandapowerNet) -> QvMode:
    """The reduced Jacobian's smallest eigenvalue and the PQ buses that participate most."""
    work, internal = _solved_internal(net)
    reduced, pq = _reduced_jacobian(internal)
    values, right = np.linalg.eig(reduced)
    left = np.linalg.inv(right)
    i = int(np.argmin(values.real))
    participation = np.abs(right[:, i] * left[i, :])
    participation = participation / participation.sum()
    to_bus = {int(work._pd2ppc_lookups["bus"][b]): int(b) for b in work.bus.index}
    order = np.argsort(participation)[::-1][:PARTICIPATING_BUSES_SHOWN]
    buses = [(to_bus[int(pq[j])], float(participation[j])) for j in order]
    warnings = []
    if values.real[i] < 0:
        # The positive-eigenvalue criterion assumes inductive branches. snemSA's two negative-
        # reactance lines (293, 294: series-compensation equivalents, x ~ -63 ohm) give exactly
        # its two negative modes, at their end buses (measured 2026-10-23; the mode is real in
        # the linearised model, confirmed by perturbation), so a negative mode next to such a
        # branch is not by itself evidence of voltage instability.
        named = {b for b, _ in buses}
        capacitive = [int(k) for k in net.line.index[(net.line.x_ohm_per_km < 0)
                                                    & (net.line.from_bus.isin(named) | net.line.to_bus.isin(named))]]
        warnings.append(f"negative Q-V eigenvalue {values.real[i]:.4g}" + (
            f"; the participating buses end negative-reactance lines {capacitive} (series compensation), where "
            "the eigenvalue criterion does not establish instability" if capacitive else
            "; no negative-reactance branch is adjacent: investigate before relying on this operating point"))
    return QvMode(min_eigenvalue=float(values.real[i]), participating_buses=buses,
                  reduced_jacobian_condition=float(np.linalg.cond(reduced)), warnings=warnings)
