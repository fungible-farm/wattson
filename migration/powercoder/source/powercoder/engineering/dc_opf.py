"""DC optimal power flow with an independent LP cross-check (PC-REQ-011, PC-REQ-016).

Primary solver: pandapower ``rundcopp`` (PYPOWER's DC OPF solved by the PIPS
primal-dual interior-point method). Independent check: the same economic dispatch
formulated directly from the case's matrices (``dc_model.build_dc_case``, which does not
use pandapower's DC/OPF code) and solved as an LP by HiGHS through
``scipy.optimize.linprog``. Two different formulations, two different algorithms
(interior point vs. HiGHS dual simplex / IPM), one answer -- or a reported discrepancy.

With linear costs the DC OPF is a linear program, so the LP optimum is a certified
global optimum (strong duality, HiGHS reports primal and dual feasibility), and the
gap ``pandapower objective - LP objective`` is a real optimality bound. That is why the
proof's ``optimality_gap_or_bound`` is populated here and would be ``None`` for AC OPF.

The dispatch of an LP can be non-unique (e.g. every unit at the same marginal cost
and no binding network limit -- exactly snem-sa-2000, where all 57 units cost
10 EUR/MWh and every rating is the 10000 MVA placeholder). Comparing two arbitrary
optimal vertices would then report a spurious "mismatch", so a dispatch difference is
resolved by asking HiGHS for each differing unit's range over the optimal face.

Licences: pandapower BSD-3-Clause; scipy BSD-3-Clause (HiGHS: MIT).
"""

from __future__ import annotations

import contextlib
import copy
import math
from typing import Any, Iterator, Literal, TypedDict

import numpy as np
import pandapower as pp
import pandapower.optimal_powerflow as pp_opf_module
import scipy.sparse as sp
from pandapower.auxiliary import OPFNotConverged
from scipy.optimize import linprog

from powercoder.engineering.dc_model import DcCase, UnsupportedModel, build_dc_case
from powercoder.engineering.proof import ConstraintViolation, NumericalProof, make_proof, package_version

# PIPS (pandapower/pypower/ppoption.py) stops at PDIPM_FEASTOL = PDIPM_GRADTOL =
# PDIPM_COMPTOL = 1e-6 (scaled, per-unit). 1e-6 pu at baseMVA 100 is 1e-4 MW, so a
# relative objective tolerance of 1e-6 and a per-unit dispatch tolerance of 1e-3 MW
# (10x the PIPS stopping scale) are what an agreement between two correct solvers can
# be held to. Declared before measuring (backlog 0016: "tolerances declared in advance").
OBJECTIVE_REL_TOLERANCE: float = 1e-6
DISPATCH_ABS_TOLERANCE_MW: float = 1e-3
# LMPs are duals; PIPS duals converge at the same 1e-6 scale, applied to costs of
# O(10-100) EUR/MWh: 1e-3 EUR/MWh is the comparable bound.
LMP_ABS_TOLERANCE_EUR_PER_MWH: float = 1e-3
# Independent residual checks of pandapower's reported solution, in MW. The DC
# equations are linear and solved to ~1e-10 by direct factorisation, but the OPF
# result is an interior-point iterate: 1e-3 MW matches the dispatch tolerance.
RESIDUAL_TOLERANCE_MW: float = 1e-3
# A limit counts as binding when the solution is within this many MW of it -- the
# same scale as the dispatch tolerance, so "binding" is a statement the cross-check
# could confirm.
BINDING_TOLERANCE_MW: float = 1e-3
# How many differing units get an optimal-face range probe (two LPs each). 64 covers
# every unit of snem-sa-2000 (57) while bounding the cost on larger cases; units beyond
# the cap are reported unexamined, never assumed consistent.
MAX_DEGENERACY_PROBES: int = 64

CrossCheckStatus = Literal["agree", "disagree", "infeasible", "unavailable", "failed"]


class DispatchEntry(TypedDict):
    """One unit's DC OPF set point and limits."""

    element_type: str
    element_id: int
    bus: int
    p_mw: float
    min_p_mw: float
    max_p_mw: float


class BindingConstraint(TypedDict):
    """A limit the reported optimum sits on, with its shadow price where one exists."""

    element_type: str
    element_id: int
    constraint: str  # "p_max", "p_min", "flow_limit"
    value_mw: float
    limit_mw: float
    shadow_price_eur_per_mwh: float | None


class LpSolution(TypedDict):
    """The independent HiGHS LP solution."""

    status: str  # "optimal", "infeasible", "unbounded", "error"
    message: str
    objective_eur_per_h: float | None
    dispatch_mw: list[float] | None  # aligned with DcCase.generators
    lmp_eur_per_mwh: list[float] | None  # aligned with DcCase.bus_ids
    iterations: int | None


class CrossCheck(TypedDict):
    """Agreement between pandapower's DC OPF and the independent LP."""

    status: CrossCheckStatus
    reason: str
    lp_objective_eur_per_h: float | None
    objective_abs_diff: float | None
    objective_rel_diff: float | None
    max_dispatch_abs_diff_mw: float | None
    units_differing: int | None
    dispatch_unique: bool | None
    units_outside_optimal_range: int | None
    units_unprobed: int | None
    max_lmp_abs_diff_eur_per_mwh: float | None
    lp_iterations: int | None
    tolerances: dict[str, float]


class DcOpfResult(TypedDict):
    """DC OPF result with binding constraints, LMPs, cross-check and proof contract."""

    solved: bool
    objective_eur_per_h: float | None
    dispatch: list[DispatchEntry]
    binding_constraints: list[BindingConstraint]
    lmp_eur_per_mwh: dict[int, float]
    lmp_range_eur_per_mwh: tuple[float, float] | None
    rated_branches: int
    cross_check: CrossCheck
    proof: NumericalProof


TOLERANCES: dict[str, float] = {
    "objective_rel": OBJECTIVE_REL_TOLERANCE,
    "dispatch_abs_mw": DISPATCH_ABS_TOLERANCE_MW,
    "lmp_abs_eur_per_mwh": LMP_ABS_TOLERANCE_EUR_PER_MWH,
    "residual_mw": RESIDUAL_TOLERANCE_MW,
}


@contextlib.contextmanager
def _capture_opf_raw() -> Iterator[dict[str, Any]]:
    """Record PYPOWER's raw OPF result (iterations, multipliers) during ``rundcopp``.

    pandapower 3.5.4 discards ``result["raw"]["output"]["iterations"]`` after the OPF.
    Wrapping the module-level ``opf`` reference that ``_optimal_powerflow`` calls --
    in-process only, restored in ``finally`` -- keeps the solver's own iteration count
    for the proof contract without editing pandapower.
    """
    captured: dict[str, Any] = {}
    original = pp_opf_module.opf

    def recording_opf(ppci: dict, ppopt: dict) -> dict:
        result = original(ppci, ppopt)
        captured["result"] = result
        return result

    pp_opf_module.opf = recording_opf
    try:
        yield captured
    finally:
        pp_opf_module.opf = original


def _lp_matrices(case: DcCase) -> tuple[np.ndarray, sp.csr_matrix, np.ndarray, sp.csr_matrix | None, np.ndarray | None, list[tuple[float | None, float | None]], float]:
    """LP data for min c'x, x = [theta (nb), Pg (ng)] in per unit.

    Equality (nodal balance): Bbus theta + Pbusinj = Cg Pg - Pd.
    Inequality (rated branches only): |Bf theta + Pfinj| <= rate.
    """
    nb, ng = len(case.bus_ids), len(case.generators)
    base = case.base_mva
    cg = sp.csr_matrix((np.ones(ng), ([g.bus_row for g in case.generators], np.arange(ng))), shape=(nb, ng))
    a_eq = sp.hstack([case.bbus, -cg]).tocsr()
    b_eq = -case.pd_mw / base - case.pbusinj_pu
    rated = np.flatnonzero(case.rate_mw > 0)
    a_ub = b_ub = None
    if len(rated):
        bf_r = case.bf[rated]
        zeros = sp.csr_matrix((len(rated), ng))
        a_ub = sp.vstack([sp.hstack([bf_r, zeros]), sp.hstack([-bf_r, zeros])]).tocsr()
        rate = case.rate_mw[rated] / base
        b_ub = np.r_[rate - case.pfinj_pu[rated], rate + case.pfinj_pu[rated]]
    cost = np.r_[np.zeros(nb), [g.cost_c1_per_mw * base for g in case.generators]]
    bounds: list[tuple[float | None, float | None]] = [(None, None)] * nb
    for row in case.ref_rows:
        bounds[row] = (0.0, 0.0)
    for g in case.generators:
        bounds.append((None if math.isinf(g.p_min_mw) else g.p_min_mw / base,
                       None if math.isinf(g.p_max_mw) else g.p_max_mw / base))
    constant = float(sum(g.cost_c0 for g in case.generators))
    return cost, a_eq, b_eq, a_ub, b_ub, bounds, constant


def is_quadratic(case: DcCase) -> bool:
    """Whether any generator has a quadratic cost term (the OPF is then a QP)."""
    return any(g.cost_c2_per_mw2 > 0.0 for g in case.generators)


def solve_independent_qp(case: DcCase) -> LpSolution:
    """Solve the DC OPF of ``case`` as a convex QP with HiGHS (no pandapower solver code).

    Same matrices as the LP (``_lp_matrices``) plus the Hessian of the quadratic costs:
    with x = [theta, Pg] in per unit, cost = sum(c2 base^2 Pg^2 + c1 base Pg) + c0, so
    H = diag(0, ..., 2 c2 base^2) in HiGHS's 0.5 x'Hx form. LMPs come from the nodal
    balance rows' duals with the LP's sign convention; ``test_dc_opf`` checks that this
    solver reproduces the LP on a linear case. 0023: IEEE 118, the one available case
    with varied costs, has quadratic costs.
    """
    import highspy

    cost, a_eq, b_eq, a_ub, b_ub, bounds, constant = _lp_matrices(case)
    nb, ng = len(case.bus_ids), len(case.generators)
    inf = highspy.kHighsInf
    a = a_eq if a_ub is None else sp.vstack([a_eq, a_ub]).tocsr()
    lower = np.r_[b_eq, np.full(0 if a_ub is None else a_ub.shape[0], -inf)]
    upper = np.r_[b_eq, np.zeros(0) if b_ub is None else b_ub]
    csc = a.tocsc()
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = nb + ng, a.shape[0]
    lp.col_cost_ = cost
    lp.col_lower_ = np.array([-inf if lo is None else lo for lo, _ in bounds])
    lp.col_upper_ = np.array([inf if hi is None else hi for _, hi in bounds])
    lp.row_lower_, lp.row_upper_ = lower, upper
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = csc.indptr, csc.indices, csc.data
    hessian = highspy.HighsHessian()
    diagonal = np.r_[np.zeros(nb), [2.0 * g.cost_c2_per_mw2 * case.base_mva ** 2 for g in case.generators]]
    hessian.dim_, hessian.format_ = nb + ng, highspy.HessianFormat.kTriangular
    hessian.start_ = np.arange(nb + ng + 1, dtype=np.int32)
    hessian.index_ = np.arange(nb + ng, dtype=np.int32)
    hessian.value_ = diagonal
    highs = highspy.Highs()
    highs.setOptionValue("output_flag", False)
    model = highspy.HighsModel()
    model.lp_, model.hessian_ = lp, hessian
    highs.passModel(model)
    highs.run()
    status = highs.getModelStatus()
    name = {highspy.HighsModelStatus.kOptimal: "optimal", highspy.HighsModelStatus.kInfeasible: "infeasible",
            highspy.HighsModelStatus.kUnbounded: "unbounded"}.get(status, "error")
    info = highs.getInfo()
    iterations = int(getattr(info, "qp_iteration_count", 0) or getattr(info, "simplex_iteration_count", 0) or 0)
    if name != "optimal":
        return LpSolution(status=name, message=str(status), objective_eur_per_h=None, dispatch_mw=None,
                          lmp_eur_per_mwh=None, iterations=iterations)
    solution = highs.getSolution()
    x = np.asarray(solution.col_value)
    duals = np.asarray(solution.row_dual)[:a_eq.shape[0]]
    return LpSolution(status="optimal", message=str(status), objective_eur_per_h=float(highs.getObjectiveValue() + constant),
                      dispatch_mw=(x[nb:] * case.base_mva).tolist(), lmp_eur_per_mwh=(-duals / case.base_mva).tolist(),
                      iterations=iterations)


def solve_independent_lp(case: DcCase) -> LpSolution:
    """Solve the DC OPF of ``case`` as an LP with HiGHS (no pandapower solver code).

    A case with quadratic costs goes to :func:`solve_independent_qp`.

    LMPs are the nodal-balance duals: raising Pd at bus i by 1 MW lowers that row's
    right-hand side by 1/base, so LMP_i = -marginal_i / base (EUR/MWh).
    """
    if is_quadratic(case):
        return solve_independent_qp(case)
    cost, a_eq, b_eq, a_ub, b_ub, bounds, constant = _lp_matrices(case)
    nb = len(case.bus_ids)
    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    status = {0: "optimal", 2: "infeasible", 3: "unbounded"}.get(result.status, "error")
    if status != "optimal":
        return LpSolution(status=status, message=str(result.message), objective_eur_per_h=None,
                          dispatch_mw=None, lmp_eur_per_mwh=None, iterations=int(getattr(result, "nit", 0) or 0))
    pg = result.x[nb:] * case.base_mva
    lmp = -np.asarray(result.eqlin.marginals) / case.base_mva
    return LpSolution(status="optimal", message=str(result.message),
                      objective_eur_per_h=float(result.fun + constant), dispatch_mw=pg.tolist(),
                      lmp_eur_per_mwh=lmp.tolist(), iterations=int(result.nit))


def _optimal_range(case: DcCase, unit: int, objective_cap: float) -> tuple[float, float] | None:
    """Min and max of one unit's dispatch over the LP's optimal face (MW), or None."""
    cost, a_eq, b_eq, a_ub, b_ub, bounds, constant = _lp_matrices(case)
    nb = len(case.bus_ids)
    cap_row = sp.csr_matrix(cost.reshape(1, -1))
    cap_rhs = np.array([objective_cap - constant])
    a = cap_row if a_ub is None else sp.vstack([a_ub, cap_row]).tocsr()
    b = cap_rhs if b_ub is None else np.r_[b_ub, cap_rhs]
    out: list[float] = []
    for sign in (1.0, -1.0):
        c = np.zeros_like(cost)
        c[nb + unit] = sign
        result = linprog(c, A_ub=a, b_ub=b, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
        if result.status != 0:
            return None
        out.append(float(result.x[nb + unit] * case.base_mva))
    return out[0], out[1]


def _cross_check(case: DcCase, lp: LpSolution, objective: float, dispatch: list[float],
                 lmp: list[float]) -> CrossCheck:
    """Compare pandapower's optimum with the LP's, resolving LP degeneracy."""
    lp_obj = lp["objective_eur_per_h"]
    assert lp_obj is not None and lp["dispatch_mw"] is not None and lp["lmp_eur_per_mwh"] is not None
    abs_diff = abs(objective - lp_obj)
    rel_diff = abs_diff / max(1.0, abs(lp_obj))
    diffs = np.abs(np.asarray(dispatch) - np.asarray(lp["dispatch_mw"]))
    differing = [int(k) for k in np.flatnonzero(diffs > DISPATCH_ABS_TOLERANCE_MW)]
    outside = 0
    # Strictly convex quadratic costs make the dispatch unique, so a differing unit is a
    # disagreement; the LP's optimal-face probe applies only to linear costs.
    probed = [] if is_quadratic(case) else differing[:MAX_DEGENERACY_PROBES]
    if is_quadratic(case):
        outside = len(differing)
    cap = lp_obj + OBJECTIVE_REL_TOLERANCE * max(1.0, abs(lp_obj))
    for unit in probed:
        span = _optimal_range(case, unit, cap)
        if span is None or not (span[0] - DISPATCH_ABS_TOLERANCE_MW <= dispatch[unit] <= span[1] + DISPATCH_ABS_TOLERANCE_MW):
            outside += 1
    unprobed = 0 if is_quadratic(case) else len(differing) - len(probed)
    lmp_diff = float(np.max(np.abs(np.asarray(lmp) - np.asarray(lp["lmp_eur_per_mwh"])))) if lmp else 0.0
    agree = (rel_diff <= OBJECTIVE_REL_TOLERANCE and outside == 0 and unprobed == 0)
    # LMPs are unique only when the dual is; with a degenerate primal the duals still
    # agree here because no network limit binds, but a dual mismatch is reported, not gated.
    reason = ("objective within tolerance; every differing unit lies on the LP's optimal face"
              if agree and differing else "objective and dispatch within tolerance" if agree
              else "objective or dispatch outside the declared tolerances")
    return CrossCheck(
        status="agree" if agree else "disagree", reason=reason, lp_objective_eur_per_h=lp_obj,
        objective_abs_diff=abs_diff, objective_rel_diff=rel_diff,
        max_dispatch_abs_diff_mw=float(diffs.max()) if len(diffs) else 0.0,
        units_differing=len(differing), dispatch_unique=not differing,
        units_outside_optimal_range=outside, units_unprobed=unprobed,
        max_lmp_abs_diff_eur_per_mwh=lmp_diff, lp_iterations=lp["iterations"], tolerances=dict(TOLERANCES),
    )


def _empty_cross_check(status: CrossCheckStatus, reason: str, lp: LpSolution | None = None) -> CrossCheck:
    """A cross-check record carrying no comparison numbers."""
    return CrossCheck(status=status, reason=reason, lp_objective_eur_per_h=None, objective_abs_diff=None,
                      objective_rel_diff=None, max_dispatch_abs_diff_mw=None, units_differing=None,
                      dispatch_unique=None, units_outside_optimal_range=None, units_unprobed=None,
                      max_lmp_abs_diff_eur_per_mwh=None, lp_iterations=lp["iterations"] if lp else None,
                      tolerances=dict(TOLERANCES))


def run_dc_opf(net: pp.pandapowerNet) -> DcOpfResult:
    """Solve the DC OPF of ``net`` (a copy; ``net`` is not modified) and prove it.

    The result is ``solved`` only if pandapower's OPF converged, its dispatch and angles
    satisfy the independently built DC equations and limits within tolerance, and the
    independent LP agrees (or is unavailable, which is then stated in a warning and the
    optimality bound is ``None``). An OPF that fails is ``infeasible`` only when HiGHS
    proves infeasibility of the independent LP; otherwise it is ``indeterminate``.
    """
    warnings: list[str] = []
    work = copy.deepcopy(net)
    case: DcCase | None
    lp: LpSolution | None = None
    try:
        case = build_dc_case(work)
        lp = solve_independent_lp(case)
    except UnsupportedModel as error:
        case = None
        warnings.append(f"independent LP unavailable: {error}")

    raw: dict[str, Any]
    try:
        with _capture_opf_raw() as raw:
            pp.rundcopp(work)
    except OPFNotConverged:
        status = "infeasible" if lp is not None and lp["status"] == "infeasible" else "indeterminate"
        if status == "infeasible":
            warnings.append(f"PIPS did not converge; HiGHS proves the independent LP infeasible ({lp['message']})")
        else:
            warnings.append("PIPS did not converge and infeasibility is not proved")
        iterations = _iterations(raw)
        return _unsolved(status, warnings, lp, iterations, case)
    except Exception as error:  # noqa: BLE001 -- any backend failure is a reported outcome, never a solution
        warnings.append(f"rundcopp raised {type(error).__name__}: {error}")
        return _unsolved("failed", warnings, lp, None, case)

    iterations = _iterations(raw)
    objective = float(work.res_cost)
    if case is None:
        # No independent model: report pandapower's numbers without a proof of feasibility.
        dispatch_entries = _dispatch_from_tables(work)
        proof = make_proof(converged=True, feasibility_status="indeterminate", solver_name="pandapower.rundcopp (PIPS)",
                           solver_distribution="pandapower", solver_iterations=iterations, validation_warnings=warnings)
        return DcOpfResult(solved=False, objective_eur_per_h=objective, dispatch=dispatch_entries, binding_constraints=[],
                           lmp_eur_per_mwh={int(b): float(v) for b, v in work.res_bus.lam_p.items()},
                           lmp_range_eur_per_mwh=None, rated_branches=0,
                           cross_check=_empty_cross_check("unavailable", warnings[0]), proof=proof)

    base = case.base_mva
    dispatch = [float(work[f"res_{g.element_type}"].at[g.element_id, "p_mw"]) for g in case.generators]
    theta = np.deg2rad(np.array([float(work.res_bus.at[b, "va_degree"]) for b in case.bus_ids]))
    lmp = [float(work.res_bus.at[b, "lam_p"]) for b in case.bus_ids]

    # Independent residuals of pandapower's solution in the independent model.
    injection = np.zeros(len(case.bus_ids))
    np.add.at(injection, [g.bus_row for g in case.generators], dispatch)
    nodal = (case.bbus @ theta + case.pbusinj_pu) * base - (injection - case.pd_mw)
    flows = (case.bf @ theta + case.pfinj_pu) * base
    reported = np.array([float(work[f"res_{table}"].at[idx, "p_from_mw" if table == "line" else "p_hv_mw"])
                         for table, idx in case.branch_ids])
    residuals = {
        "nodal_p_balance_mw": float(np.max(np.abs(nodal))) if len(nodal) else 0.0,
        "branch_flow_consistency_mw": float(np.max(np.abs(flows - reported))) if len(flows) else 0.0,
        "system_p_balance_mw": float(abs(sum(dispatch) - case.pd_mw.sum() - case.pbusinj_pu.sum() * base)),
    }

    violations: list[ConstraintViolation] = []
    binding: list[BindingConstraint] = []
    for g, p in zip(case.generators, dispatch):
        if p > g.p_max_mw + RESIDUAL_TOLERANCE_MW:
            violations.append(ConstraintViolation(element_type=g.element_type, element_id=g.element_id, quantity="p_mw",
                                                  value=p, limit=g.p_max_mw, kind="above_max"))
        if p < g.p_min_mw - RESIDUAL_TOLERANCE_MW:
            violations.append(ConstraintViolation(element_type=g.element_type, element_id=g.element_id, quantity="p_mw",
                                                  value=p, limit=g.p_min_mw, kind="below_min"))
        for name, limit in (("p_max", g.p_max_mw), ("p_min", g.p_min_mw)):
            if math.isfinite(limit) and g.p_min_mw != g.p_max_mw and abs(p - limit) <= BINDING_TOLERANCE_MW:
                binding.append(BindingConstraint(element_type=g.element_type, element_id=g.element_id, constraint=name,
                                                 value_mw=p, limit_mw=limit, shadow_price_eur_per_mwh=None))
    mu = _branch_multipliers(work, case)
    for k in np.flatnonzero(case.rate_mw > 0):
        table, idx = case.branch_ids[k]
        if abs(flows[k]) > case.rate_mw[k] + RESIDUAL_TOLERANCE_MW:
            violations.append(ConstraintViolation(element_type=table, element_id=idx, quantity="p_mw",
                                                  value=float(flows[k]), limit=float(case.rate_mw[k]), kind="above_max"))
        if abs(abs(flows[k]) - case.rate_mw[k]) <= BINDING_TOLERANCE_MW:
            binding.append(BindingConstraint(element_type=table, element_id=idx, constraint="flow_limit",
                                             value_mw=float(flows[k]), limit_mw=float(case.rate_mw[k]),
                                             shadow_price_eur_per_mwh=mu[k] if mu is not None else None))

    assert lp is not None
    if lp["status"] == "optimal":
        cross = _cross_check(case, lp, objective, dispatch, lmp)
        gap: float | None = objective - float(lp["objective_eur_per_h"])  # type: ignore[arg-type]
        basis: str | None = "LP strong duality: HiGHS optimum of the independent formulation is a certified bound"
    else:
        cross = _empty_cross_check("disagree", f"pandapower converged but the independent LP is {lp['status']}", lp)
        gap, basis = None, None
        warnings.append(cross["reason"])
    if (rated := int(np.sum(case.rate_mw > 0))) == 0:
        warnings.append("no branch has a flow limit (no max_loading_percent): the OPF is a pure economic dispatch")
    if (cross["max_lmp_abs_diff_eur_per_mwh"] or 0.0) > LMP_ABS_TOLERANCE_EUR_PER_MWH:
        # Not gated: with a degenerate primal (a limit binding with zero marginal value)
        # the dual set is not a point, and two correct solvers can return different LMPs.
        warnings.append(f"LMPs differ by up to {cross['max_lmp_abs_diff_eur_per_mwh']:.6g} EUR/MWh between solvers: "
                        "the duals are not unique for this case; quote LMPs only with this caveat")
    if cross["dispatch_unique"] is False:
        warnings.append(f"dispatch is not unique: {cross['units_differing']} units differ between two optimal solutions")

    residual_ok = all(v <= RESIDUAL_TOLERANCE_MW for v in residuals.values())
    solved = residual_ok and not violations and cross["status"] == "agree"
    if not residual_ok:
        warnings.append("pandapower's reported solution fails the independent residual check")
    proof = make_proof(
        converged=True, feasibility_status="feasible" if solved else "indeterminate",
        solver_name="pandapower.rundcopp (PYPOWER DC OPF, PIPS)", solver_distribution="pandapower",
        equation_residuals=residuals, constraint_violations=violations,
        optimality_gap_or_bound=gap, optimality_basis=basis, solver_iterations=iterations,
        conditioning=None, validation_warnings=warnings,
    )
    entries = [DispatchEntry(element_type=g.element_type, element_id=g.element_id, bus=case.bus_ids[g.bus_row],
                             p_mw=p, min_p_mw=g.p_min_mw, max_p_mw=g.p_max_mw) for g, p in zip(case.generators, dispatch)]
    return DcOpfResult(
        solved=solved, objective_eur_per_h=objective, dispatch=entries, binding_constraints=binding,
        lmp_eur_per_mwh={b: v for b, v in zip(case.bus_ids, lmp)}, lmp_range_eur_per_mwh=(min(lmp), max(lmp)),
        rated_branches=rated, cross_check=cross, proof=proof,
    )


def _iterations(raw: dict[str, Any]) -> int | None:
    """PIPS iteration count from a captured raw OPF result, if any."""
    try:
        return int(raw["result"]["raw"]["output"]["iterations"])
    except (KeyError, TypeError):
        return None


def _branch_multipliers(net: pp.pandapowerNet, case: DcCase) -> list[float] | None:
    """Flow-limit shadow prices (EUR/MWh) for model branches, from the OPF's ppc result."""
    from pandapower.pypower.idx_brch import MU_SF, MU_ST

    branch = net._ppc.get("branch") if isinstance(getattr(net, "_ppc", None), dict) else None
    if branch is None or branch.shape[1] <= MU_ST:
        return None
    rows = case.ppc_branch_rows
    return (np.real(branch[rows, MU_SF]) + np.real(branch[rows, MU_ST])).tolist()


def _dispatch_from_tables(net: pp.pandapowerNet) -> list[DispatchEntry]:
    """Dispatch straight from pandapower's result tables (no independent model)."""
    out: list[DispatchEntry] = []
    for table in ("gen", "ext_grid"):
        for idx in net[f"res_{table}"].index:
            row = net[table].loc[idx]
            out.append(DispatchEntry(element_type=table, element_id=int(idx), bus=int(row.bus),
                                     p_mw=float(net[f"res_{table}"].at[idx, "p_mw"]),
                                     min_p_mw=float(row.get("min_p_mw", -np.inf)), max_p_mw=float(row.get("max_p_mw", np.inf))))
    return out


def _unsolved(status: str, warnings: list[str], lp: LpSolution | None, iterations: int | None,
              case: DcCase | None) -> DcOpfResult:
    """Result for an OPF that produced no solution: no objective, no dispatch."""
    if lp is not None and lp["status"] == "infeasible":
        cross = _empty_cross_check("infeasible", "both solvers found no solution; HiGHS certifies LP infeasibility", lp)
    elif lp is not None and lp["status"] == "optimal":
        cross = _empty_cross_check("disagree", "pandapower found no solution but the independent LP is optimal", lp)
        cross["lp_objective_eur_per_h"] = lp["objective_eur_per_h"]
    else:
        cross = _empty_cross_check("unavailable" if lp is None else "failed", "no comparison possible", lp)
    proof = make_proof(converged=False, feasibility_status=status,  # type: ignore[arg-type]
                       solver_name="pandapower.rundcopp (PYPOWER DC OPF, PIPS)", solver_distribution="pandapower",
                       solver_iterations=iterations, validation_warnings=warnings)
    return DcOpfResult(solved=False, objective_eur_per_h=None, dispatch=[], binding_constraints=[], lmp_eur_per_mwh={},
                       lmp_range_eur_per_mwh=None, rated_branches=int(np.sum(case.rate_mw > 0)) if case else 0,
                       cross_check=cross, proof=proof)


SOLVER_VERSIONS: dict[str, str] = {"pandapower": package_version("pandapower"), "scipy": package_version("scipy")}
