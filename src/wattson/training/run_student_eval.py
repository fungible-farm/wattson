"""Evaluate any model directory on the Phase 1 suites with the shared harness (backlog 0039).

``eval`` serves one model alone (vLLM, local only, via ``run_agent_study.session``), runs the chosen suites
at the shared concurrency, budgets and scoring contract, and journals to ``<root>/pipeline.jsonl``. ``compare``
writes the strict and solver-verified paired comparison between two finished result directories.

Used for the stock comparators (Gemma 4 E4B-it) and for the held-out runs of stock Qwen and Granite, so every
arm in the claim runs the same tasks. On a profile with ``suspend_units`` the inference service is
stopped for the run and restarted in ``finally``, as ``run_grpo_pilot`` does.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from wattson.inference.tool_probe import record
from wattson.training import run_comparison
from wattson.training import run_agent_study
from wattson.training.run_agent_study import BENCHMARK, SMOKE, record_ledgers, session
from wattson.training.run_comparison import write_comparison, write_numerical_comparison
from wattson.training.run_grpo_pilot import HELD_OUT, PROFILES, apply_profile

# Suite name -> task file. The held-out 90 fits are the primary endpoint; the others are secondary (0039).
SUITES: dict[str, Path] = {
    "heldout": HELD_OUT,
    "smoke": SMOKE,
    "benchmark": BENCHMARK,
    "judgement": Path("data/suites/judgement-20261024-v2/examples.jsonl"),
    "unsolvable": Path("data/suites/unsolvable-20261024-v2/examples.jsonl"),
    # Worst-outage questions on snem-1803 and ieee39, in the original wording and a never-trained wording.
    "planning": Path("data/suites/planning-heldout-20261029/examples.jsonl"),
    # Questions that need arithmetic over several solver results (a sum, a difference, a mean) and a new metric.
    "derived": Path("data/suites/derived-heldout-20261030/examples.jsonl"),
}


def evaluate(args: argparse.Namespace, events) -> None:
    """Serve the model and run each requested suite on it."""
    profile = PROFILES[args.profile]
    run_comparison.TOOL_CALL_PARSER = args.tool_parser
    if args.python_tools:
        run_agent_study.EVALUATOR_FLAGS = ("--python-tools", *(("--python-report-fields",) if args.python_report_fields else ()))
    suites = [(name, SUITES[name], args.root / f"{args.label}-{name}") for name in args.suites]
    fraction = args.gpu_memory_fraction if args.gpu_memory_fraction is not None else profile["gpu_memory_fraction"]
    session(args.label, "eval", args.source, args.model_path, fraction, args.root, events,
            suites, latency=False, reasoning=args.reasoning or "")
    record_ledgers(args.root, events)
    record(events, "evaluation_complete", {"label": args.label, "suites": args.suites})


def compare(args: argparse.Namespace) -> None:
    """Strict and solver-verified paired comparison of two finished result directories."""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with (args.out.parent / f"{args.out.name}.events.jsonl").open("a") as events:
        spanned = frozenset(args.span)
        write_comparison(args.base, args.student, args.out.parent / f"{args.out.name}.json", events, spanned)
        write_numerical_comparison(args.base, args.student, args.out.parent / f"{args.out.name}-numerical.json",
                                   events, spanned)


def main() -> None:
    """``eval`` or ``compare``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    ev = sub.add_parser("eval")
    ev.add_argument("--root", type=Path, required=True, help="New run directory; must not exist")
    ev.add_argument("--label", required=True)
    ev.add_argument("--source", type=Path, required=True, help="Directory mounted read-only at /student")
    ev.add_argument("--model-path", default="", help="Path inside --source (the hub layout needs its whole tree)")
    ev.add_argument("--tool-parser", default="qwen3_coder", help="vLLM --tool-call-parser")
    ev.add_argument("--reasoning", default="", help="vLLM --reasoning-parser; empty adds none")
    ev.add_argument("--suites", type=lambda s: s.split(","), default=["heldout", "smoke"])
    ev.add_argument("--profile", choices=sorted(PROFILES), default="gb10")
    ev.add_argument("--python-tools", action="store_true",
                    help="Serve run_python to the model; recorded as tool_set study+python")
    ev.add_argument("--python-report-fields", action="store_true",
                    help="With --python-tools: serve the v2 shell, whose trial replies carry the report numbers")
    ev.add_argument("--gpu-memory-fraction", type=float, default=None,
                    help="vLLM --gpu-memory-utilization; default is the profile's (sized for the 4B Qwen). "
                         "Granite 8B needs run_comparison.GRANITE_GPU_MEMORY_FRACTION")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--base", type=Path, required=True)
    cmp_.add_argument("--student", type=Path, required=True)
    cmp_.add_argument("--out", type=Path, required=True, help="Stem of the comparison JSON files")
    cmp_.add_argument("--span", action="append", default=[], help="A condition this comparison deliberately spans, "
                      "e.g. tool_set when comparing a python-shell run with a plain-tool run")
    args = parser.parse_args()
    if args.step == "compare":
        compare(args)
        return
    unknown = [name for name in args.suites if name not in SUITES]
    if unknown:
        parser.error(f"unknown suites {unknown}; choose from {sorted(SUITES)}")
    apply_profile(PROFILES[args.profile])
    args.root.mkdir()
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    with (args.root / "pipeline.jsonl").open("x") as events:
        record(events, "driver_start", {"pid": os.getpid(), "unix_time": time.time(), "label": args.label,
                                        "source": str(args.source), "model_path": args.model_path,
                                        "tool_parser": args.tool_parser, "reasoning": args.reasoning,
                                        "suites": args.suites, "profile": args.profile})
        suspended = []
        try:
            for unit in PROFILES[args.profile]["suspend_units"]:
                subprocess.run(["systemctl", "--user", "stop", unit], check=True)
                suspended.append(unit)
                record(events, "unit_suspended", {"unit": unit})
            evaluate(args, events)
        except Exception as error:
            record(events, "driver_error", {"type": type(error).__name__, "message": str(error)})
            raise
        except SystemExit as stop:
            record(events, "driver_stopped", {"exit_code": stop.code, "unix_time": time.time()})
            raise
        finally:
            for unit in suspended:
                code = subprocess.run(["systemctl", "--user", "start", unit], check=False).returncode
                record(events, "unit_restarted", {"unit": unit, "returncode": code})


if __name__ == "__main__":
    main()
