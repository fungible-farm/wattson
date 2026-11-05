#!/bin/sh
# CWD-independent host adapter; stdout from inspection is the actual reader result.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IMAGE=${PLEXOSDB_IMAGE:-localhost/wattson-plexosdb:1.7.0}
MODE=${1:-help}
if [ "$#" -gt 0 ]; then shift; fi
case "$MODE" in
    build)
        exec podman build --memory "${PLEXOSDB_MEMORY:-1g}" --cpu-period=100000 \
            --cpu-quota=100000 --ulimit nproc=128:128 -f "$HERE/Containerfile" -t "$IMAGE" "$HERE" ;;
    check)
        set -- --entrypoint /app/.venv/bin/python "$IMAGE" /app/proof.py
        input="$HERE/fixtures" ;;
    inspect)
        input=$(CDPATH= cd -- "${1:?Supply input directory}" && pwd)
        file=${2:-model.xml}
        set -- "$IMAGE" "/input/$file" ;;
    *) echo 'PLEXOS is a file CLI, not an MCP server: build | check | inspect DIR [FILE]' >&2; exit 2 ;;
esac
exec podman run --rm --pull=never --memory "${PLEXOSDB_MEMORY:-1g}" \
    --memory-swap "${PLEXOSDB_MEMORY:-1g}" --cpus "${PLEXOSDB_CPUS:-1}" --pids-limit=128 \
    --network=none --read-only --cap-drop=all --security-opt=no-new-privileges \
    --tmpfs /tmp:rw,size=256m -v "$input:/input:ro" "$@"
