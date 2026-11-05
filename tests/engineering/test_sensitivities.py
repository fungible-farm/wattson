"""PTDF/LODF agree with finite-difference and outage DC re-solves; bridges are named."""

from __future__ import annotations

import numpy as np
import pandapower as pp

from tests.engineering.conftest import requires_snem
from wattson.engineering.sensitivities import (
    LODF_FLOW_ABS_TOLERANCE_MW,
    PTDF_ABS_TOLERANCE,
    bridge_outage_islands,
    compute_factors,
    cross_check_lodf,
    cross_check_ptdf,
)

# The independently built Bbus must equal pandapower's makeBdc to round-off; 1e-9 pu is
# far above float64 noise on susceptances up to ~1e5 pu (snem-sa-2000) and far below
# any real modelling difference.
MODEL_AGREEMENT_PU: float = 1e-9


def _check_case(net: pp.pandapowerNet) -> None:
    """Shared assertions: residuals, both re-solve cross-checks, bridge handling."""
    factors = compute_factors(net)
    residuals = factors["proof"]["equation_residuals"]
    assert residuals["independent_bbus_vs_makeBdc_pu"] <= MODEL_AGREEMENT_PU
    ptdf = cross_check_ptdf(net, factors)
    lodf = cross_check_lodf(net, factors)
    assert len(ptdf["probes"]) >= 5 and ptdf["within_tolerance"], ptdf
    assert len(lodf["probes"]) >= 5 and lodf["within_tolerance"], lodf
    assert ptdf["max_abs_error"] <= PTDF_ABS_TOLERANCE and lodf["max_abs_error"] <= LODF_FLOW_ABS_TOLERANCE_MW
    assert factors["conditioning"]["reduced_bbus_condition_number"] >= 1.0
    bridges = factors["bridge_branch_ids"]
    assert factors["conditioning"]["bridge_branches"] == len(bridges)
    if bridges:
        k = factors["branch_ids"].index(bridges[0])
        assert np.isnan(factors["lodf"][:, k]).all()
        assert bridge_outage_islands(net, bridges[0]), "a bridge outage must island buses"
        assert any("bridge" in s for s in cross_check_lodf(net, factors, outages=[bridges[0]])["skipped"])


def test_case30_factors_match_dc_re_solves(case30: pp.pandapowerNet) -> None:
    """IEEE 30-bus: PTDF and LODF agree with re-solves well inside tolerance."""
    _check_case(case30)


def test_a_non_bridge_outage_keeps_every_bus_energized(case30: pp.pandapowerNet) -> None:
    """The bridge classification is not over-broad: a meshed outage islands nothing."""
    factors = compute_factors(case30)
    meshed = next(b for b in factors["branch_ids"] if b not in set(factors["bridge_branch_ids"]))
    assert bridge_outage_islands(case30, meshed) == []


@requires_snem
def test_snem_sa_2000_factors_match_dc_re_solves(snem_net: pp.pandapowerNet) -> None:
    """CSIRO SA case: same checks on 503 buses x 698 branches, lines and transformers."""
    _check_case(snem_net)
    factors = compute_factors(snem_net)
    probed = {p["element"].split()[0] for p in cross_check_lodf(snem_net, factors)["probes"]}
    assert probed == {"line", "trafo"}
