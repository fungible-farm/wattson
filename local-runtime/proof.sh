#!/bin/sh
# Committed end-to-end proof: no fabricated output or hand-edited recording.
# Requires the two built/loaded images plus the explicit infra image.
set -eu
cd "$(dirname "$0")"
# Use "fresh" after rebuilding to discard this tutorial's old image instances.
if [ "${1:-}" = fresh ]; then ./wattson down; fi
./wattson install-ipc
./wattson hello
./wattson check
if ./wattson fail; then
    echo 'FAIL: deliberate failure returned success' >&2
    exit 1
fi
./wattson inspect
./wattson record
# Validate results and cast from inside the developer image, not host Python.
podman run --rm -i --pull=never --network=none --read-only --user 0 \
    --cap-drop=all --security-opt=no-new-privileges \
    --volume "$PWD/.wattson:/evidence:ro,Z" --entrypoint python \
    "${WATTSON_DEV_IMAGE:-localhost/wattson-developer:example}" - <<'PY'
import json
from pathlib import Path

evidence = Path('/evidence')
results = [json.loads(p.read_text()) for p in evidence.glob('runs/*/result.json')]
assert any(row['recipe'] == 'check' and row['exit_code'] == 0 for row in results)
assert any(row['recipe'] == 'fail' and row['exit_code'] != 0 for row in results)
assert any(row['recipe'] == 'record' and row['exit_code'] == 0 for row in results)
cast = [json.loads(line) for line in (evidence / 'recordings/wattson.cast').read_text().splitlines()]
assert cast[0]['version'] == 2
output = ''.join(event[2] for event in cast[1:] if event[1] == 'o')
assert 'PASS: socket + FIFO round trips' in output
assert 'Observed recipe exit status:' in output
print('PASS: invocation evidence + real asciinema recording')
PY
./wattson stop
./wattson logs
podman run --rm -i --pull=never --network=none --read-only --user 0 \
    --cap-drop=all --security-opt=no-new-privileges \
    --volume "$PWD/.wattson:/evidence:ro,Z" --entrypoint python \
    "${WATTSON_IMAGE:-localhost/wattson-command:example}" - <<'PY'
import json
from pathlib import Path

state = json.loads(Path('/evidence/service-stopped.json').read_text())[0]['State']
assert state['Running'] is False
assert state['FinishedAt']
print('PASS: externally observed service termination')
PY
./wattson install-ipc
./wattson export
podman load --input .wattson/wattson-images.tar
echo 'PASS: Wattson local runtime proof (pod retained; ./wattson down removes it)'
