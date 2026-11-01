"""Smoke-gate the retrained students, then evaluate all four models and measure them.

r6 of the WattsOn study. Inputs are
frozen and verified before anything is served:

- the students from ``training/agent_sft.py`` (each ``hash_manifest.json``
  re-hashed), trained on tool-call trajectories (AGENTS.md FM-19);
- the 20-task smoke suite drawn from the *validation* split, so the gate never
  sees a benchmark task, and the stratified 100-task benchmark sample
  (``wattson/datasets/sample_benchmark.py``).

Stages, every one owned by this driver and journaled to ``pipeline.jsonl``:

1. Smoke: each student serves alone and runs the 20 tasks. It passes only with
   at least ``SMOKE_MIN_PASS`` strict passes; a student that fails is not
   evaluated further, and its comparisons are recorded as skipped.
2. For each of Qwen base, Qwen student, Granite base, Granite student (one
   server at a time, as in r5): the latency benchmark on the idle server
   (``wattson/inference/latency_bench.py``), then the 100-task evaluation.
   Host MemAvailable is sampled for the server's whole life.
3. The strict and solver-verified comparisons for every pair.
4. Lab 10: RestoreBench's single tool-using agent on a 5-scenario IEEE 118 pilot,
   each evaluated model served again on its own. This runs after the
   comparisons are written, so the external benchmark cannot cost the study its
   results (labs/10-poweragentbench-restorebench/README.md).

No stage is retried or truncated automatically; a failed stage stops the driver
with its logs kept.

``--strict-arm`` is a separate study arm (never part of r6, backlog 0011):
strict tool calling as a 2x2 on the 20 smoke tasks for every model, server
``--tool-strict-level`` {auto, parameter} x first turn {auto, required}. vLLM
builds xgrammar's model-native tool grammar per request from the request's own
tools, so nothing is stored here, and the prompt is unchanged (strictness is
server-side). See strict_arm.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

from wattson.inference.evaluate_agent import frozen_task_count
from wattson.inference.tool_probe import record
from wattson.training.granite.train import digest
from wattson.training.run_comparison import (
    CONCURRENCY, GPU_MEMORY_FRACTION, GRANITE_GPU_MEMORY_FRACTION, HF_HUB, MEMINFO, QWEN_BASE_SNAPSHOT, REPO,
    command, meminfo_bytes, serve, stop_server, verify_evaluation, write_comparison, write_numerical_comparison)

# Fit prompts in wording v2 (wattson/datasets/reword.py): v1 said "applied to bus B"
# for a fit that scales every load. Same tasks, ids and references; r1-r6b ran the v1 files
# (domain-stratified-100-20261022, smoke-validation-20-20261022, smoke/splits/train.jsonl).
BENCHMARK: Path = Path("data/suites/domain-stratified-100-20261024-v2/examples.jsonl")
SMOKE: Path = Path("data/suites/smoke-validation-20-20261024-v2/examples.jsonl")
# Independence for both suites is audited against the students' training rows.
TRAIN_SPLIT: Path = Path("data/tasks/train-split-20261024-v2/examples.jsonl")
GRANITE_BASE: Path = Path("out/granite-4.2-8b-base")
STUDENTS: dict[str, Path] = {
    "qwen-student": Path("out/agent-sft-qwen35-4b-20261022-v2"),
    "granite-student": Path("out/agent-sft-granite42-8b-20261022-v2"),
}
# Half the 20 smoke tasks. The scripted trajectories pass 20/20; the bases passed
# 3.9-5.6% of the 306-task benchmark (r4), so at p = 0.1, P(>= 10/20) ~ 7e-6:
# a pass is learned behaviour, not luck. Fixed before any student was served.
SMOKE_MIN_PASS: int = 10
# The strict-tool-calling arm's two factors (backlog 0011): the server's vLLM
# --tool-strict-level ("parameter" enforces each argument's schema on every turn)
# and the first turn's tool_choice ("required" forces a tool call).
STRICT_LEVELS: tuple[str, ...] = ("auto", "parameter")
EVALUATOR: str = "wattson.inference.evaluate_agent"
PROMPTED_EVALUATOR: str = "wattson.inference.evaluate_prompted"
FIRST_TURN_CHOICES: tuple[str, ...] = ("auto", "required")
# Host memory sampling period; vLLM's load and each request last seconds or more.
MEMORY_SAMPLE_S: float = 1.0
RESTOREBENCH_LAB: Path = Path("labs/10-poweragentbench-restorebench/restorebench_lab.py")
# A pilot, not a benchmark result: the first 5 IEEE 118 scenarios per model, as a
# transfer/regression check. RestoreBench caps
# an episode at 1,200 s, so 5 at 8 in flight is bounded at ~20 min per model;
# all 46 would add ~7 h across four models for a signal the study does not gate on.
RESTOREBENCH_CORPUS: str = "ieee118"
RESTOREBENCH_SCENARIOS: int = 5


class MemorySampler:
    """Record MemAvailable once per period until stopped, in its own thread.

    GB10 memory is unified: weights, KV cache and activations all come out of
    MemAvailable, so the drop from the pre-start baseline is the server's peak
    footprint, provided nothing else starts meanwhile (the driver runs alone).
    """

    def __init__(self, path: Path) -> None:
        self.path, self.stop = path, threading.Event()
        self.baseline = meminfo_bytes("MemAvailable", MEMINFO)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        with self.path.open("x") as stream:
            while not self.stop.is_set():
                stream.write(json.dumps({"unix_time": time.time(),
                                         "available": meminfo_bytes("MemAvailable", MEMINFO)}) + "\n")
                stream.flush()
                self.stop.wait(MEMORY_SAMPLE_S)

    def __enter__(self) -> "MemorySampler":
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop.set()
        self.thread.join()

    def peak(self, since: float = 0.0) -> dict[str, int]:
        """Lowest MemAvailable after ``since`` and its drop from the baseline."""
        samples = [json.loads(line) for line in self.path.read_text().splitlines()]
        lowest = min(s["available"] for s in samples if s["unix_time"] >= since)
        return {"baseline_available": self.baseline, "min_available": lowest, "peak_used": self.baseline - lowest}


def verify_student(directory: Path, events: TextIO) -> Path:
    """Re-hash every adapter and merged file the trainer recorded; return the merged model.

    Raises:
        ValueError: if any file is missing or changed.
    """
    manifest = json.loads((directory / "hash_manifest.json").read_text())
    for relative, expected in manifest.items():
        if digest(directory / relative) != expected:
            raise ValueError(f"Student file changed since training: {directory / relative}")
    log = json.loads((directory / "train_log.json").read_text())
    record(events, "student_verified", {"directory": str(directory), "files": len(manifest),
        "completed_steps": log["completed_steps"], "trajectories_sha256": log["trajectories_sha256"]})
    return directory / "merged-bf16"


# Extra evaluate_agent flags for every suite of this process (set by run_student_eval --python-tools);
# a module constant rather than a parameter so every caller of ``session`` keeps its signature.
EVALUATOR_FLAGS: tuple[str, ...] = ()


def evaluation(model: str, port: int, tasks: Path, out: Path, first_turn_tool_choice: str = "auto",
               strict_level: str = "auto", evaluator: str = EVALUATOR) -> tuple[list[str], Path]:
    """The evaluate_agent command for one suite, at the shared concurrency and budgets.

    ``strict_level`` must be the --tool-strict-level the server was started with;
    it is recorded in the run manifest as a comparison condition.
    """
    return ([sys.executable, "-m", evaluator, "--base-url", f"http://127.0.0.1:{port}",
             "--model", model, "--tasks", str(tasks), "--exclude", str(TRAIN_SPLIT), "--out", str(out),
             "--concurrency", str(CONCURRENCY), "--first-turn-tool-choice", first_turn_tool_choice,
             "--server-tool-strict-level", strict_level, *EVALUATOR_FLAGS],
            out.parent / f"{out.name}.log")


def session(label: str, tag: str, source: Path, model_path: str, fraction: float, root: Path, events: TextIO,
            suites: list[tuple[str, Path, Path]] | list[tuple[str, Path, Path, str]], latency: bool,
            restorebench: bool = False, strict_level: str = "auto", evaluator: str = EVALUATOR,
            reasoning: str | None = None) -> None:
    """Serve one model alone; optionally benchmark latency; run each suite; release it.

    Args:
        tag: Distinguishes this session's container and files: a student is
            served once for its smoke gate and again for the benchmark.
        suites: (stage, tasks, out_dir[, first_turn_tool_choice]) evaluations to run in
            order on this one server; the choice defaults to "auto".
        latency: Run the latency benchmark on the idle server before any suite.
        restorebench: Run Lab 10's RestoreBench agent stage instead of or after the suites.
        strict_level: The server's vLLM --tool-strict-level ("auto" adds no flag).
        evaluator: The evaluation module (evaluate_agent, or evaluate_prompted for G0's
            procedure-in-the-prompt baseline).
    """
    name, model = f"wattson-{root.name}-{label}-{tag}", f"wattson-{label}"
    if reasoning is None:
        reasoning = "nemotron_v3" if label.startswith("granite") else "qwen3"
    port = 8101
    with MemorySampler(root / f"{label}-{tag}-memory.jsonl") as memory:
        try:
            serve(source, name, model, reasoning, root, events, memory_fraction=fraction,
                  model_path=model_path, port=port,
                  vllm_args=() if strict_level == "auto" else ("--tool-strict-level", strict_level))
            loaded = memory.peak()
            record(events, "memory_after_load", {"model": label, **loaded})
            if latency:
                start = time.time()
                command([sys.executable, "-m", "wattson.inference.latency_bench", "--base-url",
                         f"http://127.0.0.1:{port}", "--model", model, "--tasks", str(SMOKE),
                         "--out", str(root / f"{label}-latency.json")], root / f"{label}-latency.log", events)
                record(events, "latency_complete", {"model": label, "path": str(root / f"{label}-latency.json"),
                                                     "memory": memory.peak(start)})
            for stage, tasks, out, *choice in suites:
                start = time.time()
                command(*evaluation(model, port, tasks, out, choice[0] if choice else "auto", strict_level, evaluator),
                        events)
                verify_evaluation(out, events)
                record(events, "suite_memory", {"model": label, "stage": stage, "memory": memory.peak(start)})
            if restorebench:
                out = root / f"{label}-restorebench"
                command([sys.executable, str(RESTOREBENCH_LAB), "--step", "agent", "--base-url",
                         f"http://127.0.0.1:{port}", "--model", model, "--corpus", RESTOREBENCH_CORPUS,
                         "--limit", str(RESTOREBENCH_SCENARIOS), "--results-dir", str(out)],
                        root / f"{label}-restorebench.log", events)
                record(events, "restorebench_complete", {"model": label,
                    "summary": json.loads((out / "summary.json").read_text())})
        finally:
            stop_server(name, root, events)
    record(events, "memory_peak", {"model": label, "session": tag, **memory.peak()})


def run(args: argparse.Namespace, events: TextIO) -> None:
    """Gate, evaluate, measure and compare, each stage only after the last completed."""
    extra: dict[str, Path] = dict(getattr(args, "suites", None) or {})
    for suite in (SMOKE, BENCHMARK, *extra.values()):
        record(events, "suite_verified", {"tasks": str(suite), "n_tasks": frozen_task_count(suite)})
    directories = STUDENTS | dict(getattr(args, "students", None) or {})
    students = {label: verify_student(directory, events) for label, directory in directories.items()}
    fractions = {"qwen": GPU_MEMORY_FRACTION, "granite": GRANITE_GPU_MEMORY_FRACTION}
    root = args.root

    passed: dict[str, bool] = {}
    for label, merged in students.items():
        out = root / f"{label}-smoke"
        session(label, "smoke", merged, "", fractions[label.split("-")[0]], root, events,
                [("smoke", SMOKE, out)], latency=False)
        summary = json.loads((out / "summary.json").read_text())
        passed[label] = summary["n_pass"] >= SMOKE_MIN_PASS
        record(events, "smoke_gate", {"model": label, "n_pass": summary["n_pass"], "n_tasks": summary["n_tasks"],
                                      "required": SMOKE_MIN_PASS, "passed": passed[label]})

    models = [("qwen-base", HF_HUB, QWEN_BASE_SNAPSHOT), ("qwen-student", students["qwen-student"], ""),
              ("granite-base", GRANITE_BASE, ""), ("granite-student", students["granite-student"], "")]
    evaluated = []
    for label, source, model_path in models:
        if label in passed and not passed[label]:
            record(events, "evaluation_skipped", {"model": label, "reason": "smoke gate failed"})
            continue
        # Extra suites (r7: judgement, unsolvable) run on the same server, so a model loads once.
        session(label, "benchmark", source, model_path, fractions[label.split("-")[0]], root, events,
                [("benchmark", BENCHMARK, root / label)]
                + [(name, tasks, root / f"{label}-{name}") for name, tasks in extra.items()], latency=True)
        evaluated.append(label)

    pairs = [("qwen-base", "qwen-student"), ("granite-base", "granite-student"), ("qwen-base", "granite-base"),
             ("qwen-student", "granite-student"), ("qwen-base", "granite-student"), ("qwen-student", "granite-base")]
    outcomes = {}
    for first, second in pairs:
        stem = f"{first}-vs-{second}"
        if first not in evaluated or second not in evaluated:
            record(events, "comparison_skipped", {"comparison": stem, "evaluated": evaluated})
            continue
        strict = write_comparison(root / first, root / second, root / f"{stem}.json", events)
        numerical = write_numerical_comparison(root / first, root / second, root / f"{stem}-numerical.json", events)
        outcomes[stem] = {"strict_delta_pp": strict["delta_percentage_points"],
                          "solver_verified_delta_pp": numerical["delta_percentage_points"]}
        for name in extra:
            suite_stem = f"{first}-{name}-vs-{second}-{name}"
            compared = write_comparison(root / f"{first}-{name}", root / f"{second}-{name}", root / f"{suite_stem}.json", events)
            outcomes[suite_stem] = {"strict_delta_pp": compared["delta_percentage_points"]}
    record(events, "comparisons_complete", {"smoke": passed, "evaluated": evaluated, "comparisons": outcomes})
    record_ledgers(root, events)

    sources = dict((label, (source, model_path)) for label, source, model_path in models)
    for label in evaluated:
        session(label, "restorebench", *sources[label], fractions[label.split("-")[0]], root, events,
                [], latency=False, restorebench=True)
    record(events, "pipeline_complete", {"smoke": passed, "evaluated": evaluated, "comparisons": outcomes})


def record_ledgers(root: Path, events: TextIO) -> None:
    """PC-REQ-4: every evaluated directory's error ledger and IR protocol validity, journaled."""
    from wattson.inference.error_ledger import summarize
    from wattson.inference.protocol_suite import measure

    for directory in sorted(p for p in root.iterdir() if p.is_dir() and any(p.glob("ex_*.jsonl"))):
        journals = sorted(directory.glob("ex_*.jsonl"))
        summary = summarize(journals)
        (root / f"{directory.name}-error-ledger.json").write_text(json.dumps(summary, indent=2) + "\n")
        record(events, "error_ledger", {"model": directory.name, **summary, "protocol": measure(journals)})


def strict_arm(args: argparse.Namespace, events: TextIO) -> None:
    """Strict tool calling as a 2x2 on the smoke suite, for every model (backlog 0011).

    Server --tool-strict-level {auto, parameter} x first turn {auto, required}:
    each model is served once per strict level and runs both first-turn
    choices there. Each of the three non-baseline cells is compared with the
    model's own (auto, auto) cell, the comparison naming exactly the
    conditions it spans, so the effect of forcing is not credited to schema
    enforcement or the reverse. 20 tasks per cell: a direction-finding pilot,
    not a gate.
    """
    students = {label: verify_student(directory, events) for label, directory in STUDENTS.items()}
    sources = {"qwen-base": (HF_HUB, QWEN_BASE_SNAPSHOT), "qwen-student": (students["qwen-student"], ""),
               "granite-base": (GRANITE_BASE, ""), "granite-student": (students["granite-student"], "")}
    fractions = {"qwen": GPU_MEMORY_FRACTION, "granite": GRANITE_GPU_MEMORY_FRACTION}
    cell = lambda label, level, choice: args.root / f"{label}-strict-{level}-first-{choice}"
    outcomes: dict[str, dict[str, Any]] = {}
    for label, (source, model_path) in sources.items():
        for level in STRICT_LEVELS:
            session(label, f"strict-{level}", source, model_path, fractions[label.split("-")[0]], args.root, events,
                    [(f"smoke-strict-{level}-first-{choice}", SMOKE, cell(label, level, choice), choice)
                     for choice in FIRST_TURN_CHOICES], latency=False, strict_level=level)
        baseline = cell(label, "auto", "auto")
        for level in STRICT_LEVELS:
            for choice in FIRST_TURN_CHOICES:
                if (level, choice) == ("auto", "auto"):
                    continue
                spans = frozenset(name for name, differs in (("server_tool_strict_level", level != "auto"),
                                                              ("first_turn_tool_choice", choice != "auto")) if differs)
                stem = f"{label}-strict-{level}-first-{choice}-vs-baseline"
                strict = write_comparison(baseline, cell(label, level, choice), args.root / f"{stem}.json", events, spans)
                numerical = write_numerical_comparison(baseline, cell(label, level, choice),
                                                       args.root / f"{stem}-numerical.json", events, spans)
                outcomes.setdefault(label, {})[f"strict-{level}/first-{choice}"] = {
                    "strict_delta_pp": strict["delta_percentage_points"],
                    "solver_verified_delta_pp": numerical["delta_percentage_points"]}
    record(events, "pipeline_complete", {"arm": "strict-tool-calling-2x2", "tasks": str(SMOKE),
                                         "comparisons": outcomes})


def prompted_arm(args: argparse.Namespace, events: TextIO) -> None:
    """G0 baseline (b): each base model with the procedure in its system prompt, on the smoke suite.

    Compared, on the same 20 tasks, with that base's plain run (the strict arm's
    (auto, auto) cell, ``--strict-root``) and with its family's student (r6b's
    smoke run, ``--r6-root``). Each comparison names the condition it spans
    (system_prompt_variant). If a prompted base matches its student, fine-tuning
    added nothing measurable (backlog 0008).
    """
    sources = {"qwen-base": (HF_HUB, QWEN_BASE_SNAPSHOT), "granite-base": (GRANITE_BASE, "")}
    fractions = {"qwen": GPU_MEMORY_FRACTION, "granite": GRANITE_GPU_MEMORY_FRACTION}
    spans = frozenset({"system_prompt_variant"})
    outcomes: dict[str, dict[str, Any]] = {}
    for label, (source, model_path) in sources.items():
        family = label.split("-")[0]
        prompted = args.root / f"{label}-prompted-smoke"
        session(label, "prompted", source, model_path, fractions[family], args.root, events,
                [("smoke-prompted", SMOKE, prompted)], latency=False, evaluator=PROMPTED_EVALUATOR)
        references = {"plain-base": args.strict_root / f"{label}-strict-auto-first-auto",
                      "student": args.r6_root / f"{family}-student-smoke"}
        for name, reference in references.items():
            if not (reference / "summary.json").is_file():
                record(events, "comparison_skipped", {"model": label, "against": name, "missing": str(reference)})
                continue
            stem = f"{label}-prompted-vs-{name}"
            strict = write_comparison(reference, prompted, args.root / f"{stem}.json", events, spans)
            numerical = write_numerical_comparison(reference, prompted, args.root / f"{stem}-numerical.json",
                                                   events, spans)
            outcomes.setdefault(label, {})[name] = {"strict_delta_pp": strict["delta_percentage_points"],
                                                     "solver_verified_delta_pp": numerical["delta_percentage_points"]}
    record(events, "pipeline_complete", {"arm": "prompted-base", "tasks": str(SMOKE), "comparisons": outcomes})


def restorebench_arm(args: argparse.Namespace, events: TextIO) -> None:
    """Run only the Lab 10 RestoreBench pilot for the models an r6 root evaluated.

    r6b's own pilot stage failed after its evaluations and comparisons had
    completed (FM-24 recurrence: a bare ``uv`` in restorebench_lab.py), so the
    pilot is rerun here into a fresh root, exactly as ``run`` would have: same
    sources, memory fractions, corpus and scenario limit. The model list is the
    ``evaluated`` list r6's ``comparisons_complete`` recorded, so a model the
    smoke gate excluded stays excluded.

    Raises:
        RuntimeError: if the r6 root has no ``comparisons_complete`` record.
    """
    records = [json.loads(line) for line in (args.r6_root / "pipeline.jsonl").read_text().splitlines()]
    completed = [r["payload"] for r in records if r["kind"] == "comparisons_complete"]
    if not completed:
        raise RuntimeError(f"{args.r6_root} has no comparisons_complete record")
    evaluated = completed[-1]["evaluated"]
    students = {label: verify_student(directory, events) for label, directory in STUDENTS.items()}
    sources = {"qwen-base": (HF_HUB, QWEN_BASE_SNAPSHOT), "qwen-student": (students["qwen-student"], ""),
               "granite-base": (GRANITE_BASE, ""), "granite-student": (students["granite-student"], "")}
    fractions = {"qwen": GPU_MEMORY_FRACTION, "granite": GRANITE_GPU_MEMORY_FRACTION}
    for label in evaluated:
        session(label, "restorebench", *sources[label], fractions[label.split("-")[0]], args.root, events,
                [], latency=False, restorebench=True)
    record(events, "pipeline_complete", {"arm": "restorebench-pilot", "r6_root": str(args.r6_root),
                                         "evaluated": evaluated})


def main() -> None:
    """Create a fresh run root and durable journal; refuse automatic re-execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New run directory; must not exist")
    parser.add_argument("--strict-arm", action="store_true",
                        help="Run only the strict-tool-calling 2x2 on the smoke suite (backlog 0011)")
    parser.add_argument("--prompted-arm", action="store_true",
                        help="Run only G0's prompted-base baseline on the smoke suite (needs --r6-root, --strict-root)")
    parser.add_argument("--restorebench-arm", action="store_true",
                        help="Run only Lab 10's RestoreBench pilot for --r6-root's evaluated models")
    parser.add_argument("--r6-root", type=Path, help="Completed r6 root (its student smoke runs)")
    parser.add_argument("--strict-root", type=Path, help="Completed strict-arm root (its plain base cells)")
    parser.add_argument("--student", action="append", default=[], metavar="LABEL=DIR",
                        help="Evaluate this trained student for LABEL (qwen-student / granite-student)")
    parser.add_argument("--suite", action="append", default=[], metavar="NAME=EXAMPLES",
                        help="A further frozen suite run in each model's benchmark session")
    args = parser.parse_args()
    pairs = {"--student": args.student, "--suite": args.suite}
    for flag, values in pairs.items():
        if any("=" not in value for value in values):
            parser.error(f"{flag} takes NAME=PATH")
    args.students = {k: Path(v) for k, v in (value.split("=", 1) for value in args.student)}
    args.suites = {k: Path(v) for k, v in (value.split("=", 1) for value in args.suite)}
    if not set(args.students) <= set(STUDENTS):
        parser.error(f"--student labels are {sorted(STUDENTS)}")
    if args.prompted_arm and not (args.r6_root and args.strict_root):
        parser.error("--prompted-arm needs --r6-root and --strict-root")
    if args.restorebench_arm and not args.r6_root:
        parser.error("--restorebench-arm needs --r6-root")
    args.root.mkdir()
    # A supervisor's stop arrives as SIGTERM; unwind so the journal records it.
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    with (args.root / "pipeline.jsonl").open("x") as events:
        record(events, "driver_start", {"pid": os.getpid(), "unix_time": time.time(), "smoke_min_pass": SMOKE_MIN_PASS,
            "source_sha256": {str(p.relative_to(REPO)): digest(p)
                              for parent in (REPO / "wattson/inference", REPO / "wattson/datasets", REPO / "wattson/training")
                              for p in parent.glob("*.py")}})
        try:
            (prompted_arm if args.prompted_arm else strict_arm if args.strict_arm
             else restorebench_arm if args.restorebench_arm else run)(args, events)
        except Exception as error:
            record(events, "driver_error", {"type": type(error).__name__, "message": str(error)})
            raise
        except SystemExit as stop:
            record(events, "driver_stopped", {"exit_code": stop.code, "unix_time": time.time()})
            raise


if __name__ == "__main__":
    main()
