#!/usr/bin/env bash
# Builds this optional integration; leaves WattsOn's baseline images/config untouched.
set -euo pipefail
cd "$(dirname "$0")"
command -v podman >/dev/null || { echo 'Install Podman 5+ first' >&2; exit 1; }
podman build --memory=2g --cpu-period=100000 --cpu-quota=200000 -t localhost/wattson-pyodbc-mcp:0.4.0 .
./proof.sh
