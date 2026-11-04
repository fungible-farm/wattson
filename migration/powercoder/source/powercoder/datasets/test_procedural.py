"""N-1 and free-text pool tasks are checked on the network they pose, and say everything they need."""

from __future__ import annotations

import re

import pytest

from powercoder.datasets import generate, procedural
from powercoder.distillation.filter import _check_fit
from powercoder.inference.studies import run_study_trial

CASE: str = "ieee39"  # pandapower built-in: no data/ needed, and held out of every pool


def test_pool_and_held_out_networks_never_overlap() -> None:
    assert not set(procedural.POOL_NETWORKS) & set(procedural.HELD_OUT_NETWORKS)
    assert "snem-1803" in procedural.HELD_OUT_NETWORKS


@pytest.fixture(scope="module")
def n1_row() -> dict:
    elements = procedural.discover_load_scale_buses(CASE)
    line = procedural.safe_outages(CASE)[0]
    for i in range(20):
        row = generate._gen_fit_example("pf-load-scale-fit", CASE, elements, i, procedural.PROCEDURAL_SEED,
                                        generate.EndpointCache(), contingency_line=line)
        if row["lifecycle"]["status"] == "verified":
            return row
    pytest.skip("no verified N-1 fit drawn")


def test_an_n1_fit_is_checked_with_its_outage_applied(n1_row: dict) -> None:
    inputs = n1_row["task"]["inputs"]
    scale = n1_row["expected"]["structured"]["scale_factor"]
    passed, evidence, _ = _check_fit(n1_row, {"scale_factor": scale})
    assert passed and f"line {inputs['contingency_line']} out of service" in n1_row["task"]["prompt"]
    bus = int(inputs["element"].split()[-1])
    tool = run_study_trial(network_id=CASE, operation="load_scale", scale=scale, observation="bus_voltage",
                           observation_id=bus, max_iter=50, contingency_line=inputs["contingency_line"])
    intact = run_study_trial(network_id=CASE, operation="load_scale", scale=scale, observation="bus_voltage",
                             observation_id=bus, max_iter=50)
    assert evidence["metric"] == pytest.approx(tool.data["metric"], abs=1e-9)
    assert evidence["metric"] != pytest.approx(intact.data["metric"], abs=1e-9), "the outage must matter"


def test_a_free_text_prompt_states_every_quantity_the_fit_needs(n1_row: dict) -> None:
    inputs = n1_row["task"]["inputs"]
    for template in procedural.FREE_TEXT_TEMPLATES:
        prompt = procedural.free_text(n1_row, template)
        for needed in (CASE, f"line {inputs['contingency_line']}", inputs["element"].split()[-1],
                       f"{inputs['target']:.6f}", str(inputs["tolerance"]), str(inputs["scale_lo"]), str(inputs["scale_hi"])):
            assert needed in prompt, (needed, prompt)
        assert re.search(r"\bevery load|all demand", prompt), "what is scaled must be explicit"


@pytest.fixture(scope="module")
def operating_point_row() -> dict:
    lines = procedural.discover_rating_lines(CASE)
    for i in range(20):
        row = generate._gen_fit_example("pf-line-rating-fit", CASE, lines, i, procedural.PROCEDURAL_SEED,
                                        generate.EndpointCache(), load_level=1.08)
        if row["lifecycle"]["status"] == "verified":
            return row
    pytest.skip("no verified operating-point fit drawn")


def test_an_operating_point_fit_is_checked_and_evidenced_at_its_load_level(operating_point_row: dict) -> None:
    import json

    from powercoder.inference.evaluate_agent import relevant_evidence

    row, inputs = operating_point_row, operating_point_row["task"]["inputs"]
    scale = row["expected"]["structured"]["scale_factor"]
    passed, evidence, _ = _check_fit(row, {"scale_factor": scale})
    assert passed and "every load at 1.08 times its base value" in row["task"]["prompt"]
    line = int(inputs["element"].split()[-1])
    arguments = {"network_id": CASE, "operation": "line_rating", "scale": scale, "element_id": line,
                 "observation": "line_loading", "observation_id": line, "max_iter": 50}
    at_level = run_study_trial(**arguments, load_level=1.08)
    at_base = run_study_trial(**arguments)
    assert evidence["metric"] == pytest.approx(at_level.data["metric"], abs=1e-9) != pytest.approx(at_base.data["metric"], abs=1e-9)

    def events(result) -> list[dict]:
        text = json.dumps({"status": result.status.value, "data": result.data})
        return [{"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": text}]}}}]

    answer = f"{scale:.6f}"
    assert relevant_evidence(row, answer, events(at_level))
    assert not relevant_evidence(row, answer, events(at_base)), "base-load evidence must not count"
    with pytest.raises(ValueError, match="already scales every load"):
        generate._gen_fit_example("pf-load-scale-fit", CASE, procedural.discover_load_scale_buses(CASE), 0,
                                  procedural.PROCEDURAL_SEED, generate.EndpointCache(), load_level=1.04)
