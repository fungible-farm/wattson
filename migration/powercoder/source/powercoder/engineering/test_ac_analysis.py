"""AC power flow proof, V/Q sensitivities cross-checked by finite difference, and Q-V modes."""

from __future__ import annotations

import pandapower as pp
import pandapower.networks as pn
import pytest

from powercoder.engineering.ac_analysis import FD_REL_TOLERANCE, ac_power_flow, qv_modal, vq_sensitivity
from powercoder.engineering.conftest import requires_snem
from powercoder.engineering.dc_model import UnsupportedModel


@pytest.fixture
def case9() -> pp.pandapowerNet:
    return pn.case9()


def test_the_proof_carries_the_residual_iterations_and_conditioning(case9: pp.pandapowerNet) -> None:
    proof = ac_power_flow(case9)["proof"]
    assert proof["converged"] and proof["feasibility_status"] == "feasible"
    assert proof["equation_residuals"]["kcl_residual_mva"] < 1e-6 and proof["solver_iterations"] > 0
    assert proof["conditioning"]["jacobian_condition_number"] > 1


def test_dv_dq_from_the_jacobian_matches_an_independent_finite_difference(case9: pp.pandapowerNet) -> None:
    for bus in (4, 6, 8):
        result = vq_sensitivity(case9, bus)
        assert result["agree"] and result["relative_difference"] <= FD_REL_TOLERANCE
        assert result["dv_dq_pu_per_mvar_jacobian"] > 0


def test_a_voltage_controlled_bus_has_no_dv_dq(case9: pp.pandapowerNet) -> None:
    with pytest.raises(UnsupportedModel, match="not a PQ bus"):
        vq_sensitivity(case9, int(case9.gen.bus.iloc[0]))


def test_an_inductive_network_has_a_positive_qv_mode(case9: pp.pandapowerNet) -> None:
    mode = qv_modal(case9)
    assert mode["min_eigenvalue"] > 0 and not mode["warnings"]


@requires_snem
def test_snem_sa_negative_mode_is_flagged_at_its_series_compensated_lines(snem_net: pp.pandapowerNet) -> None:
    mode = qv_modal(snem_net)
    assert mode["min_eigenvalue"] < 0
    assert "negative-reactance lines [293, 294]" in mode["warnings"][0] or "[294, 293]" in mode["warnings"][0]
