#!/usr/bin/env python3
"""Lab 1 agent mode: give the load-scale-fit task to a served language model
through the real tool harness, grade it by re-solving the network, and print
it next to the committed bisection result.

Usage:
    uv run benchmarks/lab01-simple-loadflow-fit/agent_mode.py \
        --base-url http://127.0.0.1:8000 --model wattson

The task constants (target bus, field SCADA reading, tolerance, scale bounds)
are read from run.py itself, not duplicated here.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import wattson.cli
from wattson import fetch
from wattson.inference.evaluate_agent import grade
from wattson.inference.studies import run_study_trial
from wattson.training.unsloth.evaluate import extract_scale

LAB_DIR = Path(__file__).resolve().parent
EXPECTED_FILE = LAB_DIR / "expected_results.json"
CASE_ID: str = "snem-sa-2000"          # the case Lab 1's snemSA.m is served as by the tool server (wattson.inference.powermcp.CASE_FILES)
HARNESS_TIMEOUT_S: int = 600           # one attempt; the evaluator allows each inference call up to 1800 s, a fit needs a few
RESOLVE_MAX_ITER: int = 50             # the iteration cap the tool schema requires and every trained call uses
STDERR_SHOWN_CHARS: int = 500


def _load_run_py_constants() -> dict[str, Any]:
    """Import run.py by file path and read its named constants."""
    spec = importlib.util.spec_from_file_location("lab1_run", LAB_DIR / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {
        "TARGET_BUS": mod.TARGET_BUS,
        "FIELD_SCADA_VOLTAGE_PU": mod.FIELD_SCADA_VOLTAGE_PU,
        "FIT_TOLERANCE_PU": mod.FIT_TOLERANCE_PU,
        "SCALE_LO": mod.SCALE_LO,
        "SCALE_HI": mod.SCALE_HI,
        "EXPECTED_FILE": mod.EXPECTED_FILE,
    }


def build_task_row(constants: dict[str, Any]) -> dict[str, Any]:
    """Build the task row in the shape the evaluator grades."""
    prompt = (
        f"Using the power flow solver, find the scale factor s in "
        f"[{constants['SCALE_LO']}, {constants['SCALE_HI']}] applied to every "
        f"load's active and reactive demand on case '{CASE_ID}' such that "
        f"the voltage at bus {constants['TARGET_BUS']} equals "
        f"{constants['FIELD_SCADA_VOLTAGE_PU']} pu (within "
        f"{constants['FIT_TOLERANCE_PU']} pu). Return only the scale factor."
    )
    return {
        "example_id": "lab01-agent",
        "family": "lab",
        "subfamily": "pf-load-scale-fit",
        "topology": CASE_ID,
        "task": {
            "prompt": prompt,
            "inputs": {
                "case": CASE_ID,
                "element": f"bus {constants['TARGET_BUS']}",
                "metric_unit": "pu",
                "scale_lo": constants["SCALE_LO"],
                "scale_hi": constants["SCALE_HI"],
                "target": constants["FIELD_SCADA_VOLTAGE_PU"],
                "tolerance": constants["FIT_TOLERANCE_PU"],
            },
        },
        "expected": {"answer": "", "structured": {}, "validity": "valid"},
    }


def count_solver_calls(journal_path: Path) -> int:
    """Count solver calls from the journal: each run_study_trial is 1; for
    run_python, count trials from the matching tool_response."""
    calls = 0
    pending_python: dict[str, dict[str, Any]] = {}
    for line in journal_path.read_text().splitlines():
        event = json.loads(line)
        if event["kind"] == "tool_request":
            name = event["payload"]["function"]["name"]
            if name == "run_study_trial":
                calls += 1
            elif name == "run_python":
                pending_python[event["payload"]["id"]] = event["payload"]
        elif event["kind"] == "tool_response":
            tid = event["payload"].get("tool_call_id")
            if tid in pending_python:
                try:
                    content = event["payload"]["result"]["content"][0]["text"]
                    body = json.loads(content)          # {"status": ..., "data": {"stdout": ..., "trials": [...]}, "error": ...}
                    trials = (body.get("data") or {}).get("trials")
                    calls += len(trials) if isinstance(trials, list) else 1
                except (KeyError, json.JSONDecodeError, TypeError):
                    calls += 1
                del pending_python[tid]
    return calls


def attempt_ended(journal_path: Path) -> str:
    """How the attempt ended, from the journal's terminal event (answered, guard_refused, turn_budget_exhausted, ...)."""
    events = [json.loads(line) for line in journal_path.read_text().splitlines() if line.strip()]
    terminal = [e for e in events if e["kind"] == "terminal"]
    return str(terminal[-1]["payload"].get("state")) if terminal else "no terminal event"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Model server root (accepts .../v1 form)")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--enforce", action="store_true", help="Enforce provenance guard")
    parser.add_argument("--out", default="out/lab01-agent", help="Output directory")
    args = parser.parse_args(argv)

    root = wattson.cli.server_root(args.base_url)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Load constants from run.py
    constants = _load_run_py_constants()

    # Build task and write prompt
    task = build_task_row(constants)
    prompt_file = out / "prompt.txt"
    prompt_file.write_text(task["task"]["prompt"])

    # Remove old journal if present (probe refuses to overwrite)
    journal = out / "journal.jsonl"
    if journal.exists():
        journal.unlink()

    # Run the harness as a subprocess
    command = [
        sys.executable, "-m", "wattson.inference.tool_probe",
        "--base-url", root, "--model", args.model,
        "--prompt", str(prompt_file),
        "--journal", str(journal),
        "--study-tools", "--python-tools", "--python-report-fields",
    ]
    if args.enforce:
        command.append("--enforce-provenance")

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=HARNESS_TIMEOUT_S)
        if result.returncode != 0 and not journal.exists():
            print(f"FAIL: tool_probe exited {result.returncode} before writing a journal")
            if result.stderr:
                print(result.stderr[:STDERR_SHOWN_CHARS])
            return 1
    except subprocess.TimeoutExpired:
        print(f"FAIL: tool_probe timed out after {HARNESS_TIMEOUT_S}s")
        return 1
    except FileNotFoundError:
        print("FAIL: could not find Python executable")
        return 1

    # Grade the attempt
    try:
        result = grade(task, journal)
    except Exception as e:
        print(f"FAIL: grading error: {e}")
        return 1

    # Extract the answered scale
    scale = extract_scale(result["answer"])
    if scale is None:
        print(f"FAIL: no scale in the answer ({attempt_ended(journal)}): {result['answer'][:100]!r}")
        return 1

    # Re-solve at the answered scale
    try:
        study = run_study_trial(
            network_id=CASE_ID,
            operation="load_scale",
            scale=scale,
            observation="bus_voltage",
            observation_id=constants["TARGET_BUS"],
            max_iter=RESOLVE_MAX_ITER,
        )
        voltage = study.data["metric"]
    except Exception as e:
        print(f"FAIL: re-solve error: {e}")
        return 1

    residual = voltage - constants["FIELD_SCADA_VOLTAGE_PU"]

    # Count solver calls from the journal
    solver_calls = count_solver_calls(journal)

    # Read bisection result from expected_results.json
    expected = json.loads(EXPECTED_FILE.read_text())

    # Print comparison table
    print(f"{'method':<30} | {'scale':>7} | {f'bus {constants['TARGET_BUS']} voltage (pu)':>20} | {'residual (pu)':>12} | {'solver calls':>12}")
    print("-" * 110)
    print(
        f"bisection (committed)        | {expected['fitted_scale']:>7.3f} | "
        f"{expected['fitted_voltage_pu']:>20.6f} | {expected['residual_pu']:>12.6f} | "
        f"{expected['iterations']:>12}"
    )
    print(
        f"{args.model:<30} | {scale:>7.4f} | "
        f"{voltage:>20.6f} | {residual:>12.6f} | "
        f"{solver_calls:>12}"
    )
    print()
    print(
        f"{'PASS' if result['passed'] else 'FAIL'} "
        f"passed={result['passed']} numerical={result['numerical_pass']} "
        f"tool_evidence={result['tool_evidence_pass']} provenance={result['provenance_pass']}"
    )

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
