#!/usr/bin/env bash
#
# grpo_wait.sh -- launch G3's GRPO pilot (training/run_grpo_pilot.py) once r7 completes and its
# Qwen student passed the smoke gate. Refuses on a failed or stopped r7, or a failed gate.
#
# Usage (supervised): systemd-run --user --unit=powercoder-grpo-wait --working-directory="$MAIN" \
#   /bin/bash "$MAIN/../nem-poweragent-lab-bus/scripts/grpo_wait.sh"

set -euo pipefail

MAIN="${MAIN:-/home/brianh/promptexecution/fungible/nem-poweragent-lab}"
OUT="training/unsloth/out"
J="$OUT/agent-study-r7/pipeline.jsonl"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
POLL_S=600

cd "$MAIN"
log() { echo "$(date -Is) grpo-wait: $*"; }
log "waiting for $J"
while true; do
  if [[ -f "$J" ]]; then
    kind="$(tail -1 "$J" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["kind"])' 2>/dev/null || true)"
    case "$kind" in
      pipeline_complete) break;;
      driver_error|driver_stopped) log "REFUSED: r7 ended with $kind"; exit 1;;
    esac
  fi
  sleep "$POLL_S"
done
gate="$(grep '"smoke_gate"' "$J" | python3 -c 'import json,sys
rows=[json.loads(l)["payload"] for l in sys.stdin]
print(next((r["passed"] for r in rows if r["model"]=="qwen-student"), False))')"
[[ "$gate" == "True" ]] || { log "REFUSED: the r7 Qwen student did not pass its smoke gate"; exit 1; }
[[ ! -e "$OUT/grpo-pilot-r7" ]] || { log "REFUSED: $OUT/grpo-pilot-r7 exists"; exit 1; }
log "launching the GRPO pilot"
exec systemd-run --user --unit=powercoder-grpo-pilot-r7 --remain-after-exit --working-directory="$MAIN" \
  --description="PowerCoder G3 GRPO pilot from the r7 Qwen student" --property=TimeoutStopSec=60 \
  --property="ExecStop=/bin/sh -c 'podman ps -q --filter name=^powercoder-grpo-pilot-r7- | xargs -r podman stop -t 30'" \
  "$UV" run --offline --no-sync python -m training.run_grpo_pilot --root "$OUT/grpo-pilot-r7" \
  --student "$OUT/agent-sft-qwen35-4b-20261024-r7"
