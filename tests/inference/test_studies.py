"""Study-trial responses must not mislead a model about contingencies or status."""

from __future__ import annotations

import pytest

from wattson.inference import powermcp as pm
from wattson.inference.studies import VALID_IDS_SHOWN, base_case_voltage_violations, run_study_trial, tool_schema

# fixture-2bus-ok has a bus 1 load and no voltage violations, so it runs in milliseconds.
NETWORK: str = "fixture-2bus-ok"


def trial(**overrides: object) -> pm.PowerMCPResult:
    """Run one load-scale trial on the 2-bus fixture, observing bus 1."""
    arguments: dict = {"network_id": NETWORK, "operation": "load_scale", "scale": 1.0, "element_id": 1,
                       "observation": "bus_voltage", "observation_id": 1, "max_iter": 20}
    return run_study_trial(**(arguments | overrides))


def test_contingency_line_is_optional_and_the_intact_network_is_named() -> None:
    """Omitting contingency_line studies the intact network and says so (FM-18)."""
    assert "contingency_line" not in tool_schema()["function"]["parameters"]["required"]
    result = trial()
    assert result.data["study"]["contingency_line"] is None
    assert result.data["contingency"] == "none (intact network)"


def test_trial_reports_the_base_case_violations_and_a_valid_metric() -> None:
    """A converged trial carries the unmodified case's violation count (FM-17)."""
    result = trial(scale=0.95)
    assert result.status is pm.ExecutionStatus.SUCCESS
    assert result.data["metric_valid"] is True
    assert result.data["base_case_voltage_violations"] == base_case_voltage_violations(NETWORK) == 0


# snem-sa-2000 line 60: an N-1 outage that isolates buses (FM-25). Measured
# 2026-10-23; the reference solver reports the contingency as converged.
ISOLATING_LINE: int = 60
CASE: str = "snem-sa-2000"


@pytest.mark.skipif(not (pm.REPO_ROOT / pm.CASE_FILES[CASE]).exists(),
                    reason="needs data/ (scripts/fetch_csiro_nem_data.py)")
def test_an_outage_that_isolates_buses_is_a_valid_study_with_them_listed() -> None:
    """De-energized buses are listed and skipped, as the reference solver does, not a failure."""
    net, _ = pm._load_network_object(CASE)
    result = run_study_trial(network_id=CASE, operation="none", scale=1.0, element_id=0, observation="bus_voltage",
                             observation_id=int(min(net.bus.index)), max_iter=50, contingency_line=ISOLATING_LINE)
    assert result.status in {pm.ExecutionStatus.SUCCESS, pm.ExecutionStatus.CONSTRAINT_VIOLATION}
    assert result.data["de_energized_buses"] > 0 and result.data["de_energized_bus_ids"]
    assert result.data["converged"] and result.data["min_bus_voltage_pu"] > 0


def test_an_intact_network_response_has_no_de_energized_fields() -> None:
    """Responses without isolation stay byte-identical to the trained ones."""
    assert "de_energized_buses" not in trial().data


@pytest.mark.skipif(not (pm.REPO_ROOT / pm.CASE_FILES[CASE]).exists(),
                    reason="needs data/ (scripts/fetch_csiro_nem_data.py)")
def test_an_unknown_bus_error_names_valid_ids_to_recover_with() -> None:
    """r6b Qwen student: bus 999 was refused with no valid id in reach, so it guessed 1, 2, 3, ..."""
    net, _ = pm._load_network_object(CASE)
    lowest = sorted(int(bus) for bus in net.bus.index)[:VALID_IDS_SHOWN]
    result = run_study_trial(network_id=CASE, operation="none", scale=1.0, element_id=0, observation="bus_voltage",
                             observation_id=999, max_iter=50, contingency_line=94)
    assert result.status is pm.ExecutionStatus.EXECUTION_FAILURE
    assert f"Unknown observation element 999: the network has {len(net.bus)} buses" in result.error
    assert result.error.endswith("ids are " + ", ".join(map(str, lowest)))
    # The first id named is valid: the recovery the error enables actually works.
    retry = run_study_trial(network_id=CASE, operation="none", scale=1.0, element_id=0, observation="bus_voltage",
                            observation_id=lowest[0], max_iter=50, contingency_line=94)
    assert retry.data["converged"]


def test_unknown_contingency_and_modification_ids_name_valid_ids_too() -> None:
    """Every id the tool can refuse gets the same recoverable message."""
    assert trial(contingency_line=99).error.endswith("contingency line 99: the network has 1 lines; the lowest 1 ids are 0")
    assert "Unknown modification element 7" in trial(operation="line_rating", element_id=7,
                                                      observation="line_loading", observation_id=0).error


def test_a_report_needs_no_invented_element_or_observation() -> None:
    """IR 0.2.0: a network-wide report sends only what it uses, and says it observed nothing."""
    assert tool_schema()["function"]["parameters"]["required"] == ["network_id", "operation", "max_iter"]
    result = run_study_trial(network_id=NETWORK, operation="none", max_iter=20)
    assert result.status != pm.ExecutionStatus.EXECUTION_FAILURE, result.error
    assert result.data["observation"] == "none (network-wide report)"
    assert not {"metric", "metric_unit", "metric_valid"} & set(result.data)
    assert result.data["study"] == {"network_id": NETWORK, "operation": "none", "max_iter": 20, "contingency_line": None}
    full = trial(operation="none", observation_id=1)
    assert full.data["min_bus_voltage_pu"] == result.data["min_bus_voltage_pu"]


@pytest.mark.parametrize(("operation", "missing"), [
    ("load_scale", {"scale"}), ("load_scale", {"observation_id", "observation"}), ("line_rating", {"element_id"})])
def test_a_modification_names_what_it_still_needs(operation: str, missing: set[str]) -> None:
    observation = {"load_scale": "bus_voltage", "line_rating": "line_loading"}[operation]
    arguments = {"network_id": NETWORK, "operation": operation, "scale": 1.05, "element_id": 0,
                 "observation": observation, "observation_id": 0, "max_iter": 20}
    result = run_study_trial(**{k: v for k, v in arguments.items() if k not in missing})
    assert result.status == pm.ExecutionStatus.EXECUTION_FAILURE
    assert all(name in result.error for name in missing - {"observation"}), result.error


def test_load_scale_needs_no_element_id() -> None:
    result = trial(element_id=None)
    assert result.data["metric_valid"] and "element_id" not in result.data["study"]


def test_load_level_sets_the_operating_point_and_is_refused_with_load_scale() -> None:
    """IR 0.3.0: every load times load_level before the study; omitted, nothing changes."""
    base = run_study_trial(network_id="ieee39", operation="none", max_iter=50)
    heavier = run_study_trial(network_id="ieee39", operation="none", max_iter=50, load_level=1.08)
    assert "operating_point" not in base.data and "load_level" not in base.data["study"]
    assert heavier.data["study"]["load_level"] == 1.08 and heavier.data["operating_point"].startswith("every load at 1.08")
    assert heavier.data["min_bus_voltage_pu"] < base.data["min_bus_voltage_pu"]
    refused = trial(load_level=1.04)
    assert refused.status == pm.ExecutionStatus.EXECUTION_FAILURE and "already scales every load" in refused.error
    out_of_range = run_study_trial(network_id="ieee39", operation="none", max_iter=50, load_level=1.5)
    assert out_of_range.status == pm.ExecutionStatus.EXECUTION_FAILURE
