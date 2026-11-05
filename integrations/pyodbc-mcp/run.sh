#!/usr/bin/env bash
# Usage: ./run.sh /absolute/path/to/private-config readonly_sql
# Agent MCP stdio command: /absolute/path/to/this/run.sh (args as above).
set -euo pipefail
config_dir="$(realpath "${1:?Supply directory containing private odbc.ini}")"
dsn="${2:?Supply DSN name}"
[[ -f "$config_dir/odbc.ini" ]] || { echo 'Missing odbc.ini' >&2; exit 1; }
: "${WATTSON_DATABASE_READONLY_CONFIRMED:?Set yes only after verifying database-side read-only permissions}"
# Explicit bind directory; no home directory, database export, or credentials in image.
exec podman run --rm -i --read-only --cap-drop=all --security-opt=no-new-privileges \
  --memory=512m --cpus=1 --pids-limit=128 --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  -v "$config_dir:/config:ro,Z" -v "$config_dir/odbc.ini:/etc/odbc.ini:ro,Z" \
  -e WATTSON_ODBC_DSN="$dsn" -e WATTSON_DATABASE_READONLY_CONFIRMED \
  localhost/wattson-pyodbc-mcp:0.4.0
