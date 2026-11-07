#!/usr/bin/env python3
"""Run a command while holding the exclusive GPU lease (#29: "the lease refuses a second exclusive engine").

    python3 scripts/gpu_lease_run.py PURPOSE [--timeout SECONDS] [--lock-dir DIR] -- COMMAND [ARGS...]

Acquires the single `gpu.lock` (src/wattson/inference/gpu_lease.py), runs COMMAND in the foreground with the lease held, and
releases it however COMMAND ends. If another engine holds the lease for longer than --timeout (default 0: do not wait), nothing is
started: the holder is named on stderr and the exit status is 75 (EX_TEMPFAIL). Otherwise the exit status is COMMAND's.

The lease is a flock, so it lives only as long as this process: it can guard an engine that runs in the FOREGROUND (a vLLM or
llama-server process, `podman run` without -d, `podman kube play --wait`), not one that is started detached and returns at once
(`just up` today). That is why `just up` is not wrapped: a detached pod would drop the lease the moment this process exited.
"""
import argparse
import importlib.util
import os
import signal
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX_TEMPFAIL = 75


def load_lease():
    spec = importlib.util.spec_from_file_location("gpu_lease", os.path.join(REPO, "src", "wattson", "inference", "gpu_lease.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GPULease


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("purpose", help="what the engine is for, recorded in the lock file (e.g. llm-chat)")
    parser.add_argument("--timeout", type=float, default=0.0, help="seconds to wait for the lease (default: do not wait)")
    parser.add_argument("--lock-dir", default=os.environ.get("WATTSON_GPU_LOCK_DIR", "/tmp/wattson-gpu"))
    argv = sys.argv[1:] if argv is None else argv
    if "--" in argv:  # options, then `--`, then the command (argparse's REMAINDER would swallow the options)
        split = argv.index("--")
        args, command = parser.parse_args(argv[:split]), argv[split + 1:]
    else:  # PURPOSE COMMAND...: the recipe form, no options
        args, command = parser.parse_known_args(argv)
    if not command:
        parser.error("give a command after --")
    lease = load_lease()(args.lock_dir)
    if not lease.acquire(args.purpose, timeout=args.timeout):
        print(f"gpu-run: refused: the GPU lease is held by [{lease.holder() or 'unknown'}] (purpose:start:pid); nothing was started",
              file=sys.stderr)
        return EX_TEMPFAIL
    print(f"gpu-run: lease acquired for '{args.purpose}'", file=sys.stderr)
    child = subprocess.Popen(command)
    for sig in (signal.SIGTERM, signal.SIGINT):  # a stop request reaches the engine (podman kube play --wait cleans the pod up on SIGTERM)
        signal.signal(sig, lambda number, _frame: child.send_signal(number))
    try:
        return child.wait()
    finally:
        lease.release()
        print(f"gpu-run: lease released for '{args.purpose}'", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
