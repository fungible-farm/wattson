#!/usr/bin/env bash
#
# smoke.sh -- the WattsOn v0.1.0 smoke check: does the toolchain reproduce on this machine? CPU only, no model, no GPU.
# Stops at the first failing stage. See README.md for what each stage proves.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

stage() { printf '\n=== %s ===\n' "$1"; }

stage "1/5 environment (uv sync, locked)"
uv sync --all-groups --frozen
uv run python - <<'PY'
import platform, sys
import numpy, pandapower, scipy
print("python", sys.version.split()[0], "|", platform.machine(), "|", platform.platform())
print("numpy", numpy.__version__, "| scipy", scipy.__version__, "| pandapower", pandapower.__version__)
PY

stage "2/5 data (CSIRO case files, pinned commit, sha256-verified)"
uv run python scripts/fetch_csiro_nem_data.py

stage "3/5 tests"
uv run python -m pytest -q -x

stage "4/5 oracle trajectories (real programs, real tool server, real grader)"
uv run python -m wattson.smoke oracle

stage "5/5 re-score shipped journals (must equal fixtures/expected.json)"
uv run python -m wattson.smoke rescore

printf '\nSMOKE OK\n'
