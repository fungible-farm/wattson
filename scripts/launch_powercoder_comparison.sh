#!/usr/bin/env bash
#
# launch_powercoder_comparison.sh -- run src/wattson/training/run_comparison.py as one
# supervised systemd --user unit.
#
# Why: the 2026-10-21 r2 run was a detached exec session plus a driver that
# adopted an externally started evaluator, so nothing owned the whole pipeline
# and stopping it meant hunting PIDs. Here the driver starts every stage itself
# and runs inside a transient unit:
#   - `systemctl --user status <unit>` and `journalctl --user -u <unit>` show it;
#   - `systemctl --user stop <unit>` first stops this run's podman containers
#     (ExecStop), then SIGTERMs the driver and its evaluator children (default
#     KillMode=control-group). The containers' conmon processes sit in the
#     unit's cgroup but ignore SIGTERM -- r3's first launch spent its whole
#     120 s stop timeout on one -- so containers are stopped explicitly. When
#     the driver exits on its own (failure), ExecStop does not run; ExecStopPost
#     stops anything left after the stop timeout;
#   - Restart is never set: the driver refuses automatic re-execution, and a
#     failed unit stays "failed" for inspection (--remain-after-exit keeps a
#     finished one visible too).
#
# Usage:
#   ./scripts/launch_powercoder_comparison.sh out/agent-domain-r3
#   ./scripts/launch_powercoder_comparison.sh out/agent-domain-r5 \
#       --qwen-from out/agent-domain-r4    # Granite stages only
#
# Arguments after the root pass through to src/wattson/training/run_comparison.py.
# The run root must not exist yet; the driver creates it and refuses reuse.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${1:?usage: $0 <new run root, e.g. out/agent-domain-r3>}"
shift
RUN="$(basename "$ROOT")"
[[ "$RUN" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "Invalid run name" >&2; exit 2; }
UNIT="wattson-${RUN}"
# Escape the only regex metacharacter allowed in a validated run name.
ESCAPED_RUN="${RUN//./\\.}"
# The verified, already-exited pinned Granite download (see the handoff).
DOWNLOAD_CONTAINER="wattson-granite42-download-r1"
# A systemd unit's PATH has no ~/.local/bin (AGENTS.md FM-24); resolve uv explicitly.
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

systemd-run --user --unit="$UNIT" --remain-after-exit \
  --description="PowerCoder Qwen/Granite comparison ($RUN)" \
  --working-directory="$REPO_ROOT" \
  --property=TimeoutStopSec=60 \
  --property="ExecStop=$STOP_CONTAINERS" \
  --property="ExecStopPost=$STOP_CONTAINERS" \
  "$UV" run --offline --no-sync python -m wattson.training.run_comparison \
    --root "$ROOT" --download-container "$DOWNLOAD_CONTAINER" "$@"

echo "started $UNIT; inspect with: systemctl --user status $UNIT"
