"""AC OPF: feasibility re-verified by an independent AC re-solve; optimality never claimed."""

from __future__ import annotations

import pandapower.networks as pn
import pytest

from wattson.engineering.ac_opf import VOLTAGE_MATCH_TOLERANCE_PU, run_ac_opf
from wattson.engineering.cases import CASES, REPO_ROOT, load_case


def test_case9_is_re_verified_feasible_without_an_optimality_claim() -> None:
    result = run_ac_opf(pn.case9())
    proof = result["proof"]
    assert result["solved"] and proof["feasibility_status"] == "feasible"
    assert result["max_voltage_mismatch_pu"] <= VOLTAGE_MATCH_TOLERANCE_PU
    assert proof["optimality_gap_or_bound"] is None
    assert any("no global optimality certificate" in w for w in proof["validation_warnings"])


def test_an_overloaded_demand_is_not_reported_solved() -> None:
    net = pn.case9()
    net.load["p_mw"] *= 5  # far beyond case9's generation: no feasible OPF exists
    result = run_ac_opf(net)
    assert not result["solved"] and result["proof"]["feasibility_status"] != "feasible"


@pytest.mark.skipif(not (REPO_ROOT / CASES["ieee39-pab-stressed"]["path"]).exists(), reason="run `just lab10-fetch` first")
def test_ieee39_binds_a_thermal_limit_at_a_verified_feasible_optimum() -> None:
    result = run_ac_opf(load_case("ieee39-pab-stressed"))
    assert result["solved"]
    assert any(b["quantity"] == "loading_percent" for b in result["binding"])
