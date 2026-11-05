"""Compare actual study trials to the independent dataset generator's solver path."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from wattson.datasets import generate
from wattson.inference import powermcp as pm
from wattson.inference.studies import run_study_trial

ROOT: Path = Path(__file__).resolve().parents[2]
TRIALS: list[dict] = json.loads((Path(__file__).parent / "fixtures/study_trials.json").read_text())
REJECTED: list[dict] = json.loads((Path(__file__).parent / "fixtures/rejected_study_arguments.json").read_text())


def trial_arguments(row: dict, scale: float) -> dict:
    """Translate a training fixture's public input into one measurement request."""
    inputs = row["inputs"]
    ids = [int(x) for x in re.findall(r"\d+", inputs["element"])]
    operation, observation = {
        "pf-load-scale-fit": ("load_scale", "bus_voltage"),
        "pf-line-rating-fit": ("line_rating", "line_loading"),
        "pf-gen-dispatch-fit": ("gen_dispatch", "trafo_loading"),
    }[row["subfamily"]]
    return {
        "network_id": inputs["case"], "operation": operation, "scale": scale,
        "element_id": ids[0], "observation": observation, "observation_id": ids[-1],
        "contingency_line": None, "max_iter": 20,
    }


@pytest.mark.parametrize("row", TRIALS, ids=lambda row: row["subfamily"])
def test_trials_match_independent_physics_and_do_not_accumulate(row: dict) -> None:
    """Changing and repeating a scale must match fresh independent network copies."""
    inputs = row["inputs"]
    args = trial_arguments(row, inputs["scale_lo"])
    modifier = {
        "load_scale": generate._modifier_load_scale,
        "line_rating": generate._make_modifier_line_rating(args["element_id"]),
        "gen_dispatch": generate._make_modifier_gen_dispatch(args["element_id"]),
    }[args["operation"]]
    reader = {
        "bus_voltage": generate._reader_bus_voltage,
        "line_loading": generate._reader_line_loading,
        "trafo_loading": generate._reader_trafo_loading,
    }[args["observation"]](args["observation_id"])
    independent = generate._make_evaluate(inputs["case"], modifier, reader)
    hashes = []
    for scale in (inputs["scale_lo"], inputs["scale_hi"], inputs["scale_lo"]):
        args["scale"] = scale
        result = run_study_trial(**args)
        assert result.status in {pm.ExecutionStatus.SUCCESS, pm.ExecutionStatus.CONSTRAINT_VIOLATION}
        assert result.data["metric"] == pytest.approx(independent(scale), abs=1e-10)
        assert result.evidence["validity"]["numerical"]
        hashes.append(result.evidence["input_hash"])
    assert hashes[0] == hashes[-1]
    assert hashes[0] != hashes[1]


def test_budget_failure_exposes_no_measurement() -> None:
    """The real CSIRO case cannot converge under a one-iteration budget."""
    args = trial_arguments(TRIALS[0], TRIALS[0]["inputs"]["scale_lo"])
    args["max_iter"] = 1
    result = run_study_trial(**args)
    assert result.status == pm.ExecutionStatus.NON_CONVERGENCE
    assert not result.data
    assert result.evidence is None


@pytest.mark.parametrize("overrides", REJECTED)
def test_reject_invalid_trial_before_loading_network(overrides: dict, monkeypatch) -> None:
    """Type, scope and effort violations must not invoke a physical solver."""
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Invalid request reached network loading")

    monkeypatch.setattr(pm, "_load_network_object", forbidden)
    args = trial_arguments(TRIALS[0], TRIALS[0]["inputs"]["scale_lo"])
    result = run_study_trial(**(args | overrides))
    assert result.status == pm.ExecutionStatus.EXECUTION_FAILURE
    assert not result.data
    assert "Invalid request reached" not in result.error


def test_study_tool_available_only_when_enabled() -> None:
    """Call a real study over SDK transport without changing the default contract."""
    async def check() -> None:
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "wattson.inference.mcp_server", "--study-tools",
        ], cwd=str(ROOT))
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            assert "run_study_trial" in {t.name for t in (await session.list_tools()).tools}
            args = trial_arguments(TRIALS[0], TRIALS[0]["inputs"]["scale_lo"])
            response = await session.call_tool("run_study_trial", args)
            assert not response.isError
            observed = json.loads(response.content[0].text)
            assert observed["data"]["metric"] == run_study_trial(**args).data["metric"]
            args["scale"] = float("inf")
            rejected = await session.call_tool("run_study_trial", args)
            assert rejected.isError

    asyncio.run(check())
