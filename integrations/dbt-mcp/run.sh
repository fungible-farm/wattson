#!/usr/bin/env bash
# stdio launcher: stdout belongs exclusively to MCP. Use smoke to verify locally.
set -euo pipefail
integration_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
image="${DBT_MCP_IMAGE:-localhost/wattson-dbt-mcp:2.10.1}"
project="${DBT_MCP_PROJECT:-$integration_dir/sample}"
profiles="${DBT_MCP_PROFILES:-$integration_dir/profiles}"
volume="${DBT_MCP_VOLUME:-wattson-dbt-mcp-output}"
mode="${1:-serve}"
case "$mode" in
  build)
    exec podman build --cpu-period=100000 --cpu-quota=200000 --memory=4g --ulimit nproc=256:256 -t "$image" -f "$integration_dir/Containerfile" "$integration_dir"
    ;;
  serve|smoke) ;;
  *) echo "usage: $0 [build|serve|smoke]" >&2; exit 2 ;;
esac
# Absolute paths prevent Podman treating a relative path as a named volume.
project="$(cd -- "$project" && pwd)"
profiles="$(cd -- "$profiles" && pwd)"
podman volume exists "$volume" || podman volume create "$volume" >/dev/null
args=(run --rm -i --network=none --cpus=2 --memory=2g --pids-limit=256
  --read-only --cap-drop=all --security-opt=no-new-privileges
  --tmpfs /tmp:rw,nosuid,nodev,size=256m
  -v "$project:/project:ro,Z" -v "$profiles:/profiles:ro,Z"
  -v "$volume:/output:rw")
if [[ "$mode" == smoke ]]; then
  exec podman "${args[@]}" --entrypoint python "$image" /opt/dbt/smoke.py
fi
exec podman "${args[@]}" "$image"
