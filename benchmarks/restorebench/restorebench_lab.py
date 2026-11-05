"""Lab 10: run PowerAgentBench's RestoreBench (Level 3) on this node, with local models.

Steps (each re-runnable; ``--step check`` is the self-check):

- ``fetch``   -- clone PowerAgentBench at the pinned commit into ``data/`` and verify
  the hashes of every RestoreBench file this lab relies on (fetch_restorebench.py).
- ``verify``  -- RestoreBench's own corpus verifier (``restorebench-verify``) on both
  frozen corpora: hashes, non-convergence on load, and each scenario's witness.
- ``witness`` -- score every scenario's published witness with the standalone
  scorer (``restorebench-score --batch``): the solver's own verdict on whether
  each scenario is solvable as shipped, on this machine.
- ``check``   -- re-run ``witness`` and diff it against ``expected_witness_summary.json``.
- ``agent``   -- RestoreBench's single tool-using agent (configuration 2) on a
  locally served model, through ``agent_runner.py`` and ``local_provider.py``.

RestoreBench runs in its own ``uv`` environment, built from its own lockfile
(``uv sync --frozen`` in level_3): pandapower 3.4.0, not this repo's 3.5.4, so its
solver policies are the published ones. This script only orchestrates it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

LAB: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = LAB.parents[1]
LEVEL_3: Path = REPO_ROOT / ".artifacts/restorebench-source/benchmarks/steady/level_3"
CORPORA: tuple[str, ...] = ("ieee118", "pegase89")
RESULTS: Path = REPO_ROOT / "out" / "restorebench"
EXPECTED_WITNESS: Path = LAB / "expected_witness_summary.json"
# The systemd user manager's PATH has no ~/.local/bin, so a bare "uv" fails when
# the agent study runs this lab from a unit (AGENTS.md FM-24; r6b's Lab 10 pilot,
# 2026-10-23). Same fallback as the launch scripts.
UV_FALLBACK: Path = Path.home() / ".local/bin/uv"


def uv() -> str:
    """The uv executable: from PATH, else the standard user install.

    Raises:
        RuntimeError: if neither exists.
    """
    found = shutil.which("uv") or (str(UV_FALLBACK) if UV_FALLBACK.is_file() else None)
    if found is None:
        raise RuntimeError(f"uv not found on PATH or at {UV_FALLBACK}")
    return found


def in_restorebench(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command inside RestoreBench's frozen environment, from its own directory."""
    return subprocess.run([uv(), "run", "--frozen", "--project", str(LEVEL_3), *args], cwd=LEVEL_3,
                          text=True, capture_output=capture, check=False)


def ensure_environment() -> None:
    """Build RestoreBench's environment from its lockfile (no-op once built).

    Raises:
        RuntimeError: if the checkout is missing or ``uv sync --frozen`` fails.
    """
    if not (LEVEL_3 / "uv.lock").is_file():
        raise RuntimeError("RestoreBench is not fetched; run --step fetch first")
    subprocess.run([uv(), "sync", "--frozen", "--project", str(LEVEL_3)], cwd=LEVEL_3, check=True,
                   capture_output=True)


def step_verify() -> dict[str, Any]:
    """Run restorebench-verify per corpus and record its verdict and any failing scenarios."""
    report = {}
    for corpus in CORPORA:
        run = in_restorebench("restorebench-verify", "--dataset-dir", f"dataset/{corpus}", capture=True)
        text = run.stdout + run.stderr
        verified = re.search(r"verified:\s+(\d+)/(\d+)", text)
        report[corpus] = {"exit_code": run.returncode,
                          "verified": int(verified.group(1)) if verified else None,
                          "scenarios": int(verified.group(2)) if verified else None,
                          "failures": re.findall(r"^\s+(S\d{4})\s+(.+)$", text, flags=re.MULTILINE)}
    return report


def step_witness() -> dict[str, Any]:
    """Score every published witness; return per-corpus status counts and non-successes."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    report = {}
    for corpus in CORPORA:
        out = RESULTS / f"{corpus}-witness-scores.jsonl"
        out.unlink(missing_ok=True)
        run = in_restorebench("python", str(LAB / "witness_scorer.py"), "--corpus-dir", f"dataset/{corpus}",
                              "--out", str(out), capture=True)
        if run.returncode:
            raise RuntimeError(f"witness scoring failed for {corpus}: {run.stderr[-2000:]}")
        scores = [json.loads(line) for line in out.read_text().splitlines()]
        report[corpus] = {
            "witnesses": len(scores),
            "success": sum(s["status"] == "SUCCESS" for s in scores),
            "not_success": {s["scenario_id"]: s["status"] for s in scores if s["status"] != "SUCCESS"},
        }
    return report


def main() -> int:
    """Dispatch one step and print its JSON report; ``check`` exits non-zero on drift."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", required=True, choices=["fetch", "verify", "witness", "check", "agent"])
    parser.add_argument("--base-url", help="agent: OpenAI-compatible server root")
    parser.add_argument("--model", action="append", help="agent: served model name; repeatable")
    parser.add_argument("--corpus", choices=CORPORA, default="ieee118", help="agent: which frozen corpus")
    parser.add_argument("--limit", type=int, help="agent: first N scenarios only")
    parser.add_argument("--results-dir", type=Path, help="agent: new directory for RestoreBench's cells")
    args = parser.parse_args()
    if args.step == "fetch":
        return subprocess.run([sys.executable, str(LAB / "fetch_restorebench.py")], check=False).returncode
    ensure_environment()
    if args.step == "verify":
        report = step_verify()
        (RESULTS / "verify.json").parent.mkdir(exist_ok=True)
        (RESULTS / "verify.json").write_text(json.dumps(report, indent=2) + "\n")
    elif args.step in {"witness", "check"}:
        report = step_witness()
        (RESULTS / "witness_summary.json").write_text(json.dumps(report, indent=2) + "\n")
        if args.step == "check":
            expected = json.loads(EXPECTED_WITNESS.read_text())
            ok = report == expected
            print(json.dumps(report, indent=2))
            print("PASS witness scores match expected_witness_summary.json" if ok
                  else f"FAIL witness scores differ from {EXPECTED_WITNESS.name}")
            return 0 if ok else 1
    else:
        if not (args.base_url and args.model and args.results_dir):
            parser.error("--step agent needs --base-url, --model and --results-dir")
        if args.results_dir.exists():
            parser.error(f"{args.results_dir} exists; RestoreBench would resume into it")
        command = ["python", str(LAB / "agent_runner.py"), "--base-url", args.base_url,
                   "--data-dir", f"dataset/{args.corpus}", "--results-dir", str(args.results_dir.resolve())]
        command += [part for model in args.model for part in ("--model", model)]
        command += ["--limit", str(args.limit)] if args.limit else []
        return in_restorebench(*command).returncode
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
