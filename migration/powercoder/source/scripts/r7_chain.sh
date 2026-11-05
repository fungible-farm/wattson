#!/usr/bin/env bash
#
# r7_chain.sh -- after the r6b queue, retrain both students on IR 0.2.0 trajectories and run r7.
#
# Steps, each refusing (exit 1, reason on stdout) rather than improvising:
#   1. wait until no r6b unit or waiter is running and no powercoder-* container exists, so the
#      r6b baselines (strict, prompted, Lab 10) finish on the code they started with;
#   2. fast-forward the main tree to $BRANCH (tracked files must be clean; no merge commits);
#   3. uv sync --frozen, build the IR's lift binary, and run the CPU tests the change touches
#      (never the full tests/ suite: it loads a llama-swap model, see the OOM memory note);
#   4. train both r7 students (scripts/launch_agent_sft.sh r7) and wait for them;
#   5. launch the r7 study: four models on the smoke gate, the 100-task benchmark, the judgement
#      suite (incl. held-out snem-1803) and the unsolvable suite, then error ledgers. Every suite and
#      the training split are in fit-prompt wording v2 (powercoder/datasets/reword.py).
#
# Usage (as a supervised unit, so it survives the session):
#   systemd-run --user --unit=powercoder-r7-chain --working-directory="$MAIN" \
#     /bin/bash "$MAIN/../nem-poweragent-lab-bus/scripts/r7_chain.sh"

set -euo pipefail

MAIN="${MAIN:-/home/brianh/promptexecution/fungible/nem-poweragent-lab}"
BRANCH="${BRANCH:-chore/bus-discovery}"
OUT="training/unsloth/out"
# The systemd user manager's PATH has neither (FM-24).
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
CARGO="$(command -v cargo || echo "$HOME/.cargo/bin/cargo")"
POLL_S=300
R6B_UNITS=(powercoder-agent-study-r6b-strict powercoder-agent-study-r6b-prompted-wait powercoder-agent-study-r6b-prompted
           powercoder-agent-study-r6b-restorebench-wait powercoder-agent-study-r6b-restorebench)
TESTS=(powercoder/datasets/test_reword.py powercoder/inference/test_command_ir.py powercoder/inference/test_studies.py powercoder/inference/test_replay.py
       powercoder/test_commands.py powercoder/inference/test_protocol_suite.py powercoder/inference/test_error_ledger.py
       powercoder/inference/test_gateway.py powercoder/datasets/test_trajectories.py training/test_run_agent_study.py
       tests/integration/test_study_tools.py tests/integration/test_canonical_mcp_transport.py)

log() { echo "$(date -Is) r7-chain: $*"; }
refuse() { log "REFUSED: $*"; exit 1; }
running() { [[ "$(systemctl --user show -p SubState --value "$1" 2>/dev/null)" =~ ^(running|start|start-pre|auto-restart)$ ]]; }

cd "$MAIN"
for tool in "$UV" "$CARGO"; do [[ -x "$tool" ]] || refuse "$tool not found"; done

log "1/5 waiting for the r6b queue (${R6B_UNITS[*]})"
while true; do
  busy=""
  for unit in "${R6B_UNITS[@]}"; do running "$unit" && busy="$unit"; done
  [[ -z "$busy" && -z "$(podman ps -q --filter name=^powercoder-)" ]] && break
  sleep "$POLL_S"
done
log "r6b queue idle"

log "2/5 fast-forwarding $MAIN to $BRANCH"
git diff --quiet && git diff --cached --quiet || refuse "tracked files in $MAIN are modified"
git merge --ff-only "$BRANCH" || refuse "$BRANCH is not a fast-forward of $(git rev-parse --abbrev-ref HEAD)"
log "main tree at $(git rev-parse --short HEAD)"

log "3/5 sync, build lift, CPU test gate"
"$UV" sync --frozen || refuse "uv sync --frozen failed"
(cd rust && "$CARGO" build -q --release -p command-ir --bin lift) || refuse "lift build failed"
"$UV" run --offline --no-sync python -m pytest -q "${TESTS[@]}" || refuse "CPU test gate failed"

log "4/5 training r7 students"
./scripts/launch_agent_sft.sh r7
sleep 30
while running powercoder-agent-sft-r7; do sleep "$POLL_S"; done
[[ "$(systemctl --user show -p Result --value powercoder-agent-sft-r7)" == "success" ]] || refuse "powercoder-agent-sft-r7 did not succeed"
for student in agent-sft-qwen35-4b-20261024-r7 agent-sft-granite42-8b-20261024-r7; do
  [[ -f "$OUT/$student/hash_manifest.json" ]] || refuse "$student has no hash_manifest.json"
done

log "5/5 launching the r7 study"
exec ./scripts/launch_agent_study.sh "$OUT/agent-study-r7" \
  --student "qwen-student=$OUT/agent-sft-qwen35-4b-20261024-r7" \
  --student "granite-student=$OUT/agent-sft-granite42-8b-20261024-r7" \
  --suite "judgement=$OUT/judgement-20261024-v2/examples.jsonl" \
  --suite "unsolvable=$OUT/unsolvable-20261024-v2/examples.jsonl"
