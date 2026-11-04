#!/usr/bin/env bash
# Offline database proof after image build; SQLite is only a disposable test fixture.
set -euo pipefail
cd "$(dirname "$0")"
fixture_dir="$(mktemp -d)"
trap 'rm -rf "$fixture_dir"' EXIT
chmod 755 "$fixture_dir"
# Initialize real SQLite bytes using the image's standard library; no host Python deps.
podman run --rm --network=none --read-only --memory=256m --cpus=1 --pids-limit=64 \
  --userns=keep-id --user "$(id -u):$(id -g)" -v "$fixture_dir:/fixture:rw,Z" \
  --entrypoint python localhost/wattson-pyodbc-mcp:0.4.0 -c \
  "import sqlite3; c=sqlite3.connect('/fixture/fixture.db'); c.execute('CREATE TABLE measurements(name TEXT, mw REAL)'); c.execute(\"INSERT INTO measurements VALUES ('fixture-a',42.5)\"); c.commit(); c.close()"
cat > "$fixture_dir/odbc.ini" <<'INI'
[fixture]
Driver=SQLite3
Database=/config/fixture.db
ReadOnly=Yes
INI
chmod 444 "$fixture_dir/fixture.db" "$fixture_dir/odbc.ini"
podman run --rm --network=none --read-only --cap-drop=all --security-opt=no-new-privileges \
  --memory=512m --cpus=1 --pids-limit=128 --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  -v "$fixture_dir:/config:ro,Z" -v "$fixture_dir/odbc.ini:/etc/odbc.ini:ro,Z" -e WATTSON_ODBC_DSN=fixture \
  -e WATTSON_DATABASE_READONLY_CONFIRMED=yes --entrypoint python \
  localhost/wattson-pyodbc-mcp:0.4.0 /app/proof.py
