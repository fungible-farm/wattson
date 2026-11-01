"""Qualify actual model-to-MCP tool use with a durable controller-owned journal.

This is a bounded interface probe, not a scored benchmark. Only the supplied
prompt is exposed to the model; the controller never executes model Python.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import httpx

from wattson.inference import provenance
from wattson.mcp_env import server_environment
from wattson.inference.client import InferenceClient

# Qualification budgets, not release benchmark parameters.
MAX_TURNS: int = 12
MAX_TOOL_CALLS: int = 24
# 2048 truncated 32 of 76 Qwen3.5-4B base attempts mid-reasoning (r2/r3,
# finish_reason=length; AGENTS.md FM-2). Reasoning is not carried between
# turns, so this bounds one response, not the conversation.
MAX_TOKENS: int = 8192
# One full response at the ~14 tok/s low end measured on GB10 takes ~585 s;
# with concurrent requests sharing the GPU allow ~3x that. A request that still
# exceeds it is that attempt's terminal ``inference_timeout`` (FM-6): r4's
# Granite side-by-side stage decoded ~4 tok/s per stream and one timeout
# aborted the whole stage. Every compared run records this value, and
# compare_agents refuses runs whose values differ.
INFERENCE_TIMEOUT_S: float = 1800.0
# How the first turn may choose to call a tool (``--first-turn-tool-choice``).
#
# The problem: an agent that answers before calling the solver can only be
# guessing (the first r6 Qwen student invented report numbers that way). The
# provenance check (provenance.py) fails such answers after the fact;
# "required" makes the first turn unable to be an answer at all. Later turns
# always use "auto" so the model can still answer once it has solver results.
#
# - "auto": the model decides (the r1-r6 protocol, and every r6 comparison).
# - "required": OpenAI tool_choice="required" on turn 0. vLLM 0.31 builds the
#   grammar itself, per request, from that request's own tools: its qwen3_coder
#   tool parser asks xgrammar for the model-native structural tag (Qwen XML,
#   which both students were trained on). Nothing is generated or stored here.
#   Measured 2026-10-22 on the v1 Qwen student: the forced reply was
#   "</think><tool_call>\n<function=run_study_trial>..." in the trained XML;
#   vLLM's grammar drops the two newlines Qwen otherwise writes after </think>.
#   Argument values are also grammar-checked when the server runs with
#   --tool-strict-level parameter (run_agent_study's forced arm does).
#
# "required" is a separate study arm: compare_agents refuses to compare it with
# "auto" runs unless the comparison is that arm's own.
FIRST_TURN_TOOL_CHOICES: tuple[str, ...] = ("auto", "required")
# The evaluation's system turn. Training trajectories (wattson/datasets/
# trajectories.py) reuse it verbatim, so students train on the prompt they are
# evaluated with (AGENTS.md FM-19).
SYSTEM_PROMPT: str = ("Use the available tools to solve the task. Report only results supported by actual "
                      "tool responses. Convergence and physical validity are different.")
# vLLM's 400 text when prompt + max_tokens exceeds --max-model-len.
CONTEXT_OVERFLOW: str = "maximum context length"


# Runtime enforcement of "the model never does arithmetic" (opt-in, so published results describe the unguarded harness). A final answer
# is checked with the scorer's own provenance rule; a number no tool produced sends the model back to compute it, at most this many times,
# after which the attempt ends as ``guard_refused`` and no answer is emitted.
MAX_GUARD_RETRIES: int = 2
GUARD_MESSAGE: str = ("Your answer contains numbers that no tool produced: {numbers}. Do not work numbers out yourself. "
                      "Use the run_python tool to compute any value you need from solver results, then answer again using only numbers "
                      "that a tool printed or returned.")


# The guard is stricter than the scorer on purpose: over-strict costs a retry, over-lenient breaks the guarantee. It reads a number glued to
# letters ("0.8654pu", "707kV", which the scorer's pattern skips) and refuses numbers written as words ("seven hundred and seven",
# "zero point eight"): those cannot be traced to a tool reply by value. A bare "two" or "one" in prose is not a number claim and is left alone.
GUARD_NUMBER: re.Pattern[str] = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")
_DIGIT_WORDS: str = "zero|one|two|three|four|five|six|seven|eight|nine"
WORDED_NUMBER: re.Pattern[str] = re.compile(rf"\b(?:hundred|thousand|million|billion)\b|\bpoint\s+(?:{_DIGIT_WORDS})\b", re.IGNORECASE)


def untraced_numbers(answer: str, events: list[dict[str, Any]], prompt: str) -> list[str]:
    """The numbers in ``answer`` that neither the prompt nor any tool reply so far contains, including numbers glued to units or written as words."""
    digits = provenance.check(answer, events, prompt, pattern=GUARD_NUMBER)["untraced"]
    return digits + [match.group(0) for match in WORDED_NUMBER.finditer(answer)]


def record(stream: TextIO, kind: str, payload: Any) -> None:
    """Persist each observation before the next model or tool invocation."""
    stream.write(json.dumps({"kind": kind, "payload": payload}, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


async def probe(args: argparse.Namespace, journal: TextIO) -> int:
    """Let the real model choose tools and feed back actual MCP responses."""
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "wattson.inference.mcp_server"] + (["--study-tools"] if args.study_tools else [])
        + (["--namespace-tools"] if getattr(args, "namespace_tools", False) else [])
        + (["--python-tools"] if getattr(args, "python_tools", False) else [])
        + (["--python-report-fields"] if getattr(args, "python_report_fields", False) else []),
        cwd=str(Path(__file__).resolve().parents[3]),
        env=server_environment(),
    )
    first_choice = getattr(args, "first_turn_tool_choice", "auto")
    # Evaluation is greedy (0.0). G3's GRPO rollouts sample (training/grpo.py) and ask vLLM for
    # the exact prompt and completion token ids, so the update trains on what the policy saw and
    # produced rather than a re-rendered conversation (FM-22/FM-23). Both default off.
    temperature = float(getattr(args, "temperature", 0.0))
    extra = {"return_token_ids": True} if getattr(args, "return_token_ids", False) else None
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": args.prompt.read_text()},
    ]
    record(journal, "start", {
        "model": args.model, "base_url": args.base_url, "messages": messages,
        "max_tokens": MAX_TOKENS, "temperature": temperature,
        "max_turns": MAX_TURNS, "max_tool_calls": MAX_TOOL_CALLS,
        "inference_timeout_s": INFERENCE_TIMEOUT_S,
        "first_turn_tool_choice": first_choice,
        "benchmark_claim": False,
    })
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        discovered = (await session.list_tools()).tools
        tools = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.inputSchema,
        }} for t in discovered]
        record(journal, "tools", tools)
        calls = 0
        ids: set[str] = set()
        events: list[dict[str, Any]] = []  # tool responses so far, in the journal's shape, for the guard
        guard_attempts = 0
        enforce = bool(getattr(args, "enforce_provenance", False))
        with InferenceClient(args.base_url, timeout=INFERENCE_TIMEOUT_S) as client:
            for turn in range(MAX_TURNS):
                try:
                    result = await asyncio.to_thread(
                        client.chat_completion, args.model, messages,
                        max_tokens=MAX_TOKENS, temperature=temperature, tools=tools,
                        tool_choice=first_choice if turn == 0 else "auto", extra=extra,
                    )
                except httpx.HTTPStatusError as error:
                    # The conversation outgrew the server's context: this
                    # attempt's failure, not a backend fault (AGENTS.md FM-5).
                    if error.response.status_code == 400 and CONTEXT_OVERFLOW in error.response.text:
                        record(journal, "terminal", {"state": "context_exhausted", "actual_tool_calls": calls,
                                                     "error": error.response.text[:500]})
                        return 1
                    raise
                except httpx.TimeoutException as error:
                    # One slow attempt is that attempt's failure; the client
                    # closes the connection, so vLLM aborts the request.
                    record(journal, "terminal", {"state": "inference_timeout", "actual_tool_calls": calls,
                                                 "timeout_s": INFERENCE_TIMEOUT_S, "error": type(error).__name__})
                    return 1
                record(journal, "model_response", result)
                choice = result["choices"][0]
                message = choice["message"]
                messages.append({k: message[k] for k in ("role", "content", "tool_calls") if k in message})
                requested = message.get("tool_calls") or []
                if not requested:
                    success = choice["finish_reason"] == "stop" and bool(message.get("content"))
                    if enforce and success:
                        untraced = untraced_numbers(message["content"], events, messages[1]["content"])
                        if untraced and guard_attempts < MAX_GUARD_RETRIES:
                            guard_attempts += 1
                            record(journal, "guard", {"action": "retry", "attempt": guard_attempts, "untraced": untraced})
                            messages.append({"role": "user", "content": GUARD_MESSAGE.format(numbers=", ".join(untraced))})
                            continue
                        if untraced:
                            record(journal, "guard", {"action": "refused", "attempt": guard_attempts, "untraced": untraced})
                            record(journal, "terminal", {"state": "guard_refused", "actual_tool_calls": calls, "accuracy_claim": False})
                            return 1
                    record(journal, "terminal", {
                        "state": "answered" if success else "incomplete_generation",
                        "actual_tool_calls": calls, "accuracy_claim": False,
                    })
                    return 0 if success else 1
                for call in requested:
                    if calls >= MAX_TOOL_CALLS or call["id"] in ids:
                        record(journal, "terminal", {"state": "invalid_tool_protocol", "actual_tool_calls": calls})
                        return 1
                    ids.add(call["id"])
                    record(journal, "tool_request", call)
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                    except (ValueError, TypeError) as error:
                        record(journal, "terminal", {"state": "invalid_tool_arguments", "actual_tool_calls": calls, "error": str(error)})
                        return 1
                    started = time.monotonic()
                    response = await session.call_tool(call["function"]["name"], arguments)
                    observed = response.model_dump(mode="json", by_alias=True)
                    # tool_seconds is journal-only: the message below carries the result alone, so
                    # wall-clock time never reaches the model (FM-3). The error ledger costs it (PC-REQ-4).
                    record(journal, "tool_response", {"tool_call_id": call["id"], "result": observed,
                                                      "tool_seconds": time.monotonic() - started})
                    events.append({"kind": "tool_response", "payload": {"tool_call_id": call["id"], "result": observed}})
                    calls += 1
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(observed, allow_nan=False)})
    record(journal, "terminal", {"state": "turn_budget_exhausted", "actual_tool_calls": calls})
    return 1


def main() -> int:
    """Require an explicit prompt and refuse overwriting earlier observations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--study-tools", action="store_true")
    # The Python shell the v11 students were trained on; the evaluator passes the same flags (backlog 0041).
    parser.add_argument("--python-tools", action="store_true", help="expose run_python; run_study_trial then answers reports only")
    parser.add_argument("--python-report-fields", action="store_true", help="with --python-tools: the v2 shell (report numbers in trial replies)")
    parser.add_argument("--enforce-provenance", action="store_true",
                        help="refuse a final answer containing a number no tool produced: send the model back to compute it (see MAX_GUARD_RETRIES)")
    args = parser.parse_args()
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    with args.journal.open("x") as journal:
        try:
            return asyncio.run(probe(args, journal))
        except Exception as error:
            record(journal, "controller_error", {
                "type": type(error).__name__, "message": str(error),
            })
            raise


if __name__ == "__main__":
    raise SystemExit(main())
