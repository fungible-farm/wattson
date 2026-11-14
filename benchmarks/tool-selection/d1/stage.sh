#!/usr/bin/env bash
# Stage d1-3B at a pinned revision into /c0de/models/d1-3B on sm3lly and verify what was read in review (#151).
set -eu
REV=05b80faf55536582ad5ffb2da7456f6a653acf71
DIR=/c0de/models/d1-3B
mkdir -p "$DIR"
podman run --rm --memory 4g --memory-swap 4g -v "$DIR":/models:Z wattson-d1-eval python -c \
  "from huggingface_hub import snapshot_download as s; print(s(repo_id='LiquidAI/d1-3B', revision='$REV', local_dir='/models'))"
cd "$DIR"
fail=0
chk() { n=$(stat -c %s "$1"); if [ "$n" = "$2" ]; then echo "PASS size $1 $n"; else echo "FAIL size $1 $n want $2"; fail=1; fi; }
chk model.safetensors 6247065504; chk api.py 3478; chk hybrid.py 14416; chk lfm2_vl.py 5602
chk modeling_d1.py 1335; chk prompt.py 12478; chk runner.py 11776
if grep -nE '\bsubprocess\b|\bsocket\b|\burllib\b|\bimport requests\b|requests\.(get|post)|os\.system|(^|[^.a-zA-Z_])eval\(|(^|[^.a-zA-Z_])exec\(|[^.a-zA-Z_]open\(|__import__|\bpickle\b|\bhttp\b' api.py hybrid.py lfm2_vl.py modeling_d1.py prompt.py runner.py; then echo "FAIL suspicious tokens"; fail=1; else echo "PASS no suspicious tokens"; fi
sha256sum *
[ "$fail" = 0 ] && echo "PASS stage" || { echo "FAIL stage"; exit 1; }
