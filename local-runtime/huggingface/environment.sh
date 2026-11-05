#!/bin/sh
# The host owns orchestration; HF tools and model weights live in separate images.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)
STATE="$ROOT/.wattson/huggingface"
CLI=localhost/wattson-hf-cli:2.4.0
MCP=localhost/wattson-hf-mcp:0.4.32
ACTION=${1:-components}
if [ "$#" -gt 0 ]; then shift; fi
mkdir -p "$STATE"

spec() {
    podman run --rm --memory=2g --memory-swap=2g --cpus=2 --pids-limit=256 --pull=never --network=none --read-only --tmpfs /tmp \
        --entrypoint python "$CLI" /opt/hf/models.py spec "$1" "$2"
}

case "$ACTION" in
    components)
        printf '%s\n' 'HF CLI: huggingface_hub 2.4.0 (Apache-2.0), uv-locked Python image.' \
          'HF MCP: official @llmindset/hf-mcp-server 0.4.32 (MIT), npm-locked native image.' \
          'Models: both PromptExecutionAU Wattson repositories, immutable revisions in huggingface/models.json.' \
          'Model OCI images: scratch data-only /models trees, separately accounted and mounted read-only.' \
          'Preparation uses the HF Hub; disconnected inference and model verification require no Hub service.'
        ;;
    build)
        podman build --target cli -t "$CLI" -f "$HERE/Containerfile" "$HERE"
        podman build --target mcp -t "$MCP" -f "$HERE/Containerfile" "$HERE"
        ;;
    install)
        sh "$HERE/environment.sh" components
        if ! podman image exists "$CLI" || ! podman image exists "$MCP"; then
            [ "${WATTSON_OFFLINE:-0}" != 1 ] || { echo 'Import HF tool images first.' >&2; exit 1; }
            sh "$HERE/environment.sh" build
        fi
        for MODEL in qwen granite; do
            IMAGE=$(spec "$MODEL" image)
            if podman image exists "$IMAGE"; then
                sh "$HERE/environment.sh" verify "$MODEL"
            else
                [ "${WATTSON_OFFLINE:-0}" != 1 ] || { echo "Import model image: $IMAGE" >&2; exit 1; }
                sh "$HERE/environment.sh" model "$MODEL"
            fi
        done
        ;;
    model)
        MODEL=${1:?Model qwen or granite required}
        IMAGE=$(spec "$MODEL" image)
        REPO=$(spec "$MODEL" repo_id)
        REVISION=$(spec "$MODEL" revision)
        mkdir -p "$STATE/$MODEL"
        podman run --rm --memory=2g --memory-swap=2g --cpus=2 --pids-limit=256 --pull=never --read-only --tmpfs /tmp --user 0 \
            --cap-drop=all --security-opt=no-new-privileges \
            --volume "$STATE/$MODEL:/stage:Z" --entrypoint python "$CLI" \
            /opt/hf/models.py download "$MODEL"
        # HF download metadata is disposable and must not become another model layer.
        printf '%s\n' '.cache' '*.partial' > "$STATE/$MODEL/.containerignore"
        podman build --network=none --pull=never --build-arg "MODEL_REPO=$REPO" \
            --build-arg "MODEL_REVISION=$REVISION" -t "$IMAGE" \
            -f "$HERE/Containerfile.model" "$STATE/$MODEL"
        sh "$HERE/environment.sh" verify "$MODEL"
        ;;
    verify)
        MODEL=${1:?Model qwen or granite required}
        IMAGE=$(spec "$MODEL" image)
        podman run --rm --memory=2g --memory-swap=2g --cpus=2 --pids-limit=256 --pull=never --network=none --read-only --tmpfs /tmp \
            --mount "type=image,source=$IMAGE,destination=/artifact,rw=false" \
            --entrypoint python "$CLI" /opt/hf/models.py verify "$MODEL"
        ;;
    image) spec "${1:?Model required}" image ;;
    probe)
        podman run --rm --memory=2g --memory-swap=2g --cpus=2 --pids-limit=256 --pull=never --read-only --tmpfs /tmp --user 0 \
            --volume "$STATE:/evidence:Z" --entrypoint node "$MCP" /opt/hf-mcp/probe.mjs
        ;;
    hf)
        exec podman run --rm --memory=2g --memory-swap=2g --cpus=2 --pids-limit=256 -i --pull=never --read-only --tmpfs /tmp "$CLI" "$@"
        ;;
    mcp)
        # The Hub MCP is a preparation-time service; it is not an offline Hub mirror.
        exec podman run --rm --memory=2g --memory-swap=2g --cpus=2 --pids-limit=256 -i --pull=never --read-only --tmpfs /tmp \
            --cap-drop=all --security-opt=no-new-privileges "$MCP"
        ;;
    *) echo 'Use components|build|install|model|verify|image|probe|hf|mcp' >&2; exit 2 ;;
esac
