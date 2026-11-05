#!/usr/bin/env python3
"""PowerCoder environment diagnostic: checks all prerequisites for the NEM PowerCoder mission.

Exit codes:
  0 - all checks passed
  1 - one or more checks failed
"""

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        sys.exit(1)


def main():
    print("=== NEM PowerCoder Environment Diagnostic ===")
    print()

    # OS and architecture
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Architecture: {platform.machine()}")
    print()

    # Python
    py = shutil.which("python3")
    check("python3", py is not None, py or "not found")
    result = run([py, "--version"])
    print(f"  Version: {result.stdout.strip()}")

    # uv
    uv = shutil.which("uv")
    check("uv", uv is not None, uv or "not found")
    result = run([uv, "--version"])
    print(f"  Version: {result.stdout.strip()}")

    # Podman
    podman = shutil.which("podman")
    check("podman", podman is not None, podman or "not found")
    result = run([podman, "--version"])
    print(f"  Version: {result.stdout.strip()}")

    # NVIDIA GPU
    nvidia_smi = shutil.which("nvidia-smi")
    check("nvidia-smi", nvidia_smi is not None, nvidia_smi or "not found")
    result = run([nvidia_smi, "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader"])
    if result.returncode == 0:
        print(f"  GPU: {result.stdout.strip()}")
    else:
        check("nvidia-smi query", False, result.stderr.strip())

    # Git
    git = shutil.which("git")
    check("git", git is not None, git or "not found")

    # Repository state
    repo_dir = Path(__file__).resolve().parent.parent
    result = run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"])
    check("git repo", result.returncode == 0, result.stdout.strip()[:8] if result.returncode == 0 else "not a git repo")

    # Dependencies
    print()
    print("Checking Python dependencies...")
    result = run([uv, "run", "python", "-c", "import pandapower; print(pandapower.__version__)"])
    check("pandapower", result.returncode == 0, result.stdout.strip() if result.returncode == 0 else "import failed")

    result = run([uv, "run", "python", "-c", "import powerio; print(powerio.__version__)"])
    check("powerio", result.returncode == 0, result.stdout.strip() if result.returncode == 0 else "import failed")

    result = run([uv, "run", "python", "-c", "import mcp; print('ok')"])
    check("mcp", result.returncode == 0, result.stdout.strip() if result.returncode == 0 else "import failed")

    # Lab tests (core labs only)
    print()
    print("Running core lab tests (Labs 1-3)...")
    result = run([uv, "run", "python", "-m", "pytest",
                 "labs/01-simple-loadflow-fit/", "labs/02-medium-interconnection-screening/",
                 "labs/03-advanced-provider-bakeoff/", "-q", "--tb=short"])
    check("Labs 1-3 tests", result.returncode == 0, result.stdout.strip()[-200:] if result.returncode != 0 else "all passed")

    print()
    print("=== Environment diagnostic complete ===")


if __name__ == "__main__":
    main()
