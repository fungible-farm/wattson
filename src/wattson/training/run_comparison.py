"""Complete Qwen and Granite experiments in sequence, retaining all evidence.

Serve the pinned Qwen base and the original saved Qwen student side by side and
evaluate both while the pinned Granite model trains on the same split, then
evaluate Granite base and then Granite student, one server at a time.
``--qwen-from`` instead reuses a completed earlier root's verified Qwen runs and
Granite training and runs only the Granite stages. This driver owns every stage it starts, so one supervising unit
(`systemd-run --user`, see training/granite/README.md) stops all of them. Every
GPU stage first waits for measured unified-memory headroom. No stage is retried
or truncated automatically. A failed stage leaves its logs and stops this driver.
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
from typing import Any, TextIO

import httpx

from wattson.inference.compare_agents import compare, verified_run
from wattson.inference.domain_scorecard import compare_domain
from wattson.inference.tool_probe import record
from wattson.training.granite.train import digest, verify_base

# Qualified on GB10 in the actual earlier tool-use probes; image ID is immutable.
INFERENCE_IMAGE: str = "localhost/wattson-vllm:local"  # built locally from containers/
# The interpreter inside INFERENCE_IMAGE, and whether a server must wait for host memory first. On
# GB10's unified memory vLLM's preallocation comes out of MemAvailable; on a discrete GPU it does
# not, so a host profile for one (training/run_grpo_pilot.PROFILES) turns the wait off.
INFERENCE_PYTHON: str = "/opt/venv/bin/python"
AWAIT_HOST_MEMORY: bool = True
# Extra `podman run` arguments for model servers, e.g. a host's required memory cap; none on GB10.
INFERENCE_CONTAINER_ARGS: tuple[str, ...] = ()
TRAINING_IMAGE: str = "localhost/wattson-train:local"  # built locally from containers/
# Prompt + max_tokens must fit (AGENTS.md FM-5): the largest r2/r3 prompt was
# 7,583 tokens, and tool_probe.MAX_TOKENS is 8,192, so 16,384 had no headroom.
CONTEXT_TOKENS: int = 32768
# Attempts in flight per server. One decode stream ran at ~19 tok/s with the
# GPU memory-bandwidth bound (FM-14); r3's 0.12-fraction server reported KV for
# 7.84 concurrent 16K requests yet served one. Same value for every model, so
# conditions stay identical across the comparison.
CONCURRENCY: int = 8
# r3 at 0.12 left 4.81 GiB of KV cache (128,485 tokens). 0.16 adds ~4.9 GiB,
# roughly doubling it for 8 concurrent sequences of up to 32K tokens; vLLM
# preempts rather than fails if it still runs short.
GPU_MEMORY_FRACTION: float = 0.16
# Granite's pinned weight index contains 16,761,102,336 bytes, exceeding 12%
# of the GB10's memory before runtime/KV cache. 22% served one sequence in the
# llama-swap smoke test; add the same ~4.9 GiB KV increment for concurrency.
GRANITE_GPU_MEMORY_FRACTION: float = 0.26
# Pinned Granite index weight bytes (granite42-download-verified.json). BF16
# LoRA training must hold every base weight resident, so this is a hard floor.
GRANITE_WEIGHT_BYTES: int = 16_761_102_336
# Headroom beyond a stage's own floor for the evaluator, MCP solver children and
# vLLM's API process (each well under 1 GiB RSS in r2) plus kernel/page-cache
# slack. A chosen margin, not a measurement: on 2026-10-21 the GB10 OOM-killed
# a process at 3 GB MemAvailable, so a stage must not start that close.
MEMORY_MARGIN_BYTES: int = 8 << 30
# Shared llama-swap models unload after globalTTL = 600 s idle; allow for that
# plus a spell of active use by other gateway clients before refusing.
MEMORY_WAIT_TIMEOUT_S: float = 3600.0
MEMINFO: Path = Path("/proc/meminfo")
# Two servers run side by side (base and student of one model family).
PORTS: tuple[int, int] = (8101, 8102)
# Model startup took about 90 s in the first run; allow ten minutes cold.
STARTUP_TIMEOUT_S: float = 600.0
# Poll actual process/container state every five seconds, without active retries.
POLL_INTERVAL_S: float = 5.0
BENCHMARK: Path = Path("data/suites/domain-held-out-20261020/examples.jsonl")
EXCLUDED: Path = Path("fixtures/smoke/exclude/examples.jsonl")
GRANITE_CONFIG: Path = Path("configs/unsloth/config.granite42.yaml")
REPO: Path = Path(__file__).resolve().parents[3]  # src/wattson/training/ -> the repository root
# The exact Qwen base mount and snapshot the r2 base evaluation served; the
# snapshot's files are symlinks into the hub cache, so mount the hub, not it.
# vLLM --tool-call-parser for the served model. The Qwen3.5-4B students and the Granite runs use qwen3_coder;
# another vendor's model overrides it (run_student_eval --tool-parser, backlog 0039).
TOOL_CALL_PARSER: str = "qwen3_coder"
HF_HUB: Path = Path.home() / ".cache/huggingface/.cache/hub"
QWEN_BASE_SNAPSHOT: str = "models--Qwen--Qwen3.5-4B/snapshots/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
QWEN_STUDENT: Path = Path("out/merged-bf16")


def meminfo_bytes(field: str, meminfo: Path = MEMINFO) -> int:
    """Read one /proc/meminfo field (reported in KiB) as bytes."""
    for line in meminfo.read_text().splitlines():
        name, _, value = line.partition(":")
        if name == field:
            return int(value.split()[0]) * 1024
    raise KeyError(f"{field} missing from {meminfo}")


def await_memory(stage: str, required: int, events: TextIO, meminfo: Path = MEMINFO,
                 timeout: float = MEMORY_WAIT_TIMEOUT_S, sleep: Any = time.sleep,
                 clock: Any = time.monotonic) -> int:
    """Wait for MemAvailable to cover a stage's floor plus margin, else refuse.

    GB10 memory is unified, so other tenants (llama-swap models, persistent vLLM
    backends) compete with every stage. Starting below the floor invites a
    mid-stage OOM kill, which wastes hours and leaves partial evidence.

    Returns:
        The MemAvailable bytes observed when the stage was admitted.

    Raises:
        RuntimeError: Headroom did not appear within ``timeout`` seconds.
    """
    needed = required + MEMORY_MARGIN_BYTES
    deadline = clock() + timeout
    waiting = False
    while True:
        available = meminfo_bytes("MemAvailable", meminfo)
        if available >= needed:
            record(events, "memory_admitted", {"stage": stage, "available": available, "needed": needed})
            return available
        if not waiting:
            record(events, "memory_wait", {"stage": stage, "available": available, "needed": needed})
            waiting = True
        if clock() >= deadline:
            record(events, "memory_refused", {"stage": stage, "available": available, "needed": needed})
            raise RuntimeError(f"{stage}: MemAvailable {available} < {needed} bytes after {timeout} s")
        sleep(POLL_INTERVAL_S)


def container_info(name: str) -> dict[str, Any]:
    """Inspect the exact live container handle without exposing environment secrets."""
    template = '{"image": "{{.Image}}", "command": {{json .Config.CreateCommand}}, "state": {{json .State}}}'
    return json.loads(subprocess.check_output(["podman", "inspect", name, "--format", template], text=True))


# Exit code journaled for a stage the driver killed at its deadline; 124 is coreutils ``timeout``'s.
STAGE_TIMEOUT_EXIT: int = 124


def command(args: list[str], log: Path, events: TextIO, timeout_s: float | None = None) -> None:
    """Run one stage exactly once, preserving its complete stdout and stderr.

    ``timeout_s`` bounds the stage's wall time; None (the default) waits forever.
    """
    parallel([(args, log)], events, timeout_s)


def parallel(stages: list[tuple[list[str], Path]], events: TextIO, timeout_s: float | None = None) -> None:
    """Run stages concurrently, each exactly once, then fail if any failed.

    Every stage runs to completion even when another fails: a failed Granite
    training must not discard hours of independent Qwen evaluation evidence.

    ``timeout_s`` counts from this call for every stage: one still running at the deadline is killed
    and journaled as a failure with exit code ``STAGE_TIMEOUT_EXIT`` (a hung stage otherwise holds
    the driver, and the GPU, forever: GRPO pilot r7, iteration 1's rollouts).
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    running = []
    for args, log in stages:
        record(events, "stage_start", {"command": args, "log": str(log), "unix_time": time.time()})
        output = log.open("x")
        process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, cwd=REPO)
        record(events, "stage_process", {"pid": process.pid, "command": args})
        running.append((process, output, args, log))
    failures = []
    for process, output, args, log in running:
        try:
            code = process.wait(None if deadline is None else max(deadline - time.monotonic(), 0.0))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code = STAGE_TIMEOUT_EXIT
        output.close()
        record(events, "stage_exit", {"exit_code": code, "unix_time": time.time(), "log": str(log)})
        if code:
            failures.append(f"Stage exited {code}: {args}; see {log}")
    if failures:
        raise RuntimeError("; ".join(failures))


def serve(source: Path, name: str, model: str, reasoning: str, root: Path, events: TextIO,
          memory_fraction: float = GPU_MEMORY_FRACTION, model_path: str = "", port: int = PORTS[0],
          vllm_args: tuple[str, ...] = ()) -> None:
    """Start a pinned, local-only model endpoint and wait for actual readiness.

    ``source`` is mounted read-only at /student; ``model_path`` selects a
    directory inside it (the Hugging Face hub layout needs its whole tree).
    ``vllm_args`` are appended to the vLLM command (recorded in the serving
    JSON), e.g. a study arm's ``--tool-strict-level parameter``.
    """
    # vLLM preallocates this fraction of total memory, so that is the floor.
    if AWAIT_HOST_MEMORY:
        await_memory(name, int(memory_fraction * meminfo_bytes("MemTotal")), events)
    args = ["podman", "run", "-d", "--replace", "--name", name, *INFERENCE_CONTAINER_ARGS, "--device", "nvidia.com/gpu=all",
            "--security-opt", "label=disable", "--shm-size", "1g", "-p", f"127.0.0.1:{port}:8000",
            "-v", f"{source.resolve()}:/student:ro", "-e", "HF_HUB_OFFLINE=1", "-e", "TRANSFORMERS_OFFLINE=1",
            "--entrypoint", INFERENCE_PYTHON, INFERENCE_IMAGE,
            "-m", "vllm.entrypoints.openai.api_server", "--model", f"/student/{model_path}".rstrip("/"),
            "--served-model-name", model,
            "--host", "0.0.0.0", "--port", "8000", "--dtype", "bfloat16", "--max-model-len", str(CONTEXT_TOKENS),
            "--max-num-seqs", str(CONCURRENCY), "--gpu-memory-utilization", str(memory_fraction), "--enforce-eager",
            "--enable-auto-tool-choice", "--tool-call-parser", TOOL_CALL_PARSER, *(["--reasoning-parser", reasoning] if reasoning else []),
            "--generation-config", "vllm", "--limit-mm-per-prompt", '{"image":0,"video":0}', *vllm_args]
    command(args, root / f"{name}-start.log", events)
    info = container_info(name)
    (root / f"{name}-serving.json").write_text(json.dumps(info, indent=2) + "\n")
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    with httpx.Client(timeout=POLL_INTERVAL_S) as client:
        while time.monotonic() < deadline:
            if not container_info(name)["state"]["Running"]:
                raise RuntimeError(f"Model server exited: {name}; inspect podman logs")
            try:
                response = client.get(f"http://127.0.0.1:{port}/v1/models")
                if response.status_code == 200 and model in {r["id"] for r in response.json()["data"]}:
                    record(events, "server_ready", {"container": name, "model": model})
                    return
            except httpx.TransportError:
                # A just-launched server has no listener until its weights load,
                # and rootless port forwarding resets connections until it does
                # (r3's first launch died on ReadError 104); container exit is
                # the failure signal, checked above.
                pass
            time.sleep(POLL_INTERVAL_S)
    raise RuntimeError(f"Server startup deadline exceeded: {name}")


def evaluation(model: str, directory: Path, port: int) -> tuple[list[str], Path]:
    """The full-suite command, with the same budgets and concurrency for every model."""
    return ([sys.executable, "-m", "wattson.inference.evaluate_agent", "--base-url", f"http://127.0.0.1:{port}",
             "--model", model, "--tasks", str(BENCHMARK), "--exclude", str(EXCLUDED), "--out", str(directory),
             "--concurrency", str(CONCURRENCY)],
            directory.parent / f"{directory.name}.log")


def verify_evaluation(directory: Path, events: TextIO) -> None:
    """Require a complete, untampered 306-task run before anything consumes it."""
    summary, _ = verified_run(directory)
    record(events, "evaluation_complete", {"directory": str(directory), "summary": summary})


def evaluate_pair(pair: list[tuple[str, Path, str, Path, float]], root: Path, events: TextIO,
                  extra: list[tuple[list[str], Path]] | None = None,
                  extra_floor: tuple[str, int] | None = None) -> None:
    """Serve one or two models side by side, evaluate them at once, then release them.

    Args:
        pair: (label, source, model_path, out_dir, memory_fraction) per model;
            one entry serves a single model on the first port.
        extra: Further stages (Granite training) to run alongside the evaluations.
        extra_floor: (stage, bytes) memory admission for ``extra``, checked
            after both servers hold their memory.
    """
    stages = []
    names = []
    for (label, source, model_path, directory, fraction), port in zip(pair, PORTS):
        name = f"wattson-{root.name}-{label}"
        model = f"wattson-{label}"
        reasoning = "nemotron_v3" if label.startswith("granite") else "qwen3"
        serve(source, name, model, reasoning, root, events, memory_fraction=fraction,
              model_path=model_path, port=port)
        names.append(name)
        stages.append(evaluation(model, directory, port))
    try:
        if extra_floor:
            await_memory(*extra_floor, events)
        parallel(stages + (extra or []), events)
    finally:
        # Release servers even after a failed stage, keeping their logs.
        for name in names:
            stop_server(name, root, events)
    for _label, _source, _path, directory, _fraction in pair:
        verify_evaluation(directory, events)


def stop_server(name: str, root: Path, events: TextIO) -> None:
    """Release only this experiment's server after its full evaluation completes."""
    command(["podman", "logs", name], root / f"{name}-server.log", events)
    command(["podman", "stop", name], root / f"{name}-stop.log", events)


def write_comparison(base: Path, candidate: Path, destination: Path, events: TextIO,
                     allowed_differences: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Persist the measured delta even when it fails the requested gain threshold."""
    result = compare(base, candidate, allowed_differences)
    with destination.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    record(events, "comparison_complete", {"path": str(destination), "result": result})
    return result


def write_numerical_comparison(base: Path, candidate: Path, destination: Path, events: TextIO,
                               allowed_differences: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Add M09 numerical correctness while preserving the stricter agent scores."""
    result = compare_domain(base, candidate, allowed_differences)
    with destination.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    # Full individual responses stay in the artifact, keeping the stage journal compact.
    record(events, "numerical_comparison_complete", {"path": str(destination),
        "scorer_sha256": result["scorer_sha256"], "n_tasks": result["n_tasks"],
        "base_pass_at_1": result["base_pass_at_1"], "student_pass_at_1": result["student_pass_at_1"],
        "delta_percentage_points": result["delta_percentage_points"], "domain_threshold_met": result["domain_threshold_met"]})
    return result


def run(args: argparse.Namespace, events: TextIO) -> None:
    """Advance only after authoritative completion of each prior stage."""
    import yaml

    record(events, "await_download", {"container": args.download_container})
    while True:
        download = container_info(args.download_container)
        if not download["state"]["Running"]:
            if download["state"]["ExitCode"]:
                raise RuntimeError("Pinned Granite download failed; inspect its container logs")
            break
        time.sleep(POLL_INTERVAL_S)
    config = yaml.safe_load(GRANITE_CONFIG.read_text())
    hashes = verify_base(config["model"])
    record(events, "download_verified", {"container": download, "sha256": hashes})
    training = (["podman", "run", "--name", f"wattson-{args.root.name}-granite-training", "--device", "nvidia.com/gpu=all",
                 "--network", "none", "--security-opt", "label=disable", "-v", f"{REPO}:/workspace",
                 "-e", "HF_HUB_OFFLINE=1", "-e", "TRANSFORMERS_OFFLINE=1", "-e", "PYTHONUNBUFFERED=1",
                 TRAINING_IMAGE, "python", "-m", "wattson.training.granite.train", "--config", str(GRANITE_CONFIG)],
                args.root / "granite-training.log")

    granite_student = Path(config["output"]["dir"]) / "merged-bf16"
    if args.qwen_from:
        # Stage 1 already completed under the same budgets (agent-domain-r4);
        # verified_run refuses an incomplete or altered run.
        qwen_base, qwen_student = args.qwen_from / "base", args.qwen_from / "student"
        for directory in (qwen_base, qwen_student):
            verify_evaluation(directory, events)
        if not (granite_student / "config.json").is_file():
            raise RuntimeError(f"Granite student {granite_student} is missing; run stage 1 with training")
        record(events, "reused_stage", {"qwen_from": str(args.qwen_from), "granite_student": str(granite_student)})
    else:
        # Stage 1: Qwen base and student side by side, Granite training alongside.
        qwen_base, qwen_student = args.root / "base", args.root / "student"
        evaluate_pair([("qwen-base", HF_HUB, QWEN_BASE_SNAPSHOT, qwen_base, GPU_MEMORY_FRACTION),
                       ("qwen-student", QWEN_STUDENT, "", qwen_student, GPU_MEMORY_FRACTION)],
                      args.root, events, extra=[training], extra_floor=("granite-training", GRANITE_WEIGHT_BYTES))
    qwen = write_comparison(qwen_base, qwen_student, args.root / "qwen-comparison.json", events)
    qwen_numerical = write_numerical_comparison(qwen_base, qwen_student,
                                               args.root / "qwen-numerical-comparison.json", events)

    # Stage 2: Granite base, then Granite student, one server at a time. Side by
    # side in r4 the two 8B servers shared the GB10's memory bandwidth at
    # ~32-36 tok/s each (~4 tok/s per stream), so a full 8,192-token response
    # needed ~2,000 s, past INFERENCE_TIMEOUT_S. One server gets the whole
    # bandwidth at the same CONCURRENCY and the same total wall time.
    evaluate_pair([("granite-base", Path(config["model"]["name"]), "", args.root / "granite-base",
                    GRANITE_GPU_MEMORY_FRACTION)], args.root, events)
    evaluate_pair([("granite-student", granite_student, "", args.root / "granite-student",
                    GRANITE_GPU_MEMORY_FRACTION)], args.root, events)
    granite = write_comparison(args.root / "granite-base", args.root / "granite-student",
                               args.root / "granite-comparison.json", events)
    write_comparison(qwen_base, args.root / "granite-student", args.root / "granite-vs-qwen-base.json", events)
    write_comparison(qwen_student, args.root / "granite-student", args.root / "granite-vs-qwen-student.json", events)
    granite_numerical = write_numerical_comparison(args.root / "granite-base", args.root / "granite-student",
                                                  args.root / "granite-numerical-comparison.json", events)
    write_numerical_comparison(qwen_base, args.root / "granite-student",
                               args.root / "granite-vs-qwen-base-numerical.json", events)
    write_numerical_comparison(qwen_student, args.root / "granite-student",
                               args.root / "granite-vs-qwen-student-numerical.json", events)
    record(events, "pipeline_complete", {"qwen_domain_threshold_met": qwen["domain_threshold_met"],
        "granite_domain_threshold_met": granite["domain_threshold_met"],
        "qwen_solver_verified_threshold_met": qwen_numerical["domain_threshold_met"],
        "granite_solver_verified_threshold_met": granite_numerical["domain_threshold_met"]})


def main() -> None:
    """Create a fresh run root and durable journal; refuse automatic re-execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New run directory; must not exist")
    parser.add_argument("--download-container", required=True)
    parser.add_argument("--qwen-from", type=Path,
                        help="Completed earlier root whose Qwen runs and Granite training are reused")
    args = parser.parse_args()
    args.root.mkdir()
    # A supervisor's stop arrives as SIGTERM; unwind so the journal records it.
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    with (args.root / "pipeline.jsonl").open("x") as events:
        record(events, "driver_start", {"pid": os.getpid(), "unix_time": time.time(),
            "source_sha256": {str(p.relative_to(REPO)): digest(p) for parent in (REPO / "src/wattson/inference", REPO / "src/wattson/training/granite")
                              for p in parent.glob("*.py")}, "driver_sha256": digest(Path(__file__))})
        try:
            run(args, events)
        except Exception as error:
            record(events, "driver_error", {"type": type(error).__name__, "message": str(error)})
            raise
        except SystemExit as stop:
            record(events, "driver_stopped", {"exit_code": stop.code, "unix_time": time.time()})
            raise


if __name__ == "__main__":
    main()
