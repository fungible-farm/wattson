# WattsOn quick start. Needs only Podman and just on the host; the GPU is only for the model server.
#
#   just labs                  run labs 1-3 (CPU, no model, no GPU)
#   just up granite            start the pod: pull the model, start vLLM, start the harness
#   just doctor                check everything, including a real completion from the model
#   just ask "What scale factor ...?"      just eval smoke      just pi
#   just down                  stop the pod (the model and case-file volumes are kept)
#
# Override with environment variables, e.g.  WATTSON_TAG=sha-abc1234 just up qwen   or   WATTSON_PORT=8100 just up granite
# (WATTSON_PORT is the port on YOUR machine; inside the pod the server is always on 8000.)

set shell := ["bash", "-euo", "pipefail", "-c"]

registry  := env("WATTSON_REGISTRY", "ghcr.io/fungible-farm")
tag       := env("WATTSON_TAG", "latest")
vllm      := env("WATTSON_VLLM_IMAGE", "docker.io/vllm/vllm-openai:v0.31.0")
gpu_util  := env("WATTSON_GPU_UTIL", "0.85")
max_len   := env("WATTSON_MAX_MODEL_LEN", "32768")
log_level := env("WATTSON_LOG_LEVEL", "INFO")
host_port := env("WATTSON_PORT", "8000")
image     := registry + "/wattson-harness:" + tag
lab_image := registry + "/wattson:" + tag

default:
    @just --list

# --- no model, no GPU -------------------------------------------------------------------------------------------------------

# Run lab N's self-check (1, 2 or 3) in the container. The CSIRO case files are fetched into a volume on first use.
lab n:
    podman run --rm -v wattson-cases:/cases {{lab_image}} lab {{n}}

# Labs 1, 2 and 3 in turn.
labs:
    @for n in 1 2 3; do echo "=== lab $n"; podman run --rm -v wattson-cases:/cases {{lab_image}} lab $n; done

# Check the container environment (no model server).
check:
    podman run --rm -v wattson-cases:/cases {{lab_image}} doctor

# --- the pod: model server + harness ---------------------------------------------------------------------------------------

# Start the pod with a released student: granite (Granite 4.2 8B) or qwen (Qwen3.5-4B thinking).
up model="granite":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{model}}" in
      granite) repo=PromptExecutionAU/wattson-granite-4.2-8b-py; dir=granite-4.2-8b-py; reasoning=nemotron_v3 ;;
      qwen)    repo=PromptExecutionAU/wattson-qwen3.5-4b-pyt;    dir=qwen3.5-4b-pyt;    reasoning=qwen3 ;;
      *) echo "unknown model '{{model}}': use granite or qwen" >&2; exit 2 ;;
    esac
    mkdir -p "$PWD/out"
    for v in wattson-models wattson-cases; do podman volume exists "$v" || podman volume create "$v" >/dev/null; done
    sed -e "s|@IMAGE@|{{image}}|g" -e "s|@VLLM_IMAGE@|{{vllm}}|g" -e "s|@MODEL_REPO@|$repo|g" -e "s|@MODEL_DIR@|$dir|g" \
        -e "s|@REASONING@|$reasoning|g" -e "s|@GPU_UTIL@|{{gpu_util}}|g" -e "s|@MAX_LEN@|{{max_len}}|g" \
        -e "s|@LOG_LEVEL@|{{log_level}}|g" -e "s|@HOST_PORT@|{{host_port}}|g" -e "s|@WORK@|$PWD|g" kube/wattson.yaml > "$PWD/out/wattson.rendered.yaml"
    podman kube play --replace "$PWD/out/wattson.rendered.yaml"
    echo "pod started. The first run downloads the model; watch it with:  just logs pull-model"
    echo "ready when:  just doctor"

# Stop and remove the pod. The wattson-models and wattson-cases volumes stay, so the next `just up` does not download again.
down:
    -podman pod rm --force wattson

# Follow a container's log: pull-model | fetch-cases | vllm | harness
logs service="vllm":
    podman logs --follow wattson-{{service}}

# Containers in the pod and their state.
ps:
    podman ps --all --pod --filter pod=wattson --format "table {{{{.Names}}}}\t{{{{.Status}}}}\t{{{{.Image}}}}"

# Full diagnostics, including one real completion from the model server.
doctor:
    podman exec wattson-harness bash -lc "just --justfile /app/justfile doctor"

# One question through the real tool harness.
ask question:
    podman exec wattson-harness bash -lc 'just --justfile /app/justfile ask "$1"' _ "{{question}}"

# Run a frozen suite: smoke | heldout | judgement | unsolvable | planning | derived
eval suite="smoke":
    podman exec wattson-harness bash -lc "just --justfile /app/justfile eval {{suite}}"

# Interactive pi agent, wired to the model server and the WattsOn tools.
pi:
    podman exec -it wattson-harness bash -lc "just --justfile /app/justfile pi"

# A shell in the harness container.
shell:
    podman exec -it wattson-harness bash -l

# --- build and publish locally (CI does this for the registry) -----------------------------------------------------------

# Build both images from this checkout.
build:
    podman build -f containers/Containerfile --target wattson -t {{lab_image}} .
    podman build -f containers/Containerfile --target harness -t {{image}} .
