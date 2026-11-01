"""Run G3's GRPO pilot from the r7 Qwen student, then compare it with that student (backlog 0017).

0017's cheapest first step: "a GRPO pilot on the [r7] Qwen student over generated load-scale fits
drawn from [several] networks, scored on calls to reach tolerance against bisection".

1. For each iteration: serve the current policy alone and sample rollouts on the procedural pool
   (``training.grpo rollout``). Stop it, then update in the training image (``training.grpo
   update``) and verify the new student's hashes. Serving and training never overlap (FM-10).
2. Serve the r7 student, then the final RL student, each on the held-out pool (snem-1803 and
   ieee39, which no pool trains on) and on the smoke suite (no-regression check). Both run greedy
   through the evaluation's harness.
3. Write strict and solver-verified comparisons. Record each passed load-scale fit's tool calls
   against bisection's for the same task, and every directory's error ledger (PC-REQ-4).

Every stage is journaled to ``<root>/pipeline.jsonl`` as in ``run_agent_study``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import sys
import time
from pathlib import Path
from typing import TextIO

import subprocess
from typing import TypedDict

from wattson.inference.tool_probe import record
from wattson.training import run_comparison
from wattson.training.grpo import WEIGHTINGS
from wattson.training.run_agent_study import SMOKE, record_ledgers, session, verify_student
from wattson.training.run_comparison import (GPU_MEMORY_FRACTION, HF_HUB, MEMINFO, await_memory, command, meminfo_bytes,
                                     serve, stop_server, write_comparison, write_numerical_comparison)

POOL: Path = Path("data/tasks/procedural-pool-20261024-v4/examples.jsonl")
HELD_OUT: Path = Path("data/tasks/procedural-heldout-20261024-v4/examples.jsonl")
CONFIG: Path = Path("configs/agent/config.grpo-qwen35-4b.yaml")
ADAPTER_NAME: str = "qwen35-4b-agent-lora"  # config.qwen35-4b.r7.yaml output.adapter_name
# training/agent/Containerfile's image, as scripts/launch_agent_sft.sh pins it.
TRAINING_IMAGE: str = "localhost/wattson-train:local"  # built locally from containers/
# Pilot size: 2 iterations x 64 tasks x 4 samples = 512 rollouts. At r6b's ~36 s of summed request
# time per Qwen-student attempt and 8 in flight, about 20 min of sampling per iteration (an
# estimate). 4 samples is the smallest group that gives a spread of rewards; 0.8 is a common
# sampling temperature, chosen, not tuned.
ITERATIONS: int = 2
TASKS_PER_ITERATION: int = 64
SAMPLES_PER_TASK: int = 4
TEMPERATURE: float = 0.8
ROLLOUT_SEED: int = 20261024
# A floor, not the peak: the update holds one 4B model, its LoRA optimizer state and one <=8k-token
# sequence's activations. The first SFT run's ~93 GB was allocator caching before
# expandable_segments, which the update container sets. Half of memory, plus await_memory's 8 GiB margin.
TRAINING_MEMORY_FRACTION: float = 0.5
# Stage deadlines, 3x the wall time measured in GRPO pilot r7's iteration 0 (rollouts 2,218 s, update
# 4,830 s; pipeline.jsonl stage_start/stage_exit). Iteration 1's rollouts then hung for hours with an idle server
# and no deadline; 3x is a chosen margin, not a tuned value. The merged-student evaluation stages are not bounded.
ROLLOUT_STAGE_TIMEOUT_S: float = 3 * 2218.0
UPDATE_STAGE_TIMEOUT_S: float = 3 * 4830.0
PORT: int = 8101
POLICY_MODEL: str = "wattson-qwen-policy"


class Profile(TypedDict):
    """Where the pilot runs: images, GPU share, memory waits, and services it suspends."""

    inference_image: str
    inference_python: str
    gpu_memory_fraction: float
    await_host_memory: bool
    training_image: str
    suspend_units: list[str]
    # Extra `podman run` arguments for the model server and for the update container.
    inference_container_args: list[str]
    training_container_args: list[str]


PROFILES: dict[str, Profile] = {
    # GB10: unified memory, the r6/r7 images.
    "gb10": Profile(inference_image=run_comparison.INFERENCE_IMAGE, inference_python=run_comparison.INFERENCE_PYTHON,
                    gpu_memory_fraction=GPU_MEMORY_FRACTION, await_host_memory=True, training_image=TRAINING_IMAGE,
                    suspend_units=[], inference_container_args=[], training_container_args=[]),
    # Discrete 24 GB GPU profile. Upstream vLLM 0.31.0, and the x86 training image built from
    # training/agent/Containerfile.x86 at the GB10 image's package versions. A discrete GPU's
    # memory is not host memory, so no host wait. vLLM takes 0.85 of the card; the 4B weights need
    # ~8.5 GB, which leaves the rest for KV cache. The inference service holding the card is
    # suspended for the pilot only, and restarted when it ends or fails.
    # Containers carry a memory cap; the pilot complies rather than taking the unlimited opt-out.
    # 31 GB of host RAM, ~18 GB available, and the two never run at once: the server needs ~8 GB to
    # load the 4B weights plus its runtime (14 GB); the update loads the base twice, once to train
    # and once to merge (16 GB).
    "discrete-24gb": Profile(inference_image="docker.io/vllm/vllm-openai:v0.31.0", inference_python="/usr/bin/python3",
                      gpu_memory_fraction=0.85, await_host_memory=False,
                      training_image="localhost/wattson-train:local",
                      suspend_units=[],
                      inference_container_args=["--memory", "14g", "--memory-swap", "14g"],
                      training_container_args=["--memory", "16g", "--memory-swap", "16g"]),
}


def apply_profile(profile: Profile) -> None:
    """Point run_comparison.serve (used directly and by run_agent_study.session) at this host's images."""
    run_comparison.INFERENCE_IMAGE = profile["inference_image"]
    run_comparison.INFERENCE_PYTHON = profile["inference_python"]
    run_comparison.AWAIT_HOST_MEMORY = profile["await_host_memory"]
    run_comparison.INFERENCE_CONTAINER_ARGS = tuple(profile["inference_container_args"])


def calls_against_bisection(directory: Path, tasks: Path) -> dict[str, object]:
    """Tool calls of each passed load-scale fit against bisection's on the same task."""
    from wattson.training.rl_reward import bisection_calls

    rows = {json.loads(line)["example_id"]: json.loads(line) for line in tasks.read_text().splitlines() if line.strip()}
    deltas = []
    for line in (directory / "results.jsonl").read_text().splitlines():
        result = json.loads(line)["payload"] if json.loads(line).get("kind") == "result" else json.loads(line)
        task = rows.get(result.get("example_id"))
        if not task or task["subfamily"] != "pf-load-scale-fit" or not result.get("passed"):
            continue
        events = [json.loads(e) for e in (directory / f"{result['example_id']}.jsonl").read_text().splitlines()]
        calls = sum(e["kind"] == "tool_request" for e in events)
        deltas.append(calls - bisection_calls(task))
    return {"passed_load_scale_fits": len(deltas), "median_calls_minus_bisection": statistics.median(deltas) if deltas else None,
            "fewer_than_bisection": sum(d < 0 for d in deltas)}


def run(args: argparse.Namespace, events: TextIO) -> None:
    """Iterate rollout and update, then evaluate both students; each stage only after the last."""
    root, profile = args.root, PROFILES[args.profile]
    fraction = profile["gpu_memory_fraction"]
    merged = verify_student(args.student, events)
    adapter = args.student / ADAPTER_NAME
    if args.resume:  # the iterations finished; only evaluation is left (see main's --resume)
        merged = verify_student(root / f"policy-{args.iterations}", events)
    for iteration in range(0 if not args.resume else args.iterations, args.iterations):
        name = f"wattson-{root.name}-policy-{iteration}"
        rollouts = root / f"rollouts-{iteration}"
        try:
            serve(merged, name, POLICY_MODEL, "qwen3", root, events, memory_fraction=fraction, port=PORT)
            command([sys.executable, "-m", "wattson.training.grpo", "rollout", "--base-url", f"http://127.0.0.1:{PORT}",
                     "--model", POLICY_MODEL, "--tasks", str(POOL), "--out", str(rollouts), "--iteration", str(iteration),
                     "--tasks-per-iteration", str(TASKS_PER_ITERATION), "--k", str(SAMPLES_PER_TASK),
                     "--temperature", str(TEMPERATURE), "--seed", str(args.seed)], root / f"rollouts-{iteration}.log", events, ROLLOUT_STAGE_TIMEOUT_S)
        finally:
            stop_server(name, root, events)
        record(events, "rollouts_complete", json.loads((rollouts / "summary.json").read_text()))
        policy = root / f"policy-{iteration + 1}"
        if profile["await_host_memory"]:
            await_memory(f"update-{iteration}", int(TRAINING_MEMORY_FRACTION * meminfo_bytes("MemTotal", MEMINFO)), events)
        command(["podman", "run", "--rm", "--name", f"wattson-{root.name}-update-{iteration}",
                 *profile["training_container_args"], "--device", "nvidia.com/gpu=all",
                 "--network", "none", "--security-opt", "label=disable", "-v", f"{Path.cwd()}:/workspace", "-w", "/workspace",
                 "-v", f"{HF_HUB}:/hub:ro", "-e", "HF_HUB_OFFLINE=1", "-e", "TRANSFORMERS_OFFLINE=1", "-e", "PYTHONUNBUFFERED=1",
                 "-e", "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True", profile["training_image"],
                 "python", "-m", "wattson.training.grpo", "update", "--config", str(CONFIG), "--rollouts", str(rollouts),
                 "--init-adapter", str(adapter), "--out", str(policy), "--weighting", args.weighting,
                 *(["--seed", str(args.update_seed)] if args.update_seed is not None else [])], root / f"update-{iteration}.log", events, UPDATE_STAGE_TIMEOUT_S)
        merged, adapter = verify_student(policy, events), policy / "adapter"
        record(events, "update_complete", {"iteration": iteration, "policy": str(policy)})

    students = {"qwen-student": args.student / "merged-bf16", "qwen-rl": merged}
    for label, source in students.items():
        pending = [(suite, tasks, out) for suite, tasks, out in
                   (("heldout", HELD_OUT, root / f"{label}-heldout"), ("smoke", SMOKE, root / f"{label}-smoke"))
                   if not (out / "summary.json").exists()]  # --resume: keep evaluations that already finished
        if pending:
            session(label, "pilot", source, "", fraction, root, events, pending, latency=False)
    outcomes = {}
    for suite in ("heldout", "smoke"):
        first, second = root / f"qwen-student-{suite}", root / f"qwen-rl-{suite}"
        strict = write_comparison(first, second, root / f"qwen-student-vs-qwen-rl-{suite}.json", events)
        numerical = write_numerical_comparison(first, second, root / f"qwen-student-vs-qwen-rl-{suite}-numerical.json", events)
        outcomes[suite] = {"strict_delta_pp": strict["delta_percentage_points"],
                           "solver_verified_delta_pp": numerical["delta_percentage_points"]}
    calls = {label: calls_against_bisection(root / f"{label}-heldout", HELD_OUT) for label in students}
    record(events, "calls_against_bisection", calls)
    record_ledgers(root, events)
    record(events, "pipeline_complete", {"arm": "grpo-pilot", "comparisons": outcomes, "calls_against_bisection": calls})


def main() -> None:
    """Create a fresh run root and durable journal; refuse automatic re-execution."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="New run directory; must not exist")
    parser.add_argument("--student", type=Path, required=True, help="The r7 Qwen student's training directory")
    parser.add_argument("--iterations", type=int, default=ITERATIONS)
    parser.add_argument("--seed", type=int, default=ROLLOUT_SEED, help="Rollout seed: which tasks each iteration draws")
    parser.add_argument("--update-seed", type=int, default=None, help="Update seed; default is the config's")
    parser.add_argument("--weighting", choices=WEIGHTINGS, default="grpo",
                        help="grpo (the pilot) or rft (rejection-sampling control, backlog 0039)")
    parser.add_argument("--resume", action="store_true",
                        help="Root exists with every iteration done: re-run only the evaluations lacking a summary.json "
                             "(a failed one's directory and its '-pilot' logs must first be moved aside)")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="gb10")
    args = parser.parse_args()
    apply_profile(PROFILES[args.profile])
    if not args.resume:
        args.root.mkdir()
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    with (args.root / "pipeline.jsonl").open("a" if args.resume else "x") as events:
        record(events, "driver_start", {"pid": os.getpid(), "unix_time": time.time(), "student": str(args.student),
                                        "iterations": args.iterations, "seed": args.seed, "update_seed": args.update_seed,
                                        "weighting": args.weighting, "tasks_per_iteration": TASKS_PER_ITERATION,
                                        "samples_per_task": SAMPLES_PER_TASK, "temperature": TEMPERATURE,
                                        "profile": args.profile, **PROFILES[args.profile]})
        suspended = []
        try:
            for unit in PROFILES[args.profile]["suspend_units"]:
                subprocess.run(["systemctl", "--user", "stop", unit], check=True)
                suspended.append(unit)
                record(events, "unit_suspended", {"unit": unit})
            run(args, events)
        except Exception as error:
            record(events, "driver_error", {"type": type(error).__name__, "message": str(error)})
            raise
        except SystemExit as stop:
            record(events, "driver_stopped", {"exit_code": stop.code, "unix_time": time.time()})
            raise
        finally:
            # Restart whatever was suspended, whether the pilot finished, failed or was stopped.
            for unit in suspended:
                restarted = subprocess.run(["systemctl", "--user", "start", unit], check=False).returncode
                record(events, "unit_restarted", {"unit": unit, "returncode": restarted})


if __name__ == "__main__":
    main()
