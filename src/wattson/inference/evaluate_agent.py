"""Evaluate every held-out task through real model-selected MCP calls.

The controller exposes only the task prompt, never the reference or grader.
Each task has one greedy attempt with the qualified probe's fixed budgets.
Journals are durable and exclusive; model protocol failures count as failures,
while transport/backend errors stop the run visibly rather than becoming scores.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict

from wattson.datasets.audit import audit_rows, load_rows
from wattson.inference.provenance import observations, check as provenance_check
from wattson.inference.tool_probe import FIRST_TURN_TOOL_CHOICES, INFERENCE_TIMEOUT_S, MAX_TOKENS, MAX_TOOL_CALLS, MAX_TURNS, probe, record
from wattson.training.unsloth.evaluate import FIT_SUBFAMILIES, extract_scale, score_answer

# vLLM 0.31 --tool-strict-level values: "auto" constrains tool calls only when the
# request asks (tool_choice / per-tool strict); "function" always constrains the
# call envelope; "parameter" also enforces each argument's schema.
TOOL_STRICT_LEVELS: tuple[str, ...] = ("auto", "function", "parameter")
# The tool sets an attempt's MCP server can list, as recorded in the manifest.
TOOL_SET_STUDY: str = "study"
TOOL_SET_NAMESPACE: str = "study+namespace"
# Backlog 0041: run_python added, run_study_trial limited to reports.
TOOL_SET_PYTHON: str = "study+python"
# The v2 shell: trial replies also carry the report numbers (backlog 0041).
TOOL_SET_PYTHON2: str = "study+python2"
# One task at a time reproduces the r1-r3 schedule. Tasks are independent
# (own MCP process, own journal), so concurrency changes throughput only.
DEFAULT_CONCURRENCY: int = 1


class TaskResult(TypedDict):
    """Independent numerical correctness and controller evidence for one attempt."""

    example_id: str
    subfamily: str
    answer: str
    numerical_pass: bool
    tool_evidence_pass: bool
    # Every number in the answer traces to this attempt's tool responses or prompt
    # (wattson/inference/provenance.py); an untraced number fails the attempt.
    provenance_pass: bool
    untraced_numbers: list[str]
    passed: bool
    verification: dict[str, Any]
    diagnostics: list[str]
    journal_sha256: str


def frozen_task_count(tasks: Path) -> int:
    """Return the task count a frozen suite's own manifest declares for this exact file.

    Every suite (``build_benchmark.py``'s 306 tasks, ``sample_benchmark.py``'s
    stratified samples) sits beside a ``manifest.json`` recording the file's
    sha256, so a run of a truncated or swapped file is refused.

    Raises:
        ValueError: if the manifest is missing, names another file, or has no count.
    """
    manifest_path = tasks.parent / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"No frozen-suite manifest beside {tasks}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("examples_sha256") != hashlib.sha256(tasks.read_bytes()).hexdigest():
        raise ValueError(f"{tasks} is not the file its manifest froze")
    if "n_tasks" in manifest:
        return int(manifest["n_tasks"])
    if "counts_by_subfamily" in manifest:
        return sum(manifest["counts_by_subfamily"].values())
    raise ValueError(f"{manifest_path} declares no task count")


def relevant_evidence(task: dict[str, Any], answer: str, events: list[dict[str, Any]]) -> bool:
    """Require an actual successful measurement of the requested study.

    For fits the independently checked final scale must itself have been tried.
    A tool call on another case, element, contingency, or scale is insufficient.
    """
    from wattson.distillation.filter import _parse_element

    inputs = task["task"]["inputs"]
    if task["subfamily"] in {"pf-worst-contingency", "pf-derived"}:
        from wattson.datasets.judgement import studied_every_candidate

        return studied_every_candidate(task, events)
    if task["subfamily"] == "pf-unsolvable":
        from wattson.datasets.unsolvable import checked_absent_element

        return checked_absent_element(task, events)
    scale = extract_scale(answer) if task["subfamily"] in FIT_SUBFAMILIES else None
    for observed in observations(events):
        data = observed.get("data") or {}
        study = data.get("study") or {}
        if observed.get("status") not in {"success", "constraint_violation"}:
            continue
        if study.get("network_id") != inputs["case"]:
            continue
        if study.get("contingency_line") != inputs.get("contingency_line"):
            continue
        # The operating point must be the task's too (IR 0.3.0; absent means the base load).
        if study.get("load_level") != inputs.get("load_level"):
            continue
        if task["subfamily"] == "pf-report":
            if study.get("operation") == "none" and data.get("converged"):
                return True
            continue
        kind, primary, secondary = _parse_element(inputs["element"])
        operation, observation = {
            "bus": ("load_scale", "bus_voltage"),
            "line": ("line_rating", "line_loading"),
            "gen": ("gen_dispatch", "trafo_loading"),
        }[kind]
        if (study.get("operation") == operation
            and study.get("observation") == observation
            and study.get("observation_id") == (secondary if kind == "gen" else primary)
            and (kind == "bus" or study.get("element_id") == primary)
            and scale is not None and study.get("scale") == scale
            and data.get("converged")):
            return True
    return False


def grade(task: dict[str, Any], journal: Path) -> TaskResult:
    """Score a final answer independently; incomplete generations always fail."""
    events = load_rows(journal)
    terminal = events[-1]
    completed = terminal["kind"] == "terminal" and terminal["payload"]["state"] == "answered"
    responses = [e["payload"] for e in events if e["kind"] == "model_response"]
    answer = (responses[-1]["choices"][0]["message"].get("content") or "") if responses else ""
    if task["subfamily"] == "pf-unsolvable":
        from wattson.datasets.unsolvable import grade as grade_unsolvable

        correct, verification, diagnostics = grade_unsolvable(task, answer)
    else:
        correct, verification, diagnostics = score_answer(task, answer)
    evidence = relevant_evidence(task, answer, events)
    provenance = provenance_check(answer, events, task["task"].get("prompt", ""))
    if task["subfamily"] == "pf-derived":
        # A sum, difference or mean is in no tool reply by construction; its arithmetic is measured separately.
        provenance = {**provenance, "traced": True, "untraced": []}
    return {
        "example_id": task["example_id"], "subfamily": task["subfamily"],
        "answer": answer, "numerical_pass": correct, "tool_evidence_pass": evidence,
        "provenance_pass": provenance["traced"], "untraced_numbers": provenance["untraced"],
        "passed": completed and correct and evidence and provenance["traced"],
        "verification": verification, "diagnostics": diagnostics,
        "journal_sha256": hashlib.sha256(journal.read_bytes()).hexdigest(),
    }


async def attempt(args: argparse.Namespace, task: dict[str, Any], grading: asyncio.Lock) -> TaskResult:
    """Run and grade one task's single attempt in its own prompt and journal files.

    ``grading`` serialises grading: it can re-solve a power flow in this
    process, and pandapower solves are not documented as thread-safe (each
    takes ~1 s, so this costs little).
    """
    prompt = args.out / "prompts" / f"{task['example_id']}.txt"
    prompt.write_text(task["task"]["prompt"])
    journal = args.out / f"{task['example_id']}.jsonl"
    options = argparse.Namespace(base_url=args.base_url, model=args.model, prompt=prompt, study_tools=True,
                                 first_turn_tool_choice=args.first_turn_tool_choice,
                                 namespace_tools=getattr(args, "namespace_tools", False),
                                 python_tools=getattr(args, "python_tools", False),
                                 python_report_fields=getattr(args, "python_report_fields", False),
                                 enforce_provenance=getattr(args, "enforce_provenance", False))
    with journal.open("x") as stream:
        try:
            await probe(options, stream)
        except (ValueError, KeyError, TypeError) as error:
            # Malformed model-selected arguments are failed attempts.
            record(stream, "model_protocol_error", {"type": type(error).__name__, "message": str(error)})
        except Exception as error:
            record(stream, "controller_error", {"type": type(error).__name__, "message": str(error)})
            raise
    # Keep the re-solve off the event loop so other attempts keep streaming.
    async with grading:
        return await asyncio.to_thread(grade, task, journal)


async def evaluate(args: argparse.Namespace) -> None:
    """Audit independence, run all tasks once, and fsync every scored outcome.

    Up to ``args.concurrency`` attempts run at once. Results are still
    committed to ``results.jsonl`` in benchmark order (``verified_run``
    requires it): a finished task waits for every earlier task, while its raw
    journal is already durable.
    """
    tasks = load_rows(args.tasks)
    audit = audit_rows(tasks, load_rows(args.exclude))
    if not audit["clean"] or len(tasks) != frozen_task_count(args.tasks):
        raise ValueError(f"Expected a complete, frozen, independent task suite: {audit}")
    args.out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "model": args.model, "base_url": args.base_url,
        "independence_audit": audit,
        "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.tasks, args.exclude)},
        "max_turns": MAX_TURNS, "max_tool_calls": MAX_TOOL_CALLS, "max_tokens": MAX_TOKENS,
        "inference_timeout_s": INFERENCE_TIMEOUT_S,
        "temperature": 0.0, "attempts_per_task": 1, "concurrency": args.concurrency,
        "first_turn_tool_choice": args.first_turn_tool_choice,
        # The serving side of the condition, as the caller started the server
        # (vLLM --tool-strict-level); recorded so compare_agents can refuse mixes.
        "server_tool_strict_level": args.server_tool_strict_level,
        # The tools each attempt's MCP server lists (backlog 0026 adds ls/get).
        "tool_set": (TOOL_SET_PYTHON2 if getattr(args, "python_report_fields", False)
                     else TOOL_SET_PYTHON if getattr(args, "python_tools", False)
                     else TOOL_SET_NAMESPACE if getattr(args, "namespace_tools", False) else TOOL_SET_STUDY),
        # Whether the runtime provenance guard was on: a guarded result is a different arm and must never be read as an unguarded one.
        "enforce_provenance": bool(getattr(args, "enforce_provenance", False)),
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.out / "prompts").mkdir()
    gate = asyncio.Semaphore(args.concurrency)
    grading = asyncio.Lock()

    async def bounded(task: dict[str, Any]) -> TaskResult:
        async with gate:
            return await attempt(args, task, grading)

    results: list[TaskResult] = []
    pending = [asyncio.create_task(bounded(task)) for task in tasks]
    try:
        with (args.out / "results.jsonl").open("x") as scored:
            for future in pending:
                result = await future
                record(scored, "result", result)
                results.append(result)
                print(f"{args.model}: {len(results)}/{len(tasks)} pass={sum(r['passed'] for r in results)}", flush=True)
    finally:
        # A controller error stops the run visibly; don't leave attempts running.
        for future in pending:
            future.cancel()
    summary = {**manifest, "complete": len(results) == len(tasks), "n_tasks": len(results),
               "n_pass": sum(r["passed"] for r in results),
               "pass_at_1": sum(r["passed"] for r in results) / len(results),
               "g5_met": False}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


def main() -> None:
    """Require an explicit server and a fresh directory for the full benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--first-turn-tool-choice", choices=FIRST_TURN_TOOL_CHOICES, default="auto",
                        help="'required' forces a tool call on the first turn (a separate study arm)")
    parser.add_argument("--server-tool-strict-level", choices=TOOL_STRICT_LEVELS, default="auto",
                        help="The vLLM --tool-strict-level the server was started with (recorded, not applied)")
    parser.add_argument("--namespace-tools", action="store_true",
                        help="Also serve ls/get (backlog 0026); recorded as tool_set, so only like runs compare")
    parser.add_argument("--python-tools", action="store_true",
                        help="Backlog 0041: serve run_python (a Python shell with a solver); run_study_trial keeps reports only")
    parser.add_argument("--python-report-fields", action="store_true",
                        help="With --python-tools: the v2 shell (trial replies carry the report numbers)")
    parser.add_argument("--enforce-provenance", action="store_true",
                        help="Runtime guard: refuse a final answer containing a number no tool produced (a separate arm; recorded in the summary)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help="Attempts in flight at once; match the server's --max-num-seqs")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
