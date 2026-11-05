"""DC OPF agrees with an independent HiGHS LP, and failures are never reported solved."""

from __future__ import annotations

import pandapower as pp
import pytest

from tests.engineering.conftest import requires_snem
from wattson.engineering.dc_opf import (
    DISPATCH_ABS_TOLERANCE_MW,
    LMP_ABS_TOLERANCE_EUR_PER_MWH,
    OBJECTIVE_REL_TOLERANCE,
    run_dc_opf,
)

# Linear costs (EUR/MWh) for case9's ext_grid, gen 0 and gen 1. case9 ships quadratic
# costs (checked by the independent QP); distinct linear costs give a unique merit order
# so the LP's dispatch and LMPs are unique and comparable.
CASE9_LINEAR_COSTS: list[float] = [20.0, 30.0, 10.0]
# Load multiplier that exceeds case9's total capacity (250 + 300 + 270 = 820 MW against
# 315 MW of load): 5 x 315 = 1575 MW cannot be served, so the OPF is provably infeasible.
INFEASIBLE_LOAD_SCALE: float = 5.0


def _linear_case9(net: pp.pandapowerNet) -> pp.pandapowerNet:
    """case9 with linear costs and its native 100 % line limits (line 4 binds)."""
    net.poly_cost["cp2_eur_per_mw2"] = 0.0
    net.poly_cost["cp1_eur_per_mw"] = CASE9_LINEAR_COSTS
    return net


def test_case9_matches_the_independent_lp_with_a_binding_line(case9: pp.pandapowerNet) -> None:
    """Objective, dispatch and LMPs agree; the congested line is reported with its price."""
    result = run_dc_opf(_linear_case9(case9))
    check = result["cross_check"]
    assert result["solved"] and result["proof"]["feasibility_status"] == "feasible"
    assert check["status"] == "agree" and check["dispatch_unique"]
    assert check["objective_rel_diff"] <= OBJECTIVE_REL_TOLERANCE
    assert check["max_dispatch_abs_diff_mw"] <= DISPATCH_ABS_TOLERANCE_MW
    assert check["max_lmp_abs_diff_eur_per_mwh"] <= LMP_ABS_TOLERANCE_EUR_PER_MWH
    flows = [b for b in result["binding_constraints"] if b["constraint"] == "flow_limit"]
    assert [(b["element_type"], b["element_id"]) for b in flows] == [("line", 4)]
    assert flows[0]["shadow_price_eur_per_mwh"] > 0.0
    low, high = result["lmp_range_eur_per_mwh"]
    assert high - low > 1.0  # congestion separates nodal prices
    assert 0.0 <= result["proof"]["optimality_gap_or_bound"] <= OBJECTIVE_REL_TOLERANCE * result["objective_eur_per_h"]
    assert result["proof"]["solver_iterations"] and result["proof"]["solver_iterations"] > 0


def test_the_input_network_is_not_modified(case9: pp.pandapowerNet) -> None:
    """run_dc_opf works on a copy."""
    net = _linear_case9(case9)
    before = net.load.p_mw.copy()
    run_dc_opf(net)
    assert not net.get("OPF_converged", False)  # no OPF result written back to the input
    assert net.load.p_mw.equals(before)


def test_an_infeasible_opf_is_proved_infeasible_not_solved(case9: pp.pandapowerNet) -> None:
    """PIPS fails; HiGHS certifies infeasibility; no objective or dispatch is returned."""
    net = _linear_case9(case9)
    net.load["p_mw"] *= INFEASIBLE_LOAD_SCALE
    result = run_dc_opf(net)
    assert not result["solved"] and not result["proof"]["converged"]
    assert result["proof"]["feasibility_status"] == "infeasible"
    assert result["cross_check"]["status"] == "infeasible"
    assert result["objective_eur_per_h"] is None and result["dispatch"] == []


def test_without_an_independent_formulation_the_result_is_indeterminate(case9: pp.pandapowerNet) -> None:
    """Piecewise-linear costs: pandapower converges, but nothing proves it, so it is not solved."""
    case9.poly_cost.drop(case9.poly_cost.index, inplace=True)
    for et, element in (("ext_grid", 0), ("gen", 0), ("gen", 1)):
        pp.create_pwl_cost(case9, element, et, [[0, 500, 20.0]])
    result = run_dc_opf(case9)
    assert not result["solved"]
    assert result["cross_check"]["status"] == "unavailable"
    assert result["proof"]["feasibility_status"] == "indeterminate"
    assert result["proof"]["optimality_gap_or_bound"] is None


def test_quadratic_costs_are_checked_by_the_independent_qp(case9: pp.pandapowerNet) -> None:
    """case9's native quadratic costs (0023): the QP's dispatch is unique, so it must match."""
    result = run_dc_opf(case9)
    cross = result["cross_check"]
    assert result["solved"] and cross["status"] == "agree", cross["reason"]
    assert cross["objective_rel_diff"] <= OBJECTIVE_REL_TOLERANCE
    assert cross["max_dispatch_abs_diff_mw"] <= DISPATCH_ABS_TOLERANCE_MW
    assert cross["max_lmp_abs_diff_eur_per_mwh"] <= LMP_ABS_TOLERANCE_EUR_PER_MWH


def test_the_qp_solver_reproduces_the_lp_on_linear_costs(case9: pp.pandapowerNet) -> None:
    """Same objective, dispatch and LMPs (so the same dual sign convention) when c2 = 0."""
    from wattson.engineering.dc_model import build_dc_case
    from wattson.engineering.dc_opf import solve_independent_lp, solve_independent_qp

    case = build_dc_case(_linear_case9(case9))
    lp, qp = solve_independent_lp(case), solve_independent_qp(case)
    assert lp["status"] == qp["status"] == "optimal"
    assert abs(lp["objective_eur_per_h"] - qp["objective_eur_per_h"]) <= OBJECTIVE_REL_TOLERANCE * abs(lp["objective_eur_per_h"])
    assert max(abs(a - b) for a, b in zip(lp["dispatch_mw"], qp["dispatch_mw"])) <= DISPATCH_ABS_TOLERANCE_MW
    assert max(abs(a - b) for a, b in zip(lp["lmp_eur_per_mwh"], qp["lmp_eur_per_mwh"])) <= LMP_ABS_TOLERANCE_EUR_PER_MWH


@requires_snem
def test_snem_sa_2000_matches_the_lp_on_its_degenerate_optimal_face(snem_net: pp.pandapowerNet) -> None:
    """All 57 units cost 10 EUR/MWh and no branch is limited: the optimum is unique in
    value but not in dispatch, and every differing unit lies on the LP's optimal face."""
    result = run_dc_opf(snem_net)
    check = result["cross_check"]
    assert result["solved"] and check["status"] == "agree"
    assert check["objective_rel_diff"] <= OBJECTIVE_REL_TOLERANCE
    assert check["dispatch_unique"] is False and check["units_outside_optimal_range"] == 0 and check["units_unprobed"] == 0
    assert check["max_lmp_abs_diff_eur_per_mwh"] <= LMP_ABS_TOLERANCE_EUR_PER_MWH
    assert result["rated_branches"] == 0 and result["binding_constraints"] == []
    assert result["objective_eur_per_h"] == pytest.approx(10.0 * snem_net.load.p_mw.sum(), rel=OBJECTIVE_REL_TOLERANCE)
    assert any("no branch has a flow limit" in w for w in result["proof"]["validation_warnings"])
    assert any("not unique" in w for w in result["proof"]["validation_warnings"])
