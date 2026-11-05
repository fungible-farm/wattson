#!/usr/bin/env bash
# Train the published Qwen/Granite recipe sequentially in one supervised unit.
# --dry-run prints the exact launch without starting a unit or touching a GPU.
# Images, mounted HF snapshots and output-directory guards remain explicit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
if [[ "${1:-}" == --dry-run ]]; then DRY_RUN=1; shift; fi
QWEN_CONFIG="${1:-configs/agent/config.qwen35-4b.v11pyt.yaml}"
GRANITE_CONFIG="${2:-configs/agent/config.granite42-8b.g11py.yaml}"
UNIT="${WATTSON_TRAIN_UNIT:-wattson-agent-sft}"
IMAGE="${WATTSON_TRAIN_IMAGE:-localhost/wattson-train:local}"
HUB="${WATTSON_HF_HUB:-$HOME/.cache/huggingface/hub}"
# Shared-node default: bound CPU allocations; operators must measure feasibility
# for their hardware. GPU memory is separate from this host-memory cgroup cap.
MEMORY="${WATTSON_TRAIN_MEMORY:-16g}"
CPUS="${WATTSON_TRAIN_CPUS:-4}"
PIDS=512
STOP_SECONDS=60

[[ "$UNIT" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo 'Invalid training unit name' >&2; exit 2; }
cd "$REPO_ROOT"
for CONFIG in "$QWEN_CONFIG" "$GRANITE_CONFIG"; do
    [[ -f "$CONFIG" ]] || { echo "Missing reviewed configuration: $CONFIG" >&2; exit 2; }
done

run_one() {
    # Quote every argument before embedding the two launches in the unit's shell.
    local kind="$1" config="$2"
    local args=(podman run --rm --name "$UNIT-$kind"
        --label "wattson.training.unit=$UNIT" --device nvidia.com/gpu=all
        --network none --memory "$MEMORY" --memory-swap "$MEMORY" --cpus "$CPUS" --pids-limit "$PIDS"
        --security-opt label=disable --volume "$REPO_ROOT:/workspace" --workdir /workspace
        --volume "$HUB:/hub:ro" --env PYTHONPATH=/workspace/src
        --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 --env PYTHONUNBUFFERED=1
        "$IMAGE" python -m wattson.training.agent_sft --config "$config")
    printf '%q ' "${args[@]}"
}

STOP_CONTAINERS="/bin/sh -c 'podman ps -q --filter label=wattson.training.unit=$UNIT | xargs -r podman stop -t 30'"
PLAN="set -euo pipefail; $(run_one qwen "$QWEN_CONFIG"); $(run_one granite "$GRANITE_CONFIG")"
LAUNCH=(systemd-run --user --unit="$UNIT" --remain-after-exit
    --description='Wattson tool-call training: Qwen then Granite'
    --working-directory="$REPO_ROOT" --property="TimeoutStopSec=$STOP_SECONDS"
    --property="ExecStop=$STOP_CONTAINERS" --property="ExecStopPost=$STOP_CONTAINERS"
    /bin/bash -c "$PLAN")
if [[ "$DRY_RUN" == 1 ]]; then printf '%q ' "${LAUNCH[@]}"; printf '\n'; exit 0; fi
podman image exists "$IMAGE" || { echo "Build the approved training image first: $IMAGE" >&2; exit 2; }
[[ -d "$HUB" ]] || { echo "Set WATTSON_HF_HUB to the approved snapshot cache: $HUB" >&2; exit 2; }
"${LAUNCH[@]}"
printf 'Started %s; inspect with: journalctl --user -u %s -f\n' "$UNIT" "$UNIT"
