"""Score the scripted procedure as an agent: the "script-as-agent" baseline (gate G0).

Backlog 0008 found that the benchmark cannot say whether a model adds anything
over the deterministic procedure it is trained to imitate; PRD-0010 gate G0
asks for that procedure to be scored on the same suites, with the same grader
and budgets, so every comparison reads "student vs base vs script".

This runs the plain procedure from ``powercoder/datasets/trajectories.py`` --
one ``operation="none"`` trial for reports; both interval ends then bisection
for fits -- through the real MCP stdio server (``mcp_server --study-tools``)
for every task of a frozen suite. Nothing is copied: the procedure
(``report``/``fit``), the journaling ``Conversation`` and the grader
(``evaluate_agent.grade``) are imported. No recovery faults are injected.

Output is a run directory in ``evaluate_agent``'s exact layout (per-task
journals, ``prompts/``, ``results.jsonl`` in suite order, ``manifest.json``,
``summary.json``) that ``compare_agents.verified_run`` accepts. A task the
script cannot complete (target not bracketed, no valid metric, budget
exceeded, unknown subfamily) is journaled with its reason and graded as a
failed attempt; it is never skipped.

Named stand-in (AGENTS.md): the "agent" here is not a model. The budgets
recorded in the manifest are the evaluation's own (so ``compare_agents``
accepts the pairing), but only the turn and tool-call budgets constrain the
script; ``max_tokens``, ``temperature`` and ``inference_timeout_s`` have
nothing to act on.

Usage:
    uv run python -m wattson.inference.script_baseline \\
        --tasks training/unsloth/out/smoke-validation-20-20261022/examples.jsonl \\
        --exclude powercoder/datasets/smoke/splits/train.jsonl \\
        --out training/unsloth/out/script-baseline-smoke20-20261023
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from wattson.datasets import common
from wattson.datasets.audit import audit_rows, load_rows
from wattson.datasets.trajectories import Conversation, fit, report
from wattson.inference.evaluate_agent import TaskResult, frozen_task_count, grade
from wattson.inference.tool_probe import INFERENCE_TIMEOUT_S, MAX_TOKENS, MAX_TOOL_CALLS, MAX_TURNS, SYSTEM_PROMPT, record

# The run's "model" and "agent" names; compare_agents reports ``model`` as the
# compared side's name.
AGENT_NAME: str = "scripted-procedure"
PROCEDURE: str = ("wattson.datasets.trajectories plain procedure (no recovery faults): pf-report -> one "
                  "run_study_trial(operation='none') answered in the reference report wording; fits -> "
                  "run_study_trial at scale_lo and scale_hi, then bisection on a 6-decimal grid until "
                  "|metric - target| <= tolerance (labs/_shared/gridfit.bisection_fit), answering the bare scale")
# Each attempt is its own MCP server process (~one pandapower network in memory)
# and solves are CPU-bound; 4 bounds memory and CPU while the r6b GPU evaluation
# shares this host (task constraint, 2026-10-23). It changes throughput only.
MAX_CONCURRENCY: int = 4
DEFAULT_CONCURRENCY: int = MAX_CONCURRENCY
# Conversation.trial's own budget: every tool call takes a turn and the answer
# needs one more, so at most min(MAX_TOOL_CALLS, MAX_TURNS - 1) calls fit.
TOOL_CALL_BUDGET: int = min(MAX_TOOL_CALLS, MAX_TURNS - 1)
# A scripted turn processes no model tokens. failure_modes.classify reads
# ``usage.prompt_tokens`` from every model_response, so the field is present
# and records the true count, zero, rather than an estimate.
SCRIPTED_USAGE: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
# Terminal state for a procedure that stopped without an answer for any reason
# other than the budget (which uses tool_probe's "turn_budget_exhausted").
SCRIPT_FAILED: str = "script_failed"


class ScriptBudgetExhausted(RuntimeError):
    """The procedure needed more tool calls than the evaluation allows."""


class ScriptedConversation(Conversation):
    """``trajectories.Conversation`` with model-shaped usage and a typed budget stop.

    The parent raises a plain ``RuntimeError`` at the budget, indistinguishable
    from "target not bracketed"; the budget is checked here first so it can be
    journaled as tool_probe's own ``turn_budget_exhausted`` state.
    """

    def respond(self, message: dict[str, Any], finish_reason: str) -> None:
        """Record an assistant turn as a chat completion, including (zero) usage."""
        record(self.journal, "model_response", {"choices": [{"index": 0, "message": message,
                                                             "finish_reason": finish_reason}],
                                                "usage": dict(SCRIPTED_USAGE)})
        self.messages.append(message)

    async def trial(self, arguments: dict[str, Any], tool: str = "run_study_trial") -> dict[str, Any]:
        """Make one tool call, refusing a call the evaluation's budget would not allow.

        Raises:
            ScriptBudgetExhausted: before a call past ``TOOL_CALL_BUDGET``.
        """
        if self.calls >= TOOL_CALL_BUDGET:
            raise ScriptBudgetExhausted(f"Procedure needs more than {TOOL_CALL_BUDGET} tool calls")
        return await super().trial(arguments, tool)


async def run_procedure(conversation: ScriptedConversation, row: dict[str, Any]) -> None:
    """Run the procedure and journal its terminal state; never raise for a task failure.

    ``report``/``fit`` raise ``RuntimeError`` (no valid metric, not bracketed),
    ``KeyError`` (an MCP error response has no ``data``; an unknown subfamily)
    or ``TypeError`` (``data`` is null). Each is that attempt's failure, with
    the reason journaled, as tool_probe journals a model's protocol failure.
    """
    try:
        text = await (report if row["subfamily"] == "pf-report" else fit)(conversation, row)
    except ScriptBudgetExhausted as error:
        record(conversation.journal, "terminal", {"state": "turn_budget_exhausted",
                                                  "actual_tool_calls": conversation.calls, "reason": str(error)})
    except (RuntimeError, KeyError, TypeError) as error:
        record(conversation.journal, "terminal", {"state": SCRIPT_FAILED, "actual_tool_calls": conversation.calls,
                                                  "reason": f"{type(error).__name__}: {error}"})
    else:
        conversation.answer(text)


async def attempt(out: Path, row: dict[str, Any], grading: asyncio.Lock) -> TaskResult:
    """Run one task's single scripted attempt in its own MCP server, journal and grade it.

    Mirrors ``evaluate_agent.attempt``: same journal path and prompt file, and
    grading is serialised because it re-solves power flows in this process.
    A transport or server failure is a controller error that stops the run.
    """
    (out / "prompts" / f"{row['example_id']}.txt").write_text(row["task"]["prompt"])
    journal = out / f"{row['example_id']}.jsonl"
    parameters = StdioServerParameters(command=sys.executable,
                                       args=["-m", "wattson.inference.mcp_server", "--study-tools"],
                                       cwd=str(common.REPO_ROOT))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": row["task"]["prompt"]}]
    with journal.open("x") as stream:
        record(stream, "start", {"model": AGENT_NAME, "agent": AGENT_NAME, "base_url": None, "messages": list(messages),
                                 "max_tokens": MAX_TOKENS, "temperature": 0.0, "max_turns": MAX_TURNS,
                                 "max_tool_calls": MAX_TOOL_CALLS, "inference_timeout_s": INFERENCE_TIMEOUT_S,
                                 "first_turn_tool_choice": "auto", "scripted": True, "benchmark_claim": False})
        try:
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
                await session.initialize()
                tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                           "parameters": t.inputSchema}}
                         for t in (await session.list_tools()).tools]
                record(stream, "tools", tools)
                await run_procedure(ScriptedConversation(session, stream, messages, tools), row)
        except Exception as error:
            record(stream, "controller_error", {"type": type(error).__name__, "message": str(error)})
            raise
    async with grading:
        return await asyncio.to_thread(grade, row, journal)


def git_state() -> dict[str, Any]:
    """The commit and dirtiness of the tree whose procedure and grader were run."""
    def git(*command: str) -> str:
        return subprocess.run(["git", *command], capture_output=True, text=True, cwd=common.REPO_ROOT).stdout.strip()

    return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain"))}


async def evaluate(tasks_path: Path, exclude: Path, out: Path, concurrency: int = DEFAULT_CONCURRENCY) -> dict[str, Any]:
    """Audit independence, run every task once, and write an evaluate_agent-format run.

    Returns:
        The summary written to ``summary.json``.

    Raises:
        ValueError: if the suite is not complete, frozen and independent of
            ``exclude``, ``out`` exists, or ``concurrency`` is out of range.
    """
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be in 1..{MAX_CONCURRENCY}")
    if out.exists():
        raise ValueError(f"Refusing to overwrite an earlier run: {out}")
    tasks = load_rows(tasks_path)
    audit = audit_rows(tasks, load_rows(exclude))
    if not audit["clean"] or len(tasks) != frozen_task_count(tasks_path):
        raise ValueError(f"Expected a complete, frozen, independent task suite: {audit}")
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "model": AGENT_NAME, "base_url": None, "agent": AGENT_NAME, "procedure": PROCEDURE,
        "independence_audit": audit,
        "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (tasks_path, exclude)},
        "max_turns": MAX_TURNS, "max_tool_calls": MAX_TOOL_CALLS, "max_tokens": MAX_TOKENS,
        "inference_timeout_s": INFERENCE_TIMEOUT_S,
        "temperature": 0.0, "attempts_per_task": 1, "concurrency": concurrency,
        "first_turn_tool_choice": "auto", "server_tool_strict_level": "auto",
        **git_state(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "prompts").mkdir()
    gate = asyncio.Semaphore(concurrency)
    grading = asyncio.Lock()

    async def bounded(row: dict[str, Any]) -> TaskResult:
        async with gate:
            return await attempt(out, row, grading)

    started = time.monotonic()
    results: list[TaskResult] = []
    pending = [asyncio.create_task(bounded(row)) for row in tasks]
    try:
        with (out / "results.jsonl").open("x") as scored:
            for future in pending:
                result = await future
                record(scored, "result", result)
                results.append(result)
                print(f"{AGENT_NAME}: {len(results)}/{len(tasks)} pass={sum(r['passed'] for r in results)}", flush=True)
    finally:
        for future in pending:
            future.cancel()
    summary = {**manifest, "complete": len(results) == len(tasks), "n_tasks": len(results),
               "n_pass": sum(r["passed"] for r in results),
               "pass_at_1": sum(r["passed"] for r in results) / len(results),
               "g5_met": False, "wall_clock_s": round(time.monotonic() - started, 1)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    """Require a frozen suite, its training exclusion, and a fresh output directory."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"MCP server processes at once (1..{MAX_CONCURRENCY})")
    args = parser.parse_args()
    summary = asyncio.run(evaluate(args.tasks, args.exclude, args.out, args.concurrency))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
