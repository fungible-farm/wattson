"""The RL reward: built from the graders, it pays for verified answers and never for made-up numbers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from wattson.datasets import generate, trajectories
from wattson.datasets.procedural import PROCEDURAL_SEED, discover_load_scale_buses
from wattson.inference import powermcp as pm
from wattson.training import rl_reward

CASE: str = "snem-sa-2000"
needs_case = pytest.mark.skipif(not (pm.REPO_ROOT / pm.CASE_FILES[CASE]).exists(), reason="needs data/")


@pytest.fixture(scope="module")
def fit_task() -> dict:
    elements = discover_load_scale_buses(CASE)[:3]
    return generate._gen_fit_example("pf-load-scale-fit", CASE, elements, 0, PROCEDURAL_SEED, generate.EndpointCache())


@needs_case
def test_the_scripted_bisection_earns_one_minus_its_call_cost(fit_task: dict, tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    journals.mkdir()
    trajectory, reason = asyncio.run(trajectories.build(fit_task, journals, None))
    assert reason == "kept"
    events = [json.loads(line) for line in next(journals.glob("*.jsonl")).read_text().splitlines()]
    answer = trajectory["messages"][-1]["content"]
    reference = rl_reward.bisection_calls(fit_task)
    result = rl_reward.reward(fit_task, events, answer, completed=True, reference_calls=reference)
    assert result["passed"] and not result["fabricated"]
    assert result["tool_calls"] == reference and result["calls_vs_bisection"] == 0
    assert result["reward"] == pytest.approx(1.0 - rl_reward.CALL_COST * reference)


@needs_case
def test_a_made_up_answer_is_penalised_and_never_pays(fit_task: dict) -> None:
    result = rl_reward.reward(fit_task, [], "1.037500", completed=True)
    assert result["fabricated"] and not result["passed"] and result["reward"] == -rl_reward.FABRICATION_PENALTY


def test_an_unfinished_attempt_earns_nothing_but_still_pays_for_its_calls() -> None:
    task = {"subfamily": "pf-load-scale-fit", "task": {"inputs": {"case": CASE}, "prompt": ""}}
    events = [{"kind": "tool_request", "payload": {"function": {"name": "run_study_trial", "arguments": "{}"}}}] * 3
    result = rl_reward.reward(task, events, "", completed=False)
    assert result["reward"] == pytest.approx(-3 * rl_reward.CALL_COST) and not result["fabricated"]
