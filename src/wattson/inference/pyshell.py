"""A small Python shell with a solver, so the model writes code for every calculation (backlog 0041).

The model never does arithmetic: a bisection's midpoint, a residual, a comparison with a tolerance are all
computed by code it writes and this module runs. The code can call ``solver.trial(...)`` (the same study as the
``run_study_trial`` tool) and read the result; nothing else leaves the interpreter. Every trial is recorded, so
the harness can grade a fit from what the solver really returned, not from what the code printed.

Named stand-in, not a security boundary: the child is ``python -I`` with a module allowlist, no ``open``/``eval``/
``exec``, a wall-clock timeout, memory and CPU limits and no file writes. Restricted interpreters leak (attribute
walks reach the original builtins). Real containment belongs to the Phase 2 runtime (MXC); do not expose this tool
to untrusted callers without it.
"""

from __future__ import annotations

import json
import queue
import resource
import subprocess
import sys
import threading
import time
from typing import Any, Callable

# Wall-clock budget for one run_python call. A 20-step bisection takes about a second of solver time per trial
# on the largest case, so a minute leaves room for an unusually long search and still ends a runaway loop.
WALL_TIMEOUT_S: float = 60.0
# Address-space limit for the child interpreter (bytes): enough for the standard library and the trial replies.
MEMORY_LIMIT_BYTES: int = 1 << 30
# Solver trials one run may make; a bisection to 1e-6 over a 0.2-wide interval needs about 18.
MAX_TRIALS_PER_RUN: int = 60
# Newton-Raphson iteration cap added when the code leaves ``max_iter`` out; the same value the scripted
# trajectories send (trajectories.TRIAL_MAX_ITER), so a trial from code equals a trial from a tool call.
DEFAULT_MAX_ITER: int = 50
# Characters of stdout and of an error message returned to the model.
OUTPUT_LIMIT_CHARS: int = 4000
ERROR_LIMIT_CHARS: int = 600
# Modules the code may import. ``solver`` is provided; the rest are pure standard library.
ALLOWED_MODULES: tuple[str, ...] = ("math", "json", "statistics", "fractions", "decimal", "solver")
# Fields of a trial's reply kept in the tool result: what grading and the code's own checks need. The full reply
# also carries an evidence block and a dozen fields the grader does not read; leaving them out keeps the result short.
TRIAL_DATA_FIELDS: tuple[str, ...] = ("study", "converged", "iterations", "metric", "metric_unit", "metric_valid")

# Fields v2 adds for every trial (and which an operation="none" report study is for): the numbers a multi-candidate
# comparison reads. The v1 shell hid them, so a program could not rank outages by line loading (backlog 0041).
REPORT_FIELDS: tuple[str, ...] = ("min_bus_voltage_pu", "max_bus_voltage_pu", "max_line_loading_percent",
                                  "max_trafo_loading_percent", "voltage_violations")

# What the child runs. It reads one JSON line {"code": ...}, then speaks a line protocol on its real stdout:
# {"trial": {...}} asks the parent for a solver trial and waits for one reply line; {"done": ...} ends the run.
# User prints go to a buffer, so they can never be mistaken for protocol lines.
CHILD_SOURCE: str = r'''
import builtins, io, json, sys, types

ALLOWED = set(json.loads(sys.argv[1]))
LIMIT = int(sys.argv[2])
wire_in, wire_out = sys.stdin, sys.stdout
out = io.StringIO()
sys.stdout = sys.stderr = out


class SolverError(Exception):
    """The solver refused or failed a trial."""


def trial(**arguments):
    wire_out.write(json.dumps({"trial": arguments}) + "\n")
    wire_out.flush()
    reply = json.loads(wire_in.readline())
    if "error" in reply:
        raise SolverError(reply["error"])
    return reply["response"]


def exists(network_id, kind, element_id):
    wire_out.write(json.dumps({"exists": {"network_id": network_id, "kind": kind, "element_id": element_id}}) + "\n")
    wire_out.flush()
    reply = json.loads(wire_in.readline())
    if "error" in reply:
        raise SolverError(reply["error"])
    return reply["response"]


solver = types.ModuleType("solver")
solver.trial = trial
solver.exists = exists
solver.SolverError = SolverError
real_import = builtins.__import__


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED:
        raise ImportError("import of %r is not allowed; allowed: %s" % (name, ", ".join(sorted(ALLOWED))))
    if name == "solver":
        return solver
    return real_import(name, globals, locals, fromlist, level)


safe = {k: getattr(builtins, k) for k in dir(builtins)
        if k not in {"open", "eval", "exec", "compile", "input", "breakpoint", "help", "exit", "quit",
                     "memoryview", "__import__", "__loader__", "__spec__"}}
safe["__import__"] = guarded_import
code = json.loads(wire_in.readline())["code"]
error = None
try:
    exec(compile(code, "<run_python>", "exec"), {"__builtins__": safe, "__name__": "__main__"})
except SystemExit:
    pass
except BaseException as exc:
    error = "%s: %s" % (type(exc).__name__, exc)
wire_out.write(json.dumps({"done": {"stdout": out.getvalue()[:LIMIT], "error": error}}) + "\n")
wire_out.flush()
'''


def limits() -> None:
    """Applied in the child before it starts: memory, CPU time, no file writes, no child processes."""
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    cpu = int(WALL_TIMEOUT_S) + 5
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def compact(reply: dict[str, Any], report_fields: bool = False, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """A trial's reply cut to the fields grading and the code need.

    ``report_fields`` is the v2 shell: the reply also carries the report numbers and, for an operation="none" study, the
    exact report sentence (``report_text``), so a report is copied, never rounded.
    """
    data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
    keep = TRIAL_DATA_FIELDS + (REPORT_FIELDS if report_fields else ())
    kept = {key: data[key] for key in keep if key in data}
    if report_fields and arguments is not None:
        text = report_text(arguments, data)
        if text is not None:
            kept["report_text"] = text
    return {"status": reply.get("status"), "error": reply.get("error"), "data": kept}


def element_exists(network_id: str, kind: str, element_id: int) -> bool:
    """Is ``element_id`` an element of ``kind`` ("bus", "line", "gen", "trafo") in the served network? Raises ValueError if not known."""
    from wattson.datasets.unsolvable import TABLES, element_ids

    if kind not in TABLES:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(TABLES))}")
    return int(element_id) in set(element_ids(network_id, kind))


def report_text(arguments: dict[str, Any], data: dict[str, Any]) -> str | None:
    """The reference report sentence for an operation="none" study, with the numbers printed at the decimals the format
    requires, so the model copies a sentence instead of rounding a number (voltages 4 decimals, loading 2).

    None if the study did not converge or lacks the report numbers.
    """
    needed = ("iterations", "min_bus_voltage_pu", "max_bus_voltage_pu", "max_line_loading_percent", "voltage_violations")
    if arguments.get("operation") != "none" or not data.get("converged") or any(k not in data for k in needed):
        return None
    contingency = arguments.get("contingency_line")
    where = f" with line {contingency} out of service (N-1 contingency)" if contingency is not None else " (base case)"
    return (f"Power flow on '{arguments.get('network_id')}'{where} converged in {data['iterations']} iterations. "
            f"Min bus voltage = {data['min_bus_voltage_pu']:.4f} pu, max bus voltage = {data['max_bus_voltage_pu']:.4f} pu, "
            f"max line loading = {data['max_line_loading_percent']:.2f}%, voltage violations = {data['voltage_violations']}.")


def run_python(code: str, trial: Callable[[dict[str, Any]], dict[str, Any]], report_fields: bool = False) -> dict[str, Any]:
    """Run ``code`` in the restricted child, serving its solver trials through ``trial``.

    Args:
        code: Python source the model wrote.
        trial: Runs one study from the arguments the code passed to ``solver.trial`` and returns its reply
            (the dict ``run_study_trial`` returns). It may raise; the code then sees ``solver.SolverError``.

    Returns:
        ``{"status": "ok" | "error" | "timeout", "stdout", "error", "trials": [{"arguments", "response"}]}``.
        ``trials`` lists every trial the code made, in order, with the solver's real reply.
    """
    child = subprocess.Popen([sys.executable, "-I", "-c", CHILD_SOURCE, json.dumps(list(ALLOWED_MODULES)),
                              str(OUTPUT_LIMIT_CHARS)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1, preexec_fn=limits)
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert child.stdout is not None
        for line in child.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    assert child.stdin is not None
    trials: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    result: dict[str, Any] = {"status": "error", "stdout": "", "error": "the interpreter ended without a result"}
    deadline = time.monotonic() + WALL_TIMEOUT_S
    try:
        child.stdin.write(json.dumps({"code": code}) + "\n")
        child.stdin.flush()
        while True:
            remaining = deadline - time.monotonic()
            try:
                line = lines.get(timeout=max(remaining, 0.0))
            except queue.Empty:
                result = {"status": "timeout", "stdout": "",
                          "error": f"TimeoutError: the run exceeded {WALL_TIMEOUT_S:.0f} seconds"}
                break
            if line is None:
                break
            message = json.loads(line)
            if "done" in message:
                done = message["done"]
                result = {"status": "error" if done["error"] else "ok", "stdout": done["stdout"],
                          "error": done["error"][:ERROR_LIMIT_CHARS] if done["error"] else None}
                break
            if "exists" in message:
                try:
                    found = element_exists(**message["exists"])
                    checks.append({**message["exists"], "exists": found})
                    reply = {"response": found}
                except Exception as error:  # noqa: BLE001 -- a bad kind or network is the code's to handle
                    reply = {"error": f"{type(error).__name__}: {error}"}
                child.stdin.write(json.dumps(reply) + "\n")
                child.stdin.flush()
                continue
            arguments = {"max_iter": DEFAULT_MAX_ITER, **message["trial"]}
            if len(trials) >= MAX_TRIALS_PER_RUN:
                reply: dict[str, Any] = {"error": f"trial limit of {MAX_TRIALS_PER_RUN} per run reached"}
            else:
                try:
                    response = trial(arguments)
                    trials.append({"arguments": arguments, "response": compact(response, report_fields, arguments)})
                    reply = {"response": compact(response, report_fields, arguments)}
                except Exception as error:  # noqa: BLE001 -- any solver failure is the code's to handle
                    trials.append({"arguments": arguments,
                                   "response": {"status": "execution_failure",
                                                "error": f"{type(error).__name__}: {error}", "data": {}}})
                    reply = {"error": f"{type(error).__name__}: {error}"}
            child.stdin.write(json.dumps(reply) + "\n")
            child.stdin.flush()
    finally:
        child.kill()
        child.wait()
    return {**result, "trials": trials, **({"checks": checks} if checks else {})}


# The tool the model sees. The description is the whole contract for the code's solver API.
TOOL_NAME: str = "run_python"
TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Run Python 3 code and return what it printed. Do every calculation in code (sums, midpoints, "
            "differences, comparisons with a tolerance): never compute a number yourself. Allowed imports: "
            + ", ".join(ALLOWED_MODULES) + ". The module `solver` runs the same study as run_study_trial: "
            "`reply = solver.trial(network_id=..., operation=..., scale=..., element_id=..., observation=..., "
            "observation_id=..., max_iter=...)` returns a dict with `status`, `error` and `data` "
            "(`data['metric']`, `data['converged']`, `data['metric_valid']`, `data['study']`); it raises "
            "`solver.SolverError` if the study is refused. Use print() to see results. Each call starts a fresh "
            "interpreter, so nothing is remembered between calls."),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to run."}},
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}
# Added to the tool description in the v2 shell.
REPORT_NOTE: str = (" Every reply's `data` also holds the report numbers: `min_bus_voltage_pu`, `max_bus_voltage_pu`, "
                    "`max_line_loading_percent`, `max_trafo_loading_percent`, `voltage_violations`; "
                    "`solver.trial(network_id=..., operation='none', contingency_line=N)` studies line N out of service, "
                    "and its `data['report_text']` is the finished report sentence to copy exactly. "
                    "`solver.exists(network_id, kind, element_id)` returns whether a bus, line, gen or trafo exists in the "
                    "network; check the elements a task names before searching.")
# In the python profile run_study_trial keeps only the report operation; studies that need a scale run in code.
STUDY_NOTE: str = ("In this profile only operation=\"none\" (base-case and N-1 reports) is accepted here; any study "
                   "that varies a scale runs inside run_python through solver.trial. ")
