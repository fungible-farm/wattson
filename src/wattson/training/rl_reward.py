"""Reward for G3's verifier-guided RL, built only from the existing graders (backlog 0017).

0017: "Rewards from the existing grader: grade(), provenance, and a tool-call cost. Unsupported
conclusions are penalised." For one attempt (task, journal, final answer):

- **+1** if the answer passes the solver re-check (``domain_scorecard.score_numerical``), the
  field-level provenance check (``provenance.check_fields``, v3) and the tool-evidence check
  (``evaluate_agent.relevant_evidence``);
- **-1** (``FABRICATION_PENALTY``) if it is a final answer with any number not traced to the
  attempt's tools: making a number up must never pay, even when it happens to be right;
- **-CALL_COST per tool call**, so of two correct attempts the cheaper one scores higher.

``CALL_COST`` x the evaluation's 24-call budget = 0.48 < 1, so a correct answer always outscores
any incorrect one, whatever it costs. The breakdown also reports the calls the trained bisection
procedure needs for the same task (:func:`bisection_calls`, replayed through the real study tool),
which is 0017's pilot metric: calls to reach tolerance against bisection.
"""

from __future__ import annotations

from typing import Any, TypedDict

from wattson.inference.domain_scorecard import score_numerical
from wattson.inference.evaluate_agent import relevant_evidence
from wattson.inference.provenance import check_fields
from wattson.inference.tool_probe import MAX_TOOL_CALLS

CALL_COST: float = 0.02
FABRICATION_PENALTY: float = 1.0
SCALE_DECIMALS: int = 6  # the trajectories' bisection rounds midpoints to 6 decimals
TRIAL_MAX_ITER: int = 50  # trajectories.TRIAL_MAX_ITER
assert CALL_COST * MAX_TOOL_CALLS < 1.0, "a correct answer must outscore any incorrect one"


class RewardBreakdown(TypedDict):
    """One attempt's reward and its parts."""

    reward: float
    passed: bool
    fabricated: bool
    tool_calls: int
    bisection_calls: int | None
    calls_vs_bisection: int | None  # attempt's calls minus bisection's; negative is better


def bisection_calls(task: dict[str, Any]) -> int:
    """Calls the trained procedure makes on a load-scale fit: both ends, then bisect to tolerance.

    Replays ``trajectories.fit`` through ``studies.run_study_trial`` (no MCP, same solver).

    Raises:
        ValueError: for a task that is not a load-scale fit, or a bracket that does not hold.
    """
    import re

    from wattson.inference.studies import run_study_trial

    if task["subfamily"] != "pf-load-scale-fit":
        raise ValueError("bisection_calls replays load-scale fits only")
    inputs = task["task"]["inputs"]
    bus = int(re.findall(r"\d+", inputs["element"])[-1])
    calls = 0

    def measure(scale: float) -> float:
        nonlocal calls
        calls += 1
        data = run_study_trial(network_id=inputs["case"], operation="load_scale", scale=scale,
                               observation="bus_voltage", observation_id=bus, max_iter=TRIAL_MAX_ITER,
                               contingency_line=inputs.get("contingency_line")).data
        return float(data["metric"]) - inputs["target"]

    lo, hi = inputs["scale_lo"], inputs["scale_hi"]
    f_lo, f_hi = measure(lo), measure(hi)
    if f_lo * f_hi > 0:
        raise ValueError("target not bracketed by the interval ends")
    best = min((f_lo, lo), (f_hi, hi), key=lambda p: abs(p[0]))
    while abs(best[0]) > inputs["tolerance"]:
        mid = round((lo + hi) / 2, SCALE_DECIMALS)
        f_mid = measure(mid)
        best = (f_mid, mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return calls


def reward(task: dict[str, Any], events: list[dict[str, Any]], answer: str, completed: bool,
           reference_calls: int | None = None) -> RewardBreakdown:
    """Score one attempt (see the module docstring)."""
    calls = sum(1 for e in events if e["kind"] == "tool_request")
    fields = check_fields(task, answer, events) if completed and answer else None
    fabricated = bool(fields is not None and not fields["traced"])
    passed = bool(completed and answer and not fabricated and score_numerical(task, answer)[0]
                  and relevant_evidence(task, answer, events))
    value = (1.0 if passed else 0.0) - (FABRICATION_PENALTY if fabricated else 0.0) - CALL_COST * calls
    return RewardBreakdown(reward=value, passed=passed, fabricated=fabricated, tool_calls=calls,
                           bisection_calls=reference_calls,
                           calls_vs_bisection=None if reference_calls is None else calls - reference_calls)
