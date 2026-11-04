#!/bin/sh
# Minimal host adapter. All language runtimes, agent tools, model serving and SBOM
# scanning are packaged in images; no engine socket is mounted into the agent.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)
STATE="$ROOT/.wattson"
NEMO_IMAGE=localhost/wattson-nemo:example
AGENT_IMAGE=localhost/wattson-pi:example
CPU_IMAGE=localhost/wattson-candle-cpu:example
BUILDER_IMAGE=localhost/wattson-mcp-builder:example
SCANNER_IMAGE=docker.io/anchore/syft:v1.56.0
POD=wattson-nemo-demo
REMOTE_POD=wattson-nemo-remote-demo
CPU_CONTAINER=wattson-candle-cpu
MODEL=${WATTSON_MODEL:-qwen}
ACTION=${1:-components}
if [ "$#" -gt 0 ]; then shift; fi
mkdir -p "$STATE/comparison"

components() {
    printf '%s\n' \
        'Wattson NEMO/model environment — required components:' \
        '  Host: Linux, rootless Podman 5.4+, working namespaces/cgroup v2, POSIX shell, tar, sha256sum and core utilities.' \
        '  Data/task image: Python + just 1.59.0 + just-mcp 0.2.0 + pinned NEMO source.' \
        '  Agent image: same task layers + Node 24 + Pi 1.3.0.' \
        '  CPU serving image: mistral.rs 0.9.5 (Candle), CPU-only release, runtime system libraries.' \
        '  Model data images: PromptExecutionAU Wattson Qwen3.5-4B and Granite4.2-8B; pinned revisions.' \
        '  HF CLI 2.4.0 and official HF MCP 0.4.32: separate native tooling images.' \
        '  SBOM image: Anchore Syft 1.56.0; runs locally against saved OCI archives.' \
        '  MCP developer image: Rust 1.90 + native build tools + locked, cached MCP dependencies.' \
        '  Existing local tutorial: runtime, developer/recorder, pause image (see parent installer).' \
        '  Network during preparation: image registries, GitHub, npm, Hugging Face.' \
        '  Network during comparison: sm3lly LAN endpoint or pod loopback; no cloud LLM API.' \
        '  No host Rust, Python, Node, Pi, just-mcp, Candle, Syft, or model weights preinstalled.'
}

need_image() {
    podman image exists "$1" || { echo "Missing image $1; run just install or import the package." >&2; exit 1; }
}

ensure_pod() {
    if [ "${1:-local}" = remote ]; then
        if ! podman pod exists "$REMOTE_POD"; then
            podman pod create --name "$REMOTE_POD" --infra-image registry.k8s.io/pause:3.10 --label wattson.nemo.example=true
        fi
    elif ! podman pod exists "$POD"; then
        # The complete CPU agent/model path has loopback but no external network.
        podman pod create --name "$POD" --network=none --infra-image registry.k8s.io/pause:3.10 --label wattson.nemo.example=true
    fi
}

case "$ACTION" in
    components) components ;;
    build)
        components
        podman build --target nemo -t "$NEMO_IMAGE" -f "$HERE/Containerfile" "$HERE"
        podman build --target mcp-build -t "$BUILDER_IMAGE" -f "$HERE/Containerfile" "$HERE"
        podman build --target agent -t "$AGENT_IMAGE" -f "$HERE/Containerfile" "$HERE"
        podman build -t "$CPU_IMAGE" -f "$HERE/Containerfile.candle" "$HERE"
        podman pull "$SCANNER_IMAGE"
        ;;
    install)
        components
        if ! podman image exists "$NEMO_IMAGE" || ! podman image exists "$AGENT_IMAGE" || ! podman image exists "$CPU_IMAGE" || ! podman image exists "$SCANNER_IMAGE" || ! podman image exists "$BUILDER_IMAGE"; then
            [ "${WATTSON_OFFLINE:-0}" != 1 ] || { echo 'Offline install requires importing all listed component images first.' >&2; exit 1; }
            sh "$HERE/environment.sh" build
        fi
        sh "$ROOT/huggingface/environment.sh" install
        podman run --rm --pull=never --network=none --read-only --tmpfs /tmp --cap-drop=all \
            --security-opt=no-new-privileges --entrypoint python "$NEMO_IMAGE" /opt/wattson-nemo/query.py --check
        echo 'PASS: required component images and model artifact installed; use just nemo-compare to run models.'
        ;;
    query)
        need_image "$NEMO_IMAGE"
        podman run --rm --pull=never --network=none --read-only --tmpfs /tmp --cap-drop=all \
            --security-opt=no-new-privileges --entrypoint just "$NEMO_IMAGE" \
            --justfile /opt/wattson-nemo/tasks/Justfile gencost-capital-cost "$@"
        ;;
    mcp)
        need_image "$NEMO_IMAGE"
        exec podman run --rm -i --pull=never --network=none --read-only --tmpfs /tmp \
            --cap-drop=all --security-opt=no-new-privileges "$NEMO_IMAGE"
        ;;
    cpu-help)
        need_image "$CPU_IMAGE"
        podman run --rm --pull=never --network=none "$CPU_IMAGE" "$@" --help
        ;;
    cpu-up)
        need_image "$CPU_IMAGE"
        MODEL_IMAGE=$(sh "$ROOT/huggingface/environment.sh" image "$MODEL")
        need_image "$MODEL_IMAGE"
        ensure_pod
        if podman container exists "$CPU_CONTAINER"; then
            EXISTING_MODEL=$(podman inspect --format '{{index .Config.Labels "wattson.model"}}' "$CPU_CONTAINER")
            [ "$EXISTING_MODEL" = "$MODEL_IMAGE" ] || { echo 'Run just nemo-down before changing the selected model.' >&2; exit 1; }
        fi
        if ! podman container exists "$CPU_CONTAINER"; then
            podman create --name "$CPU_CONTAINER" --pod "$POD" --pull=never --read-only \
                --tmpfs /tmp:rw,size=512m --cap-drop=all --security-opt=no-new-privileges \
                --memory=12g --memory-swap=12g --cpus=8 --pids-limit=256 --label wattson.nemo.example=true --label "wattson.model=$MODEL_IMAGE" \
                --env RAYON_NUM_THREADS=8 \
                --mount "type=image,source=$MODEL_IMAGE,destination=/model-image,rw=false" "$CPU_IMAGE" \
                serve --cpu -p 1234 -m /model-image/models --isq 4 --max-seqs 1 \
                --prefix-cache-n 0 --no-ui --token-source none
        fi
        if [ "$(podman inspect --format '{{.State.Running}}' "$CPU_CONTAINER")" != true ]; then
            podman start "$CPU_CONTAINER"
        fi
        echo 'CPU model service started in the private pod; no host port is published.'
        ;;
    benchmark)
        BACKEND=${1:-sm3lly}
        case "$BACKEND" in sm3lly|candle) ;; *) echo 'Backend must be sm3lly or candle' >&2; exit 2 ;; esac
        need_image "$AGENT_IMAGE"
        if [ "$BACKEND" = sm3lly ]; then
            ensure_pod remote
            BENCH_POD=$REMOTE_POD
        else
            ensure_pod
            BENCH_POD=$POD
        fi
        mkdir -p "$STATE/comparison/$BACKEND"
        podman run --rm --pod "$BENCH_POD" --pull=never --read-only --tmpfs /tmp:rw,size=512m \
            --user 0 --cap-drop=all --security-opt=no-new-privileges --memory=1g --cpus=4 --pids-limit=128 \
            --env WATTSON_SM3LLY_URL --volume "$STATE/comparison/$BACKEND:/results:Z" \
            "$AGENT_IMAGE" "$BACKEND" --out /results
        ;;
    compare)
        sh "$HERE/environment.sh" cpu-up
        # Readiness is checked before sending either arm's task prompts.
        podman run --rm --pod "$POD" --pull=never --read-only --tmpfs /tmp \
            --entrypoint python "$NEMO_IMAGE" -c \
            'import time,urllib.request
for attempt in range(30):
    try:
        urllib.request.urlopen("http://127.0.0.1:1234/v1/models",timeout=2).read(); break
    except OSError:
        if attempt == 29: raise
        time.sleep(1)'
        sh "$HERE/environment.sh" benchmark sm3lly
        sh "$HERE/environment.sh" benchmark candle
        podman logs "$CPU_CONTAINER" > "$STATE/comparison/candle-server.log" 2>&1
        podman inspect "$CPU_CONTAINER" > "$STATE/comparison/candle-container.json"
        podman run --rm --network=none --pull=never --read-only --user 0 \
            --volume "$STATE/comparison:/results:Z" --entrypoint python "$AGENT_IMAGE" \
            /opt/wattson-nemo/report.py /results
        ;;
    package)
        need_image "$AGENT_IMAGE"
        need_image "$SCANNER_IMAGE"
        WATTSON_OFFLINE=1 sh "$ROOT/huggingface/environment.sh" install
        PACKAGE=$(mktemp -d "$STATE/package-XXXXXXXX")
        mkdir -p "$PACKAGE/images" "$PACKAGE/sbom"
        for ROLE in runtime developer pause nemo agent cpu mcp-builder scanner hf-cli hf-mcp model-qwen model-granite; do
            case "$ROLE" in
                runtime) REF=localhost/wattson-command:example ;;
                developer) REF=localhost/wattson-developer:example ;;
                pause) REF=registry.k8s.io/pause:3.10 ;;
                nemo) REF=$NEMO_IMAGE ;;
                agent) REF=$AGENT_IMAGE ;;
                cpu) REF=$CPU_IMAGE ;;
                mcp-builder) REF=$BUILDER_IMAGE ;;
                scanner) REF=$SCANNER_IMAGE ;;
                hf-cli) REF=localhost/wattson-hf-cli:2.4.0 ;;
                hf-mcp) REF=localhost/wattson-hf-mcp:0.4.32 ;;
                model-qwen) REF=$(sh "$ROOT/huggingface/environment.sh" image qwen) ;;
                model-granite) REF=$(sh "$ROOT/huggingface/environment.sh" image granite) ;;
            esac
            need_image "$REF"
            podman image inspect "$REF" > "$PACKAGE/images/$ROLE.inspect.json"
            podman save --format oci-archive --output "$PACKAGE/images/$ROLE.tar" "$REF"
            podman run --rm --pull=never --network=none --read-only --tmpfs /tmp --user 0 \
                --env SYFT_CHECK_FOR_APP_UPDATE=false \
                --cap-drop=all --security-opt=no-new-privileges \
                --volume "$PACKAGE/images:/images:ro,Z" --volume "$PACKAGE/sbom:/sbom:Z" \
                "$SCANNER_IMAGE" "oci-archive:/images/$ROLE.tar" -o "cyclonedx-json=/sbom/$ROLE.cdx.json"
        done
        cp "$HERE/install-package.sh" "$PACKAGE/install.sh"
        podman run --rm --pull=never --network=none --read-only --tmpfs /tmp --user 0 \
            --cap-drop=all --security-opt=no-new-privileges \
            --volume "$ROOT:/source:ro,z" --volume "$PACKAGE:/package:Z" \
            --entrypoint python "$AGENT_IMAGE" /opt/wattson-nemo/package.py
        echo "Package: $PACKAGE/wattson-local.tar"
        ;;
    offline-mcp-build)
        need_image "$BUILDER_IMAGE"
        podman run --rm --pull=never --network=none "$BUILDER_IMAGE" \
            cargo build --offline --locked --release --bin just-mcp
        ;;
    down)
        if podman pod exists "$POD"; then podman pod rm --force "$POD"; fi
        if podman pod exists "$REMOTE_POD"; then podman pod rm --force "$REMOTE_POD"; fi
        echo 'Removed the model demo pod; model artifacts, results, images and SBOM packages retained.'
        ;;
    status)
        podman pod ps --filter label=wattson.nemo.example=true
        podman ps --all --filter label=wattson.nemo.example=true
        ;;
    *) echo 'Unknown environment action' >&2; exit 2 ;;
esac
