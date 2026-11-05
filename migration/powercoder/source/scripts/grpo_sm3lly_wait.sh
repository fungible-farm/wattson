#!/usr/bin/env bash
#
# grpo_sm3lly_wait.sh -- run G3's GRPO pilot on sm3lly's RTX 3090 (user approval 2026-10-24) as soon
# as the r7 Qwen student exists, in parallel with Granite's training and the r7 study on GB10.
#
# 1. Wait until training/unsloth/out/agent-sft-qwen35-4b-20261024-r7 has its hash manifest and a
#    generation check whose every entry matches (agent_sft trains Qwen before Granite). Refuse if
#    the r7 chain or the SFT unit fails first.
# 2. Copy the student to sm3lly's checkout; the pilot re-verifies every hash before using it.
# 3. Start the pilot there as a supervised unit (--profile sm3lly). It suspends the llama.cpp
#    heretic unit for its duration and restarts it when it ends or fails.
#
# Usage: systemd-run --user --unit=powercoder-grpo-sm3lly-wait --working-directory="$MAIN" \
#          /bin/bash "$MAIN/../nem-poweragent-lab-bus/scripts/grpo_sm3lly_wait.sh"

set -euo pipefail

MAIN="${MAIN:-/home/brianh/promptexecution/fungible/nem-poweragent-lab}"
REMOTE="${REMOTE:-sm3lly.lan}"
REMOTE_REPO="powercoder-grpo/repo"
STUDENT="training/unsloth/out/agent-sft-qwen35-4b-20261024-r7"
ROOT="training/unsloth/out/grpo-pilot-r7"
POLL_S=600

log() { echo "$(date -Is) grpo-sm3lly-wait: $*"; }
refuse() { log "REFUSED: $*"; exit 1; }
failed() { [[ "$(systemctl --user show -p ActiveState --value "$1" 2>/dev/null)" == "failed" ]]; }
complete() {
  [[ -f "$STUDENT/hash_manifest.json" && -f "$STUDENT/generation_check.json" ]] &&
    python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); sys.exit(0 if c and all(x["matches"] for x in c) else 1)' \
      "$STUDENT/generation_check.json"
}

cd "$MAIN"
log "waiting for $STUDENT"
until complete; do
  failed powercoder-r7-chain && refuse "powercoder-r7-chain failed before the Qwen student existed"
  failed powercoder-agent-sft-r7 && ! complete && refuse "powercoder-agent-sft-r7 failed before the Qwen student completed"
  sleep "$POLL_S"
done
log "student complete; copying to $REMOTE"
ssh -o BatchMode=yes "$REMOTE" "test ! -e $REMOTE_REPO/$ROOT" || refuse "$ROOT already exists on $REMOTE"
rsync -a "$STUDENT" "$REMOTE:$REMOTE_REPO/training/unsloth/out/"
log "launching the pilot on $REMOTE"
ssh -o BatchMode=yes "$REMOTE" "cd $REMOTE_REPO && systemd-run --user --unit=powercoder-grpo-pilot-r7 --remain-after-exit \
  --working-directory=\$HOME/$REMOTE_REPO --description='PowerCoder G3 GRPO pilot (r7 Qwen student, RTX 3090)' \
  --property=TimeoutStopSec=60 \
  --property=\"ExecStop=/bin/sh -c 'podman ps -q --filter name=^powercoder-grpo-pilot-r7- | xargs -r podman stop -t 30'\" \
  \$HOME/.local/bin/uv run --offline --no-sync python -m training.run_grpo_pilot --root $ROOT --student $STUDENT --profile sm3lly"
log "launched; follow with: ssh $REMOTE journalctl --user -u powercoder-grpo-pilot-r7 -f"
