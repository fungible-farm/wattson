#!/usr/bin/env bash
# Run d1_select.py for one split on sm3lly: offline, read-only root, capped memory, CPU only (#151).
set -eu
SPLIT=${1:?split dev|heldout}
podman run --rm --network none --read-only --tmpfs /tmp:rw,size=512m --memory 16g --memory-swap 16g \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_HOME=/tmp/hf -e HF_MODULES_CACHE=/tmp/hfmod \
  -v /c0de/models/d1-3B:/models/d1-3B:ro,Z -v /c0de/d1-eval/ts:/work/benchmarks/tool-selection:ro,Z \
  -v /c0de/d1-eval/out:/out:rw,Z wattson-d1-eval \
  python /work/benchmarks/tool-selection/d1/d1_select.py --split "$SPLIT" --out /out/d1-$SPLIT.json
