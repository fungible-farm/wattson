"""WLS state estimation (PC-REQ-014): close to the truth, bad data caught, unobservable refused."""

from __future__ import annotations

import pandapower as pp
import pandapower.networks as pn
import pytest

from wattson.engineering.state_estimation import MEASUREMENT_STD, estimate_state, synthesize_measurements

# Seed of the synthetic measurement noise, so the set is the same every run.
SEED: int = 1
# A gross error of 50 standard deviations: far outside noise, as a failed transducer would be.
GROSS_ERROR_SIGMAS: float = 50.0


@pytest.fixture
def case9() -> pp.pandapowerNet:
    net = pn.case9()
    pp.runpp(net)
    return net


def test_a_clean_estimate_is_within_three_sigma_of_the_truth(case9: pp.pandapowerNet) -> None:
    result = estimate_state(case9, synthesize_measurements(case9, SEED))
    assert result["observable"] and not result["bad_data_suspected"] and not result["removed_as_bad"]
    assert result["max_abs_voltage_error_pu"] < 3 * MEASUREMENT_STD["v"]
    assert result["proof"]["feasibility_status"] == "feasible"


def test_a_gross_error_is_detected_and_exactly_that_measurement_removed(case9: pp.pandapowerNet) -> None:
    measurements = synthesize_measurements(case9, SEED)
    target = next(i for i, m in enumerate(measurements) if m["meas_type"] == "p" and m["element_type"] == "line")
    measurements[target] = {**measurements[target], "value": measurements[target]["value"] + GROSS_ERROR_SIGMAS * MEASUREMENT_STD["p"]}
    result = estimate_state(case9, measurements)
    assert result["bad_data_suspected"]
    assert result["removed_as_bad"] == [measurements[target]]


def test_an_unobservable_set_is_refused_not_estimated(case9: pp.pandapowerNet) -> None:
    voltages = [m for m in synthesize_measurements(case9, SEED) if m["meas_type"] == "v"][:2]
    result = estimate_state(case9, voltages)
    assert not result["observable"] and result["max_abs_voltage_error_pu"] is None
    assert result["proof"]["feasibility_status"] == "indeterminate"
