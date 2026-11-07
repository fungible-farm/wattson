#!/usr/bin/env bash
# The #48 acceptance: `just up` starts the pod with weights from a SIGNED, DIGEST-PINNED ModelKit, and `just doctor` passes.
#
# Steps (a throwaway registry on 127.0.0.1:5001, nothing leaves the machine except the Hugging Face download):
#   1. build the wattson-kit image; start registry:2 (pinned by digest);
#   2. `kit import` the released Qwen student at the commit the Hub reports, `kit push` it, record the digest;
#   3. generate a fresh cosign key pair (empty password, scratch dir) and sign that digest;
#   4. `just up qwen` with WATTSON_MODEL_KIT=<registry>@<digest>, the public key, the local kit image, the pod allowed to
#      reach the host loopback registry and a small GPU fraction;
#   5. wait for vLLM, read /models/<dir>/.model-digest from the server container, run `just doctor`;
#   6. `just down` and remove the registry.
# Refuses to run if a `wattson-models` volume already exists (the kit would unpack over your real model files); set
# WATTSON_KIT_CHECK_FORCE=1 to run anyway. Needs ~10 GB download (first run), the GPU, podman, just; `just kit-install` and
# `just cosign-install` are run for you. Exit status: 0 only if vLLM answered, the digest file equals the pinned digest and doctor passed.
set -uo pipefail
cd "$(dirname "$0")/.."
fail() { echo "FAIL kit-pod: $*" >&2; exit 1; }
if podman volume exists wattson-models && [ "${WATTSON_KIT_CHECK_FORCE:-0}" != "1" ]; then
  fail "a wattson-models volume already exists; refusing to unpack a kit over it (WATTSON_KIT_CHECK_FORCE=1 to override)"
fi
SCRATCH="$(mktemp -d /tmp/kit-pod-XXXXXX)"; mkdir -p "$SCRATCH/home" "$SCRATCH/keys"
cleanup() { just down >/dev/null 2>&1; podman rm -f kit-pod-registry >/dev/null 2>&1; }
trap cleanup EXIT
just kit-install >/dev/null && just cosign-install >/dev/null || fail "could not install kit/cosign"
KIT="$PWD/.tools/kit/kit"; COSIGN="$PWD/.tools/cosign/cosign"
just kit-image-build >/dev/null 2>&1 || fail "kit image build failed"
podman rm -f kit-pod-registry >/dev/null 2>&1
podman run -d --name kit-pod-registry -p 127.0.0.1:5001:5000 docker.io/library/registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373 >/dev/null || fail "registry did not start"
sleep 3
REPO=PromptExecutionAU/wattson-qwen3.5-4b-pyt; DIR=qwen3.5-4b-pyt
COMMIT="$(curl -s "https://huggingface.co/api/models/$REPO" | python3 -c 'import sys,json;print(json.load(sys.stdin)["sha"])')" || fail "could not read the Hub commit"
echo "student: $REPO @ $COMMIT"
HOME="$SCRATCH/home" "$KIT" import "$REPO" --ref "$COMMIT" --tool hf -t localhost:5001/wattson/qwen:v1 --progress none </dev/null >/dev/null 2>&1 || fail "kit import failed"
DIGEST="$(HOME="$SCRATCH/home" "$KIT" push --plain-http localhost:5001/wattson/qwen:v1 2>&1 | grep -o 'sha256:[0-9a-f]\{64\}' | tail -1)"
[ -n "$DIGEST" ] || fail "kit push gave no digest"
echo "kit digest: $DIGEST"
(cd "$SCRATCH/keys" && HOME="$SCRATCH/home" COSIGN_PASSWORD= "$COSIGN" generate-key-pair >/dev/null 2>&1) || fail "key generation failed"
HOME="$SCRATCH/home" COSIGN_PASSWORD= "$COSIGN" sign --key "$SCRATCH/keys/cosign.key" --allow-insecure-registry --use-signing-config=false --tlog-upload=false --yes "localhost:5001/wattson/qwen@$DIGEST" >/dev/null 2>&1 || fail "signing failed"
chmod 644 "$SCRATCH/keys/cosign.pub"
WATTSON_MODEL_KIT="169.254.1.2:5001/wattson/qwen@$DIGEST" WATTSON_COSIGN_PUBKEY="$SCRATCH/keys/cosign.pub" WATTSON_KIT_INSECURE=1 \
  WATTSON_KIT_IMAGE=localhost/wattson-kit:local WATTSON_POD_HOST_LOOPBACK=1 WATTSON_PORT=18000 WATTSON_GPU_UTIL="${WATTSON_GPU_UTIL:-0.2}" \
  just up qwen >/dev/null 2>&1 || fail "just up failed"
ready=000
for _ in $(seq 1 120); do ready="$(curl -s -o /dev/null -w '%{http_code}' -m3 http://127.0.0.1:18000/v1/models)"; [ "$ready" = 200 ] && break; sleep 10; done
echo "vllm: http=$ready"
[ "$ready" = 200 ] || fail "the model server did not come up"
FILE_DIGEST="$(podman exec wattson-vllm cat "/models/$DIR/.model-digest" 2>/dev/null | tr -d '\r\n')"
echo "model-digest file: $FILE_DIGEST"
[ "$FILE_DIGEST" = "$DIGEST" ] || fail "the digest written by the init container does not equal the pinned digest"
just doctor > "$SCRATCH/doctor.txt" 2>&1; DOCTOR=$?
grep -E '^(PASS|WARN|FAIL|SKIP)' "$SCRATCH/doctor.txt" | sed 's/^/doctor: /'
[ "$DOCTOR" = 0 ] || fail "just doctor exited $DOCTOR"
echo "PASS kit-pod: the pod served the Qwen student from a signed kit pinned by digest; the init container recorded the digest; doctor passed"
