#!/bin/sh
# End-to-end gate using only the packaged images/model; no host Python/Node/Rust.
set -eu
cd "$(dirname "$0")/.."
AGENT=localhost/wattson-pi:example
WATTSON_OFFLINE=1 sh nemo-agent/environment.sh install
sh nemo-agent/environment.sh query PV1Axis 2030 CP
sh nemo-agent/environment.sh offline-mcp-build
podman run --rm --pull=never --network=none --read-only --tmpfs /tmp \
    --entrypoint python "$AGENT" -m unittest -v test_nemo
sh nemo-agent/environment.sh cpu-up
mkdir -p .wattson/comparison
podman run --rm --pod wattson-nemo-demo --pull=never --read-only --tmpfs /tmp --user 0 \
    --volume "$PWD/.wattson/comparison:/results:Z" --entrypoint python "$AGENT" \
    /opt/wattson-nemo/cpu_probe.py /results
sh nemo-agent/environment.sh benchmark candle
podman inspect wattson-candle-cpu > .wattson/comparison/candle-container.json
podman logs wattson-candle-cpu > .wattson/comparison/candle-server.log 2>&1
podman run --rm --pull=never --network=none --read-only --user 0 \
    --volume "$PWD/.wattson/comparison:/results:ro,Z" --entrypoint python "$AGENT" -c \
    'import json,pathlib
rows=json.loads(pathlib.Path("/results/candle/scorecard.json").read_text())["tasks"]
assert all(r["process_exit_code"] == 0 for r in rows), "agent/controller failure"
assert any(r["observed_expected_value"] for r in rows), "no actual MCP result reached the local agent"
print("Observed task passes:",sum(r["passed"] for r in rows),"/",len(rows))'
echo 'PASS: packaged local model + Pi + upstream just-mcp + NEMO, without external network'
