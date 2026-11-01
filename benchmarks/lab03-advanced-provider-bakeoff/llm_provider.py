#!/usr/bin/env python3
"""Real-LLM provider for Lab 3's bake-off (NEM WattsOn mission, M03).

This module wires a *real* local LLM -- served through the llama-swap
OpenAI-compatible gateway (see wattson/inference/client.py) -- into Lab 3's
task-family harness, filling the exact "propose the next trial value" role that
the deterministic local-policy-A/B/C policies stand in for in
orchestrator.py's module docstring.

Why this exists (the M03 gap, named in orchestrator.py's module docstring):
docs/VISION.md's Lab 3 swaps three *live* local LLMs through a single
inference gateway and scores them against the same task-family x provider
matrix. The deterministic policies prove the harness is real (every trial is a
genuine pandapower.runpp() call), but they are closed-form searches, not model
weights. This module closes that gap for at least one real model: the LLM
proposes each trial value, the harness evaluates it with the real solver, and
the scorecard row carries the model's real name, its real token usage, and a
full per-trial provenance log.

Sandbox note (documented, not silently swapped -- docs/DEFINITION_OF_DONE.md):
  - The LLM is reached over HTTP at DEFAULT_BASE_URL (the llama-swap gateway),
    the mission's declared API boundary. There is no in-process model and no
    cloud API key; if the gateway is down, InferenceClient raises and this
    module fails loudly (it does not fall back to a deterministic policy and
    label the result as the model's).
  - The model's proposal is the only thing that drives the search. The one
    place this module acts without the model is a *parse-failure fallback*: if
    the model's reply cannot be parsed as a number after LLM_MAX_PARSE_RETRIES
    nudges, the trial falls back to the midpoint of the current bracket (the
    same move bisection would make). That trial is flagged `adjusted=True` in
    the provenance log, so a reader can see exactly which trials the model
    actually drove. No fitted value is ever hard-coded.

Every constant below is named and sourced per this repo's "no undocumented
magic numbers" rule.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from wattson.gridfit import FitIteration, FitResult  # noqa: E402
from wattson.inference.client import InferenceClient  # noqa: E402

# ---------------------------------------------------------------------------
# Named constants (no undocumented magic numbers).
# ---------------------------------------------------------------------------

# Default model: the M01 teacher, served locally through llama-swap. Configurable
# per-run via orchestrator.py's --model flag / POWERCODER_LLM_MODEL env var, so
# the same harness can score other locally served models without code changes.
DEFAULT_LLM_MODEL: str = "qwen3.8-flash-next"

# The llama-swap gateway: the mission's declared API boundary (MISSION.md
# invariant "llama-swap is the API boundary"). Localhost only, no cloud key.
DEFAULT_BASE_URL: str = "http://127.0.0.1:8000"

# Greedy decoding: the repo values reproducibility (fixed seeds for the
# deterministic policies); temperature 0 makes the LLM's proposals as
# reproducible as the gateway allows. Not a "hard-coded answer" -- the model
# still chooses every value.
LLM_TEMPERATURE: float = 0.0

# Completion cap per proposal. Both locally served teacher models are REASONING
# models: they write their working into `reasoning_content` and the final number
# into `content`. Measured on this gateway with the secant-formula prompt:
#   - qwen3.8-flash-next: ~366 completion tokens (finish_reason=stop)
#   - qwen3.8-27b-uncensored: ~492 completion tokens (finish_reason=stop)
# A 64-token cap truncates the reasoning before it reaches the answer, leaving
# `content` empty (which is why every trial silently fell back to the bracket
# midpoint). 1024 covers the verbose 27B answer (~492-587) with headroom for
# the harder task families and for the model's non-deterministic verbosity: at
# temperature=0.0 the same prompt still occasionally rambles past 640 tokens
# (measured: a trial-2 prompt truncated at 640 three times in a row, then
# completed at 587 on a re-run). Concise answers stop early at finish_reason=
# stop, so the bigger cap costs nothing when the model is brief -- it only
# lets a verbose answer finish instead of truncating to an empty `content`.
LLM_MAX_TOKENS: int = 1024

# Per-request HTTP timeout for the gateway. A focused answer is ~366 tokens at
# ~3-4 tok/s ~= 100-120s of generation, and the FIRST call also pays the
# model-load cost (~2 min). 400s covers load + a full answer with margin; the
# InferenceClient default of 120s is too short and ReadTimes out mid-reasoning.
LLM_TIMEOUT_S: float = 400.0

# Hard cap on LLM-driven trials per task family. Sized so even a weaker model
# gets more than enough room to converge: bisection needs ~log2(0.2/0.002) ~= 7
# trials on the tightest family (load-scale-fit, tol 0.002 over a 0.2-wide
# interval) and ~4 on the 0.5-tolerance families; 15 is ~2x the worst case.
LLM_MAX_ITER: int = 15

# How many times to re-ask the model (with a "reply with only a number" nudge)
# before giving up on parsing one trial and falling back to the bracket
# midpoint. 2 retries bounds the worst case at 3 gateway calls per trial.
LLM_MAX_PARSE_RETRIES: int = 2

# Two proposed values closer than this are treated as "the same trial" for the
# repeat-nudge (see _nudge_if_repeated): it prevents the model from stalling by
# re-proposing an already-evaluated value to infinite precision. 1e-4 is far
# below every task family's tolerance (0.002 / 0.5 / 0.5), so a value this
# close to a prior trial carries no new information.
REPEAT_EPSILON: float = 1e-4

# System prompt: fixed role + output contract. Kept separate from the per-trial
# user prompt so the model's output contract never changes mid-search.
#
# Two things here are load-bearing for a reasoning model (measured, not
# assumed):
#   (1) "Keep your reasoning brief" -- without it the model re-derives the
#       interpolation at length and blows the token cap before emitting the
#       number (content came back empty at 512 tokens; with the brief-reasoning
#       instruction it finished at ~366).
#   (2) "A strong first move is linear interpolation (secant method)" -- this
#       is a strategy HINT, not a hard-coded answer: the model still computes
#       the actual value from the measurements it is given, and the hint is
#       direction-agnostic (secant works whether the quantity rises or falls
#       with s). It focuses the reasoning so the answer lands within budget.
# The explicit secant formula is a METHOD, not a hard-coded answer: the model
# still computes the actual value from the measurements it is given, and the
# formula is direction-agnostic (works whether the quantity rises or falls with
# s). Measured, it is what collapses the 27B teacher's reasoning from 1800+
# tokens (still mid-calculation at 768) to ~492 tokens with a clean numeric
# answer (finish_reason=stop). Without it, the verbose reasoner re-derives and
# re-verifies the interpolation at length and blows the token cap before
# emitting the number.
SYSTEM_PROMPT: str = (
    "You are a power-systems engineer fitting one scalar parameter by iterative "
    "measurement. You are given a target and measurements with residuals "
    "(residual = measured - target). Compute the secant root directly: "
    "s = s1 - f1*(s2-s1)/(f2-f1), using the two most informative measurements. "
    "Keep your reasoning to AT MOST 2 short lines. Do not verify, re-derive, or "
    "explore alternatives. Your final answer must be ONLY the number itself -- "
    "a plain floating-point value, e.g. 0.978544 -- on its own, with no units, "
    "no words, no surrounding punctuation, and no explanation. (A \"decimal "
    "number\" means a floating-point value with a fractional part, NOT a number "
    "with one digit after the decimal point.)"
)

# Per-trial user prompt template. {history} is the running trial log (empty on
# the first trial, which then shows only the two endpoint measurements). The
# direction of monotonicity is deliberately NOT asserted: it differs per task
# family (load-scale-fit and gen-droop-fit increase with s; line-rating-fit
# decreases, because scaling the rating up lowers the loading percentage), so
# the model must infer it from the endpoint residuals -- exactly what a human
# engineer would do with these two measurements in front of them.
USER_PROMPT_TEMPLATE: str = """\
Choose a scale factor s within [{lo}, {hi}] so the measured quantity equals
target {target}{unit}. The quantity is monotonic in s; infer the direction
from the measurements below.

MEASUREMENTS (residual = measured - target) -- the CURRENT bracket endpoints:
  s={a:.6f} -> {obs_a:.6f}{unit} (res {res_a:+.6f})
  s={b:.6f} -> {obs_b:.6f}{unit} (res {res_b:+.6f})
{history}

A residual of 0.0 is a perfect fit. Propose the NEXT s within [{lo}, {hi}].
Final answer = ONLY the number.
"""

# Nudge appended after a parse failure, to steer a rambling reply back to the
# output contract without changing the underlying question.
PARSE_NUDGE: str = "Reply with ONLY a single decimal number. No other text."


@dataclass
class TrialRecord:
    """One LLM-driven trial with full provenance (M03: provenance recorded).

    `proposed` is the model's parsed value (None if unparseable after all
    retries); `used` is the value actually evaluated by the solver; `adjusted`
    is True whenever `used` differs from the model's own proposal (clamped to
    the interval, nudged off a repeat, or a parse-failure midpoint fallback),
    so a reader can see exactly which trials the model genuinely drove.
    """

    iteration: int
    raw_response: str
    reasoning_content: str
    proposed: Optional[float]
    used: float
    adjusted: bool
    observed: float
    residual: float
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float


class LLMProvider:
    """A real LLM standing in for the "propose the next trial value" role.

    Drop-in compatible with the deterministic search policies in
    orchestrator.py: ``provider(evaluate, target, lo, hi, tol) -> FitResult``,
    where ``evaluate(trial)`` is the harness's real pandapower.runpp() call.
    The LLM proposes each trial; the harness evaluates it; the model never
    sees a physics result it did not cause.

    Token usage and per-trial provenance accumulate on the instance
    (``prompt_tokens`` / ``completion_tokens`` / ``trials``) and are reset at
    the start of every ``__call__``, so the orchestrator can read them back
    after each task family and attribute tokens to the right scorecard row.
    """

    def __init__(
        self,
        model: str = DEFAULT_LLM_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = LLM_TEMPERATURE,
        max_iter: int = LLM_MAX_ITER,
        max_parse_retries: int = LLM_MAX_PARSE_RETRIES,
        timeout: float = LLM_TIMEOUT_S,
        verbose: bool = True,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_iter = max_iter
        self.max_parse_retries = max_parse_retries
        self.verbose = verbose
        # timeout: a reasoning model needs well over the InferenceClient
        # default of 120s (see LLM_TIMEOUT_S) -- pass it through explicitly.
        self.client = InferenceClient(base_url=base_url, timeout=timeout)
        # Per-family accumulators (reset in __call__).
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.trials: list[TrialRecord] = []

    # -- public interface ----------------------------------------------------

    def __call__(
        self,
        evaluate: Callable[[float], float],
        target: float,
        lo: float,
        hi: float,
        tol: float,
    ) -> FitResult:
        """Run the LLM-driven scalar search for one task family.

        Mirrors bisection_fit's contract (gridfit.py): requires the target to
        be bracketed by evaluate(lo) and evaluate(hi) (checked, not assumed),
        and returns a FitResult whose ``converged`` is False if max_iter is
        exhausted without reaching tol.

        Args:
            evaluate: maps a trial value to the observed physical quantity
                (a real pandapower.runpp() call downstream, every time).
            target: the value evaluate(trial) should converge to.
            lo, hi: bounds of the search interval.
            tol: convergence tolerance on abs(observed - target).

        Returns:
            A FitResult with the full iteration log.

        Raises:
            ValueError: if target is not bracketed by evaluate(lo)/evaluate(hi).
            httpx.HTTPError: if the llama-swap gateway is unreachable -- a
                live model is required for this provider, so a dead gateway
                fails loudly instead of silently producing a deterministic row.
        """
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.trials = []
        iterations: list[FitIteration] = []

        obs_lo = evaluate(lo)
        obs_hi = evaluate(hi)
        f_lo = obs_lo - target
        f_hi = obs_hi - target
        if f_lo == 0:
            iterations.append(FitIteration(1, lo, obs_lo, 0.0))
            return FitResult(True, lo, obs_lo, 0.0, iterations)
        if f_hi == 0:
            iterations.append(FitIteration(1, hi, obs_hi, 0.0))
            return FitResult(True, hi, obs_hi, 0.0, iterations)
        if (f_lo > 0) == (f_hi > 0):
            raise ValueError(
                f"LLMProvider: target {target} is not bracketed by "
                f"[{lo}, {hi}] -> observed [{obs_lo}, {obs_hi}]"
            )

        a, b = lo, hi
        fa, fb = f_lo, f_hi
        for i in range(1, self.max_iter + 1):
            trial, record = self._propose(
                a, b, target, lo, hi, fa, fb, iterations
            )
            record.iteration = i
            observed = evaluate(trial)
            residual = observed - target
            iterations.append(FitIteration(i, trial, observed, residual))
            record.observed = observed
            record.residual = residual
            self.trials.append(record)
            self.prompt_tokens += record.prompt_tokens
            self.completion_tokens += record.completion_tokens
            if self.verbose:
                adj = " [adjusted]" if record.adjusted else ""
                print(
                    f"    {self.model} trial {i}: s={trial:.4f} -> "
                    f"{observed:.4f} (residual {residual:+.4f})"
                    f"{adj} [{record.prompt_tokens}+{record.completion_tokens} tok]"
                )
            if abs(residual) <= tol:
                return FitResult(True, trial, observed, residual, iterations)
            # Update the bracket exactly as bisection does, so the fallback
            # midpoint (and any future reasoning) stays inside a straddling pair.
            if (residual > 0) == (fa > 0):
                a, fa = trial, residual
            else:
                b, fb = trial, residual
        last = iterations[-1]
        return FitResult(False, last.trial, last.observed, last.residual, iterations)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()

    # -- internals -----------------------------------------------------------

    def _propose(
        self,
        a: float,
        b: float,
        target: float,
        lo: float,
        hi: float,
        fa: float,
        fb: float,
        iterations: list[FitIteration],
    ) -> tuple[float, TrialRecord]:
        """Ask the model for the next trial value; return (value, provenance).

        Tries the model up to max_parse_retries + 1 times (re-asking with
        PARSE_NUDGE after each unparseable reply). If every attempt fails to
        parse, falls back to the midpoint of the current bracket [a, b] and
        flags the record adjusted=True. A parsed value is clamped to [lo, hi]
        and nudged off any already-tried value (REPEAT_EPSILON), also flagged.
        """
        history_lines = [
            f"  s={it.trial:.6f} -> measured {it.observed:.6f} "
            f"(residual {it.residual:+.6f})"
            for it in iterations
        ]
        history = "\n".join(history_lines) if history_lines else "  (no interior trials yet)"
        # Show the model the CURRENT bracket endpoints (a, b) and their
        # observations, not the fixed interval bounds. As the bracket shrinks,
        # the secant root of the FIXED bounds stops moving (it is the same
        # already-tried value), and the model rambles trying to reconcile that
        # with the history -- which is exactly what blew the token cap on
        # trials 2+. The current bracket is what the model needs to refine its
        # search, and its secant root is always a fresh, untried value.
        obs_a = target + fa
        obs_b = target + fb
        user_prompt = USER_PROMPT_TEMPLATE.format(
            lo=lo,
            hi=hi,
            target=target,
            unit="",
            a=a,
            b=b,
            obs_a=obs_a,
            obs_b=obs_b,
            res_a=fa,
            res_b=fb,
            history=history,
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        proposed: Optional[float] = None
        raw = ""
        reasoning = ""
        prompt_tokens = 0
        completion_tokens = 0
        elapsed = 0.0
        for attempt in range(self.max_parse_retries + 1):
            if attempt > 0:
                messages.append({"role": "user", "content": PARSE_NUDGE})
            resp = self.client.chat_completion(
                self.model,
                messages,
                max_tokens=LLM_MAX_TOKENS,
                temperature=self.temperature,
            )
            choice = resp["choices"][0]
            raw = (choice["message"].get("content") or "").strip()
            # Reasoning models put their working in reasoning_content and the
            # final answer in content; keep both for provenance (a reader can
            # see the model actually derived the value, not that it was guessed).
            reasoning = choice["message"].get("reasoning_content") or ""
            usage = resp.get("usage", {})
            prompt_tokens += int(usage.get("prompt_tokens", 0))
            completion_tokens += int(usage.get("completion_tokens", 0))
            elapsed = float(resp.get("elapsed_seconds", 0.0))
            proposed = self._parse_number(raw)
            if proposed is not None:
                break

        if proposed is None:
            # Parse-failure fallback: the bracket midpoint (bisection's move).
            used = (a + b) / 2.0
            record = TrialRecord(
                iteration=0,  # set by caller via FitIteration index alignment
                raw_response=raw,
                reasoning_content=reasoning,
                proposed=None,
                used=used,
                adjusted=True,
                observed=0.0,
                residual=0.0,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_s=elapsed,
            )
            return used, record

        used = min(max(proposed, lo), hi)
        adjusted = abs(used - proposed) > 1e-12
        used = self._nudge_if_repeated(used, iterations, a, b)
        adjusted = adjusted or abs(used - proposed) > 1e-12
        record = TrialRecord(
            iteration=0,
            raw_response=raw,
            reasoning_content=reasoning,
            proposed=proposed,
            used=used,
            adjusted=adjusted,
            observed=0.0,
            residual=0.0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_s=elapsed,
        )
        return used, record

    @staticmethod
    def _parse_number(text: str) -> Optional[float]:
        """Extract the first numeric token from a model reply, or None.

        Tolerates surrounding prose, commas as thousands separators, and
        scientific notation. Returns None when no number is present -- that
        is what triggers the PARSE_NUDGE retry loop.
        """
        cleaned = text.replace(",", "")
        m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", cleaned)
        if not m:
            return None
        try:
            return float(m.group(0))
        except ValueError:
            return None

    @staticmethod
    def _nudge_if_repeated(
        value: float,
        iterations: list[FitIteration],
        a: float,
        b: float,
    ) -> float:
        """Return `value` unless it repeats an already-tried value.

        A proposal within REPEAT_EPSILON of a prior trial carries no new
        information (it is far below every task family's tolerance), so it is
        replaced by the midpoint of the current bracket -- the same
        information-preserving move bisection makes. The caller flags the
        resulting record adjusted=True.
        """
        for it in iterations:
            if abs(value - it.trial) < REPEAT_EPSILON:
                return (a + b) / 2.0
        return value
