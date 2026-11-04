"""N-1 screening reports coverage honestly and charges only base-relative violations."""

from __future__ import annotations

import math

import pandapower as pp
import pytest

from powercoder.engineering.conftest import requires_snem
from powercoder.engineering.contingency import KCL_RESIDUAL_TOLERANCE_MVA, ScreeningResult, screen_n1
from powercoder.inference import powermcp as pm

# case9 loads scaled by 1.6: the intact case still solves, but two outages leave no AC
# operating point (measured: 2 of 9 non-convergent at 1.6, 0 at 1.0).
STRESSED_LOAD_SCALE: float = 1.6
# Base-case voltage violations of snem-sa-2000 (AGENTS.md FM-17).
SNEM_BASE_VOLTAGE_VIOLATIONS: int = 39
# The heuristic AC subset used to exercise partial coverage.
TOP_K: int = 3


def _labels(result: ScreeningResult) -> dict[tuple[str, int], str]:
    """Coverage label by branch."""
    return {(c["element_type"], c["element_id"]): c["coverage"] for c in result["contingencies"]}


def test_exhaustive_ac_screen_validates_every_outage(case9: pp.pandapowerNet) -> None:
    """Every branch AC-solved and Kirchhoff-checked; islanding outages listed, not violated."""
    result = screen_n1(case9)
    assert result["ac_complete"] and result["dc_complete"]
    assert result["coverage_counts"]["validated"] == result["universe_size"] == 9
    assert "INCOMPLETE" not in result["screening_claim"]
    for c in result["contingencies"]:
        ac = c["ac"]
        assert ac["kcl_residual_mva"] <= KCL_RESIDUAL_TOLERANCE_MVA
        de_energized = set(ac["de_energized_bus_ids"])
        assert not any(v["element_type"] == "bus" and v["element_id"] in de_energized for v in ac["new_violations"])
    assert [c["severity_rank"] for c in result["contingencies"]] == list(range(1, 10))


def test_heuristic_and_partial_screens_are_not_reported_complete(case9: pp.pandapowerNet) -> None:
    """AC top-k is heuristic; a restricted DC screen leaves outages unexamined."""
    topk = screen_n1(case9, ac_selection=TOP_K)
    assert not topk["ac_complete"] and "INCOMPLETE" in topk["screening_claim"]
    assert topk["coverage_counts"]["validated"] == TOP_K
    assert topk["coverage_counts"]["exhaustive"] == topk["universe_size"] - TOP_K  # DC-only, complete DC

    partial = screen_n1(case9, dc_elements=[("line", 1), ("line", 2)], ac_selection="none")
    assert not partial["dc_complete"] and not partial["ac_complete"]
    assert partial["coverage_counts"] == {"exhaustive": 0, "heuristic": 2, "validated": 0, "unexamined": 7}
    assert "unexamined" in partial["screening_claim"]
    assert _labels(partial)[("line", 1)] == "heuristic"


def test_non_convergent_outages_are_reported_never_solved(case9: pp.pandapowerNet) -> None:
    """A collapsed outage carries no voltages, is not validated, and ranks most severe."""
    case9.load[["p_mw", "q_mvar"]] *= STRESSED_LOAD_SCALE
    result = screen_n1(case9, dc=False)
    failed = [c for c in result["contingencies"] if c["ac"]["status"] != "solved"]
    assert result["ac_nonconvergent"] == len(failed) == 2
    for c in failed:
        assert c["coverage"] != "validated" and c["ac"]["min_vm_pu"] is None and c["ac"]["kcl_residual_mva"] is None
    assert {c["severity_rank"] for c in failed} == {1, 2}
    assert any("did not converge" in w for w in result["proof"]["validation_warnings"])


def test_a_base_case_that_does_not_solve_screens_nothing() -> None:
    """No base case, no contingency results: everything unexamined, proof indeterminate."""
    net, _ = pm._load_network_object("fixture-2bus-nonconverged")
    result = screen_n1(net)
    assert not result["proof"]["converged"] and result["proof"]["feasibility_status"] == "indeterminate"
    assert result["coverage_counts"]["unexamined"] == result["universe_size"]


@pytest.fixture(scope="module")
def snem_screen(snem_net: pp.pandapowerNet) -> ScreeningResult:
    """One exhaustive DC + AC N-1 screen of snem-sa-2000 (~20 s), shared by the tests below."""
    return screen_n1(snem_net)


@requires_snem
def test_snem_sa_2000_charges_only_new_violations(snem_screen: ScreeningResult) -> None:
    """The 39 intact-case voltage violations are never counted as contingency violations."""
    result = snem_screen
    assert result["base"]["voltage_violations"] == SNEM_BASE_VOLTAGE_VIOLATIONS
    base = {(v["element_type"], v["element_id"], v["kind"]) for v in result["base"]["violations"]}
    assert result["universe_size"] == 698 and result["ac_complete"] and result["dc_complete"]
    for c in result["contingencies"]:
        for v in c["ac"]["new_violations"]:
            assert (v["element_type"], v["element_id"], v["kind"]) not in base
            assert math.isfinite(v["value"])
    assert 0 < result["contingencies_with_new_violations"] < result["universe_size"]
    assert result["proof"]["equation_residuals"]["max_validated_contingency_kcl_mva"] <= KCL_RESIDUAL_TOLERANCE_MVA


@requires_snem
def test_snem_sa_2000_islanding_and_placeholder_ratings_are_named(snem_screen: ScreeningResult) -> None:
    """FM-25 outages are solved with de-energized buses listed; 10000 MVA ratings flagged."""
    result = snem_screen
    islanding = [c for c in result["contingencies"] if c["ac"]["de_energized_buses"]]
    assert islanding and all(c["ac"]["status"] == "solved" for c in islanding)
    warnings = " ".join(result["proof"]["validation_warnings"])
    assert "placeholders" in warnings and "already has 39 violations" in warnings
    assert result["base"]["placeholder_ratings"] == result["base"]["rated_branches"]


@requires_snem
def test_reassigning_island_slacks_energizes_what_the_single_slack_blacks_out(snem_net: pp.pandapowerNet) -> None:
    """0023 proposal 3: trafo 0's outage blacks out 502 buses under one slack; with a slack per
    island they are energized, Kirchhoff-validated, and the island's slack is flagged when it
    must leave its generator's [min_p, max_p] (here it must absorb surplus generation)."""
    from powercoder.engineering.contingency import KCL_RESIDUAL_TOLERANCE_MVA, reassign_island_slacks

    result = reassign_island_slacks(snem_net, ("trafo", 0))
    assert result["single_slack_de_energized_buses"] == 502
    assert result["status"] == "solved" and result["de_energized_buses"] == 0
    assert result["kcl_residual_mva"] <= KCL_RESIDUAL_TOLERANCE_MVA
    [island] = result["islands"]
    assert island["slack_p_mw"] < 0 and island["within_slack_capacity"] is False


def test_bounded_n2_pairs_the_top_singles_and_labels_its_coverage() -> None:
    """Every pair among the top singles is AC-solved once; the coverage says it is heuristic."""
    import pandapower.networks as pn

    from powercoder.engineering.contingency import screen_n2_bounded

    net = pn.case9()
    n1 = screen_n1(net, ac_selection="all")
    n2 = screen_n2_bounded(net, n1, top_singles=4)
    assert n2["pairs_examined"] == 6 and len({(p["first"], p["second"]) for p in n2["pairs"]}) == 6
    assert n2["coverage"].startswith("heuristic")
    assert all(p["first"] != p["second"] for p in n2["pairs"])
