#!/usr/bin/env bash
#
# launch_agent_study.sh -- run src/wattson/training/run_agent_study.py (r6: smoke gates,
# four-model evaluation, latency and memory) as one supervised systemd --user
# unit, with the same stop handling as launch_powercoder_comparison.sh
# (AGENTS.md FM-11/FM-13).
#
# Run it after scripts/launch_agent_sft.sh has finished: the driver re-hashes
# both students and refuses to start without them.
#
# Usage:
#   ./scripts/launch_agent_study.sh out/agent-study-r6
#   ./scripts/launch_agent_study.sh out/agent-study-r6-strict --strict-arm
#       # strict-tool-calling 2x2 on the smoke suite (backlog 0011)
#
# Arguments after the root pass through to src/wattson/training/run_agent_study.py.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${1:?usage: $0 <new run root, e.g. out/agent-study-r6>}"
shift
RUN="$(basename "$ROOT")"
[[ "$RUN" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "Invalid run name" >&2; exit 2; }
UNIT="wattson-${RUN}"
# Escape the only regex metacharacter allowed in a validated run name.
ESCAPED_RUN="${RUN//./\\.}"
# The systemd user manager's PATH has no ~/.local/bin, so a waiter unit running
# this script found no uv and set -e exited it silently (2026-10-23 06:40: r6
# never launched). Resolve uv explicitly and say so when it is missing.
UV="$(command -v uv || true)"
UV="${UV:-$HOME/.local/bin/uv}"
if [[ ! -x "$UV" ]]; then
  echo "refusing: uv not found on PATH or at $HOME/.local/bin/uv" >&2
  exit 1
fi
STOP_CONTAINERS="/bin/sh -c 'podman ps -q --filter \"name=^wattson-${ESCAPED_RUN}-\" | xargs -r podman stop -t 30'"

if [[ -e "$REPO_ROOT/$ROOT" ]]; then
  echo "refusing: $ROOT already exists" >&2
  exit 1
fi
# --remain-after-exit keeps a finished training unit "active (exited)", so test
# its sub-state and result rather than is-active.
for sft in $(systemctl --user list-units --all --plain --no-legend 'wattson-agent-sft*' | awk '{print $1}'); do
  if [[ "$(systemctl --user show -p SubState --value "$sft")" == "running" ]]; then
    echo "refusing: $sft is still training" >&2
    exit 1
  fi
done

systemd-run --user --unit="$UNIT" --remain-after-exit \
  --description="PowerCoder r6 agent study ($RUN)" \
  --working-directory="$REPO_ROOT" \
  --property=TimeoutStopSec=60 \
  --property="ExecStop=$STOP_CONTAINERS" \
  --property="ExecStopPost=$STOP_CONTAINERS" \
  "$UV" run --offline --no-sync python -m wattson.training.run_agent_study --root "$ROOT" "$@"

echo "started $UNIT; inspect with: systemctl --user status $UNIT"
