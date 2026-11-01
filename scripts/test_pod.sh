#!/usr/bin/env bash
# Test the quick-start pod without a GPU or a model download: render kube/wattson.yaml, drop the parts that need them (see
# scripts/pod_filter.py), play what is left under a separate pod name, and check the harness from outside the way a user would.
#
#   scripts/test_pod.sh [harness-image]      default localhost/wattson-harness:local
#
# Each step prints PASS/FAIL with the evidence. The script exits non-zero if any step fails and always removes its pod and volume.
set -uo pipefail

IMAGE="${1:-localhost/wattson-harness:local}"
POD="wattson-test"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
FAILED=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILED=1; }
check() {
  local name="$1"; shift
  if "$@" >"$WORK/step.out" 2>&1; then pass "$name"; else fail "$name"; sed 's/^/        /' "$WORK/step.out" | tail -15; fi
}
cleanup() { podman pod rm --force "$POD" >/dev/null 2>&1 || true; podman volume rm --force "${POD}-cases" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

mkdir -p "$WORK/out"

# 1. every @PLACEHOLDER@ in the template is filled by the justfile (otherwise `just up` would play a broken pod)
for placeholder in $(grep -o '@[A-Z_]*@' "$HERE/kube/wattson.yaml" | sort -u); do
  if grep -q -- "s|$placeholder|" "$HERE/justfile"; then pass "justfile fills $placeholder"; else fail "justfile does not fill $placeholder"; fi
done

# 2. render with dummy values and reduce to the part that needs no GPU
sed -e "s|@IMAGE@|$IMAGE|g" -e "s|@VLLM_IMAGE@|unused|g" -e "s|@MODEL_REPO@|unused/unused|g" -e "s|@MODEL_DIR@|unused|g" \
    -e "s|@REASONING@|qwen3|g" -e "s|@GPU_UTIL@|0.5|g" -e "s|@MAX_LEN@|32768|g" -e "s|@LOG_LEVEL@|DEBUG|g" -e "s|@HOST_PORT@|18000|g" -e "s|@WORK@|$WORK|g" \
    "$HERE/kube/wattson.yaml" > "$WORK/rendered.yaml"
if grep -q '@[A-Z_]*@' "$WORK/rendered.yaml"; then fail "unfilled placeholder in the rendered YAML"; else pass "rendered YAML has no placeholder left"; fi
check "reduce the pod (no GPU, no download)" bash -c "podman run --rm -i --entrypoint python '$IMAGE' /app/scripts/pod_filter.py '$POD' < '$WORK/rendered.yaml' > '$WORK/reduced.yaml' && test -s '$WORK/reduced.yaml'"

# 3. play it
podman volume create "${POD}-cases" >/dev/null
check "podman kube play" podman kube play --replace "$WORK/reduced.yaml"

# 4. the init container fetched the case files, and the harness is running
for _ in $(seq 1 60); do
  [ "$(podman inspect -f '{{.State.Status}}' "${POD}-harness" 2>/dev/null)" = running ] && break
  sleep 2
done
check "harness container is running" test "$(podman inspect -f '{{.State.Status}}' "${POD}-harness" 2>/dev/null)" = running
check "doctor passes inside the harness" bash -c "podman exec ${POD}-harness wattson --log-level ERROR doctor | tee '$WORK/doctor.txt' | grep -q 'FAIL=0'"
check "doctor sees the case files the init container fetched" grep -q 'PASS  cases' "$WORK/doctor.txt"

# 5. the harness has its tools, and they run as the unprivileged user
check "a login shell finds wattson (the just recipes use one)" podman exec "${POD}-harness" bash -lc 'command -v wattson'
check "the harness doctor recipe runs (no model server in this reduced pod, so it must report the endpoint FAIL, not crash)" bash -c "podman exec ${POD}-harness bash -lc 'just --justfile /app/justfile doctor' 2>&1 | grep -q 'model-endpoint'"
check "pi is installed" podman exec "${POD}-harness" bash -lc 'pi --version'
# The students invent commands and numbers when pi offers bash/codemode instead of run_python; assert what pi really declares to the model.
check "pi declares exactly the four WattsOn tools and the trained system prompt" bash -c "podman exec ${POD}-harness /app/.venv/bin/python /app/scripts/pi_capture.py | tee '$WORK/pi.json' | python3 -c 'import json,sys; d=json.load(sys.stdin); want=[\"mcp__wattson__load_network\",\"mcp__wattson__run_power_flow\",\"mcp__wattson__run_python\",\"mcp__wattson__run_study_trial\"]; sys.exit(0 if d.get(\"tools\")==want and d.get(\"contains_trained_prompt\") and d.get(\"extra_chars\",999)<100 else 1)'"
check "node is new enough for pi (>= 22.19)" podman exec "${POD}-harness" bash -lc 'node -e "const [a,b]=process.versions.node.split(\".\").map(Number); process.exit(a>22||(a===22&&b>=19)?0:1)"'
check "just lists the harness recipes" podman exec "${POD}-harness" bash -lc 'just --justfile /app/justfile --list | grep -q ask'
check "runs as a non-root user" bash -c "test \"\$(podman exec ${POD}-harness id -u)\" != 0"

# 6. a lab runs through the pod, and its structured log reaches the mounted work directory
check "lab 1 passes in the pod" podman exec "${POD}-harness" wattson lab 1
check "the log file is written to the mounted /work" test -s "$WORK/out/wattson.log"
check "the log file is JSON lines" bash -c "head -1 '$WORK/out/wattson.log' | python3 -c 'import json,sys; json.loads(sys.stdin.read())'"

# 7. pi can be configured against a server address without a server running
check "pi-setup writes the provider and the MCP server" bash -c "podman exec ${POD}-harness wattson pi-setup --base-url http://127.0.0.1:8000/v1 --model wattson && podman exec ${POD}-harness bash -lc 'pi mcp list' | grep -q wattson"

# 8. tear down cleanly
check "pod removes cleanly" podman pod rm --force "$POD"

echo
if [ "$FAILED" -eq 0 ]; then echo "POD TEST OK"; else echo "POD TEST FAILED"; fi
exit "$FAILED"
