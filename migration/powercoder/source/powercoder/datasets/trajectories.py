"""Build tool-call training trajectories in the exact shape of the evaluation.

The r4 students were trained on ``{prompt -> expected.answer}`` text: no tools,
no tool calls, another system prompt, and ``seq_len: 1024``. They were then
evaluated as multi-turn tool agents and scored 0/306 (AGENTS.md FM-19). This
builds what the evaluation actually asks for, from the training split only:

- the evaluation's own system prompt (``tool_probe.SYSTEM_PROMPT``) and the tool
  list the evaluation's MCP server advertises;
- real ``run_study_trial`` calls through that same MCP stdio server, with each
  tool message holding the response exactly as the evaluation feeds it back;
- a final answer written from the measured values: the reference report wording,
  and for fits the bare scale the prompt asks for (the reference fit sentence
  fails the benchmark's own ``extract_scale``).

Each trajectory is written as an evaluation journal and graded by
``evaluate_agent.grade``, the benchmark's own scorer; only passing trajectories
are kept and every drop is counted in the manifest.

Named stand-in (AGENTS.md "Sandbox stand-ins must be named"): the assistant
turns are scripted, not sampled from a teacher model. Fits follow the reference
generator's procedure (``labs/_shared/gridfit.bisection_fit``: try both interval
ends, then bisect); reports make one ``operation="none"`` trial. Scripted turns
carry no reasoning text, so they teach the tool protocol and the search, not a
style of thinking.

Recovery variants (failure-mode fine-tuning, user direction 2026-10-22): every
other training row (by ``example_id``) also gets a variant whose first call is
one of the measured model failures, sent to the real MCP server so the response
is genuine, followed by a one-line note and the correct procedure:

- ``none_string`` (FM-7): ``contingency_line: "None"`` -> a validation error;
- ``contingency_zero`` (FM-18): a fit trial with ``contingency_line: 0`` -> a
  measurement that names "line 0 out of service";
- ``run_power_flow_contingency`` (FM-1/FM-9): an N-1 report through
  ``run_power_flow`` -> rejected, since it has no ``contingency_line``.
- ``invented_observation`` (r6b Qwen student, 2026-10-23: bus 999, then 1, 2, 3, ...): a
  report observing a bus the network lacks -> refused; the note says a network-wide report
  needs no element, and the report is sent without one (IR 0.2.0);
- ``wrong_element``: a fit's first call uses an id the network lacks in place of the one the
  task names -> refused; the note says to use the task's id, never to search for one.

Both bad ids are drawn by ``unsolvable.absent_id`` from gaps in that network's own ids,
seeded by the row, so no fixed number is learnt (user rule 2026-10-24: abstract skills, no
model-specific magic numbers). Reports and load-scale fits send no argument their operation
does not use, so no placeholder id appears in any call either.

Unsolvable rows (``pf-unsolvable``, ``unsolvable.make_unsolvable``): every
``UNSOLVABLE_EVERY``-th fit is rewritten to name an element its network lacks. The trajectory
asks the tool about it once and refuses without a value ("NEVER GUESS"; refuse only when the
task is unsolvable).

The final answer still comes only from the correct calls and passes
``grade()`` (which includes the no-made-up-numbers provenance check).
``mcp-trajectory`` rows are skipped: they are narrated
``load_network``/``run_power_flow`` transcripts, a subfamily the benchmark does
not contain, and ``run_power_flow`` for reports is FM-9's wrong tool.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, TextIO, TypedDict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from powercoder.datasets import common
from powercoder.datasets.audit import load_rows
from powercoder.inference.evaluate_agent import grade
from powercoder.inference.tool_probe import MAX_TOOL_CALLS, MAX_TURNS, SYSTEM_PROMPT, record

# Iteration cap passed to every scripted trial; the reference generator's
# solves converged in 5 iterations on snem-sa-2000, and studies.MAX_ITERATIONS is 100.
TRIAL_MAX_ITER: int = 50
# The scorer requires the reported scale to equal a tried scale exactly, and the
# reference answers print 6 decimals ("s = 1.037500"), so trials use that grid.
SCALE_DECIMALS: int = 6
# Each MCP server is its own process with one pandapower solve at a time; this
# many rows run at once (the evaluation's own concurrency is 8).
CONCURRENCY: int = 8
FIT_OPERATIONS: dict[str, tuple[str, str]] = {
    "pf-load-scale-fit": ("load_scale", "bus_voltage"),
    "pf-line-rating-fit": ("line_rating", "line_loading"),
    "pf-gen-dispatch-fit": ("gen_dispatch", "trafo_loading"),
}
SKIPPED_SUBFAMILIES: frozenset[str] = frozenset({"mcp-trajectory"})
# One recovery variant for every RECOVERY_EVERY-th row in example_id order.
RECOVERY_EVERY: int = 2
# The note the assistant writes after each injected failure, before correcting it.
RECOVERY_NOTES: dict[str, str] = {
    "none_string": ('contingency_line must be an integer or null, not the string "None"; '
                    "for the intact network I omit it."),
    "contingency_zero": ("That trial took line 0 out of service, but the task is the intact network, "
                         "so I omit contingency_line and start the search again."),
    "run_power_flow_contingency": ("run_power_flow has no contingency_line parameter; an N-1 report "
                                   'uses run_study_trial with operation="none".'),
    "invented_observation": ("Bus {bad} is not in this network, and the task names no element: a "
                             "network-wide report needs none, so I omit the observation."),
    "wrong_element": ("{kind} {bad} is not in this network; the task names {kind} {named}, so I use "
                      "that id rather than search for another."),
}
FAULTS: tuple[str, ...] = ("none_string", "contingency_zero", "run_power_flow_contingency",
                           "invented_observation", "wrong_element")
# One unsolvable rewrite for every UNSOLVABLE_EVERY-th fit (example_id order): enough to learn
# to refuse an absent element, few enough that refusal stays rare beside ~200 solvable fits.
UNSOLVABLE_EVERY: int = 8
ELEMENT_WORDS: dict[str, str] = {"bus": "Bus", "line": "Line", "gen": "Generator", "trafo": "Transformer"}
class Trajectory(TypedDict):
    """One graded conversation, ready for chat-template rendering."""

    example_id: str
    subfamily: str
    # None for the plain procedure, else the injected failure (RECOVERY_NOTES key).
    fault: str | None
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]


class Conversation:
    """Drive the evaluation's MCP server and journal every turn as tool_probe does."""

    def __init__(self, session: ClientSession, journal: TextIO, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> None:
        self.session, self.journal, self.messages = session, journal, messages
        # Argument order per tool, from its served JSON schema: grammar-constrained
        # decoding (xgrammar structural tags, any_order=False) accepts properties
        # only in schema order, so trained calls must use it too.
        self.order = {t["function"]["name"]: list(t["function"]["parameters"].get("properties", {})) for t in tools}
        self.calls = 0
        # Text for the next tool-call turn: the correction after an injected failure.
        self.note: str | None = None
        # Whether the server offers ls/get (backlog 0026); recoveries then walk the namespace.
        self.namespace = any(t["function"]["name"] == "ls" for t in tools)

    def respond(self, message: dict[str, Any], finish_reason: str) -> None:
        """Record an assistant turn in the shape of a chat completion."""
        record(self.journal, "model_response", {"choices": [{"index": 0, "message": message,
                                                             "finish_reason": finish_reason}]})
        self.messages.append(message)

    async def trial(self, arguments: dict[str, Any], tool: str = "run_study_trial") -> dict[str, Any]:
        """Make one tool-call turn and return the decoded tool response.

        A response that is not JSON (an MCP input-validation error) comes back
        as ``{"error": text}``.
        """
        if self.calls >= min(MAX_TOOL_CALLS, MAX_TURNS - 1):
            raise RuntimeError("Scripted trajectory would exceed the evaluation's budgets")
        order = self.order.get(tool, [])
        arguments = {**{k: arguments[k] for k in order if k in arguments},
                     **{k: v for k, v in arguments.items() if k not in order}}
        call = {"id": f"call_{self.calls}", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments)}}
        self.respond({"role": "assistant", "content": self.note, "tool_calls": [call]}, "tool_calls")
        self.note = None
        record(self.journal, "tool_request", call)
        response = await self.session.call_tool(tool, arguments)
        observed = response.model_dump(mode="json", by_alias=True)
        record(self.journal, "tool_response", {"tool_call_id": call["id"], "result": observed})
        self.messages.append({"role": "tool", "tool_call_id": call["id"],
                              "content": json.dumps(observed, allow_nan=False)})
        self.calls += 1
        text = observed["content"][0]["text"]
        try:
            return json.loads(text)
        except ValueError:
            return {"error": text}

    def answer(self, text: str) -> None:
        """Make the final answering turn, as the evaluation's terminal state expects."""
        self.respond({"role": "assistant", "content": text}, "stop")
        record(self.journal, "terminal", {"state": "answered", "actual_tool_calls": self.calls,
                                          "accuracy_claim": False})


def fit_arguments(row: dict[str, Any], scale: float, element: int | None = None) -> dict[str, Any]:
    """The fit procedure's trial: only the arguments its operation uses (IR 0.2.0).

    ``element`` replaces the task's named id (an injected mistake); otherwise the ids are the
    ones the prompt names, never one the script knows otherwise.
    """
    inputs = row["task"]["inputs"]
    operation, observation = FIT_OPERATIONS[row["subfamily"]]
    ids = [int(x) for x in re.findall(r"\d+", inputs["element"])]
    arguments: dict[str, Any] = {"network_id": inputs["case"], "operation": operation, "scale": scale}
    if operation != "load_scale":
        arguments["element_id"] = ids[0] if element is None else element
    observed = ids[-1] if element is None or operation == "gen_dispatch" else element
    arguments |= {"observation": observation, "observation_id": observed}
    if inputs.get("contingency_line") is not None:
        arguments["contingency_line"] = inputs["contingency_line"]
    arguments["max_iter"] = TRIAL_MAX_ITER
    if inputs.get("load_level") is not None:
        arguments["load_level"] = inputs["load_level"]
    return arguments


def report_arguments(row: dict[str, Any]) -> dict[str, Any]:
    """A network-wide report: no scale, element or observation, which it does not use."""
    inputs = row["task"]["inputs"]
    arguments: dict[str, Any] = {"network_id": inputs["case"], "operation": "none"}
    if inputs.get("contingency_line") is not None:
        arguments["contingency_line"] = inputs["contingency_line"]
    return arguments | {"max_iter": TRIAL_MAX_ITER}


def named_element(row: dict[str, Any]) -> tuple[str, int]:
    """The kind and id of the element a fit modifies (``inputs.element``, e.g. "line 232")."""
    kind = {"pf-load-scale-fit": "bus", "pf-line-rating-fit": "line", "pf-gen-dispatch-fit": "gen"}[row["subfamily"]]
    return kind, int(re.findall(r"\d+", row["task"]["inputs"]["element"])[0])


def extra_faults(row: dict[str, Any]) -> list[str]:
    """Recovery variants a recovery row gets in addition to fault_for's.

    Added rather than alternated, so every earlier trajectory is rebuilt
    byte-identically and no measured failure loses examples.
    """
    return ["invented_observation"] if row["subfamily"] == "pf-report" else ["wrong_element"]


def fault_for(row: dict[str, Any], index: int) -> str:
    """The failure injected into a row's recovery variant (alternating for fits)."""
    if row["subfamily"] == "pf-report":
        return "run_power_flow_contingency" if row["task"]["inputs"].get("contingency_line") is not None else "none_string"
    return ("none_string", "contingency_zero")[(index // RECOVERY_EVERY) % 2]


async def inject(conversation: Conversation, row: dict[str, Any], fault: str) -> None:
    """Make the faulty first call for real, then queue the correcting note.

    Raises:
        RuntimeError: if the server did not respond as the failure mode predicts.
    """
    from powercoder.datasets.unsolvable import absent_id, element_ids

    inputs = row["task"]["inputs"]
    if fault == "run_power_flow_contingency":
        observed = await conversation.trial({"network_id": inputs["case"], "contingency_line": inputs["contingency_line"]},
                                            tool="run_power_flow")
        expected = "error" in observed
    elif fault == "none_string":
        arguments = report_arguments(row) if row["subfamily"] == "pf-report" else fit_arguments(row, inputs["scale_lo"])
        observed = await conversation.trial({**arguments, "contingency_line": "None"})
        expected = "error" in observed
    elif fault == "invented_observation":
        bad = absent_id(element_ids(inputs["case"], "bus"), f"{row['example_id']}:{fault}")
        observed = await conversation.trial({**report_arguments(row), "observation": "bus_voltage", "observation_id": bad})
        expected = f"Unknown observation element {bad}" in str(observed.get("error") or "")
        conversation.note = RECOVERY_NOTES[fault].format(bad=bad)
    elif fault == "wrong_element":
        kind, named = named_element(row)
        bad = absent_id(element_ids(inputs["case"], kind), f"{row['example_id']}:{fault}")
        observed = await conversation.trial(fit_arguments(row, inputs["scale_lo"], element=bad))
        expected = f" element {bad}:" in str(observed.get("error") or "")
        conversation.note = RECOVERY_NOTES[fault].format(kind=ELEMENT_WORDS[kind], bad=bad, named=named)
    else:
        observed = await conversation.trial({**fit_arguments(row, inputs["scale_lo"]), "contingency_line": 0})
        expected = (observed.get("data") or {}).get("contingency") == "line 0 out of service"
    if not expected:
        raise RuntimeError(f"Injected {fault} did not produce its failure: {str(observed)[:200]}")
    if conversation.note is None:
        conversation.note = RECOVERY_NOTES[fault]


async def report(conversation: Conversation, row: dict[str, Any]) -> str:
    """One ``operation="none"`` trial, answered in the reference report wording."""
    inputs = row["task"]["inputs"]
    contingency = inputs.get("contingency_line")
    observed = await conversation.trial(report_arguments(row))
    data = observed["data"]
    where = (f" with line {contingency} out of service (N-1 contingency)"
             if contingency is not None else " (base case)")
    return (f"Power flow on '{inputs['case']}'{where} converged in {data['iterations']} iterations. "
            f"Min bus voltage = {data['min_bus_voltage_pu']:.4f} pu, max bus voltage = "
            f"{data['max_bus_voltage_pu']:.4f} pu, max line loading = {data['max_line_loading_percent']:.2f}%, "
            f"voltage violations = {data['voltage_violations']}.")


async def fit(conversation: Conversation, row: dict[str, Any]) -> str:
    """Bracket the target at both interval ends, then bisect to the tolerance.

    Raises:
        RuntimeError: if a trial fails or the target is not bracketed.
    """
    inputs = row["task"]["inputs"]
    target, tolerance = inputs["target"], inputs["tolerance"]

    async def measure(scale: float) -> float:
        observed = await conversation.trial(fit_arguments(row, scale))
        if not (observed["data"] or {}).get("metric_valid"):
            raise RuntimeError(f"Trial at s={scale} returned no valid metric: {observed['status']}")
        return observed["data"]["metric"]

    lo, hi = inputs["scale_lo"], inputs["scale_hi"]
    f_lo, f_hi = await measure(lo) - target, await measure(hi) - target
    best = min(((lo, f_lo), (hi, f_hi)), key=lambda point: abs(point[1]))
    if f_lo * f_hi > 0:
        raise RuntimeError("Target not bracketed by the interval ends")
    while abs(best[1]) > tolerance:
        mid = round((lo + hi) / 2, SCALE_DECIMALS)
        f_mid = await measure(mid) - target
        best = (mid, f_mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    # The prompt says "Return only the scale factor". The reference sentence
    # ("...s = 1.037500. At this scale...") fails extract_scale, whose pattern
    # rejects a number followed by a full stop, so the bare scale is answered.
    return f"{best[0]:.{SCALE_DECIMALS}f}"


async def refuse(conversation: Conversation, row: dict[str, Any]) -> str:
    """Ask the tool about the named element once; it is absent, so refuse without a value.

    Raises:
        RuntimeError: if the tool did not report the element as unknown.
    """
    inputs = row["task"]["inputs"]
    source = {"subfamily": inputs["source"]["subfamily"], "task": {"inputs": inputs["source"]["inputs"]}}
    if source["subfamily"] == "pf-report":
        # An N-1 report naming an absent line: the outage itself cannot be applied.
        source["task"]["inputs"] = {**source["task"]["inputs"], "contingency_line": inputs["absent_id"]}
        observed = await conversation.trial(report_arguments(source))
    else:
        observed = await conversation.trial(fit_arguments(source, inputs["source"]["inputs"]["scale_lo"],
                                                          element=inputs["absent_id"]))
    if f" {inputs['absent_id']}:" not in str(observed.get("error") or ""):
        raise RuntimeError(f"The absent element was not refused: {str(observed)[:200]}")
    return row["expected"]["answer"]


async def build(row: dict[str, Any], journals: Path, fault: str | None = None,
                namespace: bool = False) -> tuple[Trajectory | None, str]:
    """Script, journal and grade one row (optionally a recovery variant); return it or the drop reason."""
    parameters = StdioServerParameters(command=sys.executable,
                                       args=["-m", "powercoder.inference.mcp_server", "--study-tools",
                                             *(["--namespace-tools"] if namespace else [])],
                                       cwd=str(common.REPO_ROOT))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": row["task"]["prompt"]}]
    journal = journals / f"{row['example_id']}{'-' + fault if fault else ''}.jsonl"
    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                   "parameters": t.inputSchema}}
                 for t in (await session.list_tools()).tools]
        with journal.open("x") as stream:
            record(stream, "start", {"messages": list(messages), "scripted": True})
            record(stream, "tools", tools)
            conversation = Conversation(session, stream, messages, tools)
            try:
                if fault:
                    await inject(conversation, row, fault)
                script = {"pf-report": report, "pf-unsolvable": refuse}.get(row["subfamily"], fit)
                text = await script(conversation, row)
            except RuntimeError as error:
                return None, f"script: {error}"
            conversation.answer(text)
    result = await asyncio.to_thread(grade, row, journal)
    if not result["passed"]:
        return None, (f"grade: numerical={result['numerical_pass']} evidence={result['tool_evidence_pass']} "
                      f"provenance={result['provenance_pass']}")
    return {"example_id": row["example_id"], "subfamily": row["subfamily"], "fault": fault, "tools": tools,
            "messages": messages}, "kept"


async def build_all(jobs: list[tuple[dict[str, Any], str | None]], journals: Path,
                    namespace: bool = False) -> list[tuple[Trajectory | None, str]]:
    """Build every (row, fault) job with bounded concurrency, preserving order."""
    gate = asyncio.Semaphore(CONCURRENCY)

    async def bounded(row: dict[str, Any], fault: str | None) -> tuple[Trajectory | None, str]:
        async with gate:
            return await build(row, journals, fault, namespace)

    return await asyncio.gather(*(bounded(row, fault) for row, fault in jobs))


def write_in_order(path: Path, rows: list[Trajectory]) -> Path:
    """Write JSONL keeping every key in the order it was built.

    ``common.write_jsonl`` sorts keys. Chat templates render tool schemas with
    ``tojson`` in their stored order, so sorted keys gave the students a tool
    block ({"function": {"description": ...) the evaluation never serves
    ({"type": "function", "function": {"name": ...), for every turn of every
    trajectory (AGENTS.md FM-23).
    """
    with path.open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    return path


def main() -> int:
    """Build, grade and freeze trajectories with a manifest of every drop."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=Path, required=True, help="Training-split examples.jsonl")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--namespace-tools", action="store_true",
                        help="Serve ls/get too (backlog 0026); a different tool block, so a different training set")
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen trajectory directory")
    rows = load_rows(args.split)
    if any(r["lifecycle"]["split"] != "train" or r["lifecycle"]["status"] != "verified" for r in rows):
        raise ValueError("Only verified rows in the training split are permitted")
    selected = sorted((r for r in rows if r["subfamily"] not in SKIPPED_SUBFAMILIES), key=lambda r: r["example_id"])
    jobs: list[tuple[dict[str, Any], str | None]] = [(row, None) for row in selected]
    jobs += [(row, fault_for(row, index)) for index, row in enumerate(selected) if index % RECOVERY_EVERY == 0]
    jobs += [(row, fault) for index, row in enumerate(selected) if index % RECOVERY_EVERY == 0
             for fault in extra_faults(row)]
    from powercoder.datasets.unsolvable import make_unsolvable

    fits = [row for row in selected if row["subfamily"] in FIT_OPERATIONS]
    jobs += [(make_unsolvable(row), None) for index, row in enumerate(fits) if index % UNSOLVABLE_EVERY == 0]
    journals = args.out_dir / "journals"
    journals.mkdir(parents=True)
    outcomes = asyncio.run(build_all(jobs, journals, args.namespace_tools))
    kept = [trajectory for trajectory, _ in outcomes if trajectory is not None]
    examples = write_in_order(args.out_dir / "trajectories.jsonl", kept)
    manifest = {
        "dataset": "nem-powercoder-tool-trajectories",
        "split": str(args.split), "split_sha256": hashlib.sha256(args.split.read_bytes()).hexdigest(),
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                     cwd=common.REPO_ROOT).stdout.strip(),
        # Uncommitted generator changes make the commit alone insufficient provenance.
        "git_dirty": bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                                         cwd=common.REPO_ROOT).stdout.strip()),
        "system_prompt": SYSTEM_PROMPT, "rows": len(rows),
        "skipped_subfamilies": {name: sum(r["subfamily"] == name for r in rows) for name in sorted(SKIPPED_SUBFAMILIES)},
        "kept": len(kept),
        "kept_by_subfamily": {name: sum(t["subfamily"] == name for t in kept) for name in sorted({t["subfamily"] for t in kept})},
        "kept_by_fault": {str(name): sum(t["fault"] == name for t in kept) for name in [None, *FAULTS]},
        "dropped": {f"{row['example_id']}:{fault}": reason for (row, fault), (_, reason) in zip(jobs, outcomes)
                    if reason != "kept"},
        "tool_calls": {f"{t['example_id']}:{t['fault']}": sum(m["role"] == "tool" for m in t["messages"]) for t in kept},
        "trajectories_sha256": hashlib.sha256(examples.read_bytes()).hexdigest(),
        "benchmark_access": False,
        "namespace_tools": args.namespace_tools,
    }
    common.write_json(args.out_dir / "manifest.json", manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != "tool_calls"}, indent=2))
    return 0 if kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
