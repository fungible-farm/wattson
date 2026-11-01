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
hf_image  := registry + "/wattson-hf:" + tag
csiro_image := registry + "/wattson-csiro:" + tag

default:
    @just --list

# --- no model, no GPU -------------------------------------------------------------------------------------------------------

# Fetch the CSIRO case files into the wattson-cases volume (the wattson-csiro image; skipped when they are already there and verified).
cases:
    @podman volume exists wattson-cases || podman volume create wattson-cases >/dev/null
    @podman run --rm -v wattson-cases:/cases {{csiro_image}} fetch >/dev/null

# Run lab N's self-check (1, 2 or 3) in the container.
lab n: cases
    podman run --rm -v wattson-cases:/cases {{lab_image}} lab {{n}}

# Labs 1, 2 and 3 in turn.
labs: cases
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
    sed -e "s|@IMAGE@|{{image}}|g" -e "s|@HF_IMAGE@|{{hf_image}}|g" -e "s|@CSIRO_IMAGE@|{{csiro_image}}|g" -e "s|@VLLM_IMAGE@|{{vllm}}|g" -e "s|@MODEL_REPO@|$repo|g" -e "s|@MODEL_DIR@|$dir|g" \
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

# One question through the real tool harness. Add --enforce to refuse any answer containing a number no tool produced (docs/evaluation.md).
ask question *flags:
    podman exec wattson-harness bash -lc 'just --justfile /app/justfile ask "$1" $2' _ "{{question}}" "{{flags}}"

# Lab 1's task given to the served model, graded by re-solving, next to the lab's committed bisection result. Add --enforce for the guard.
lab1-agent *flags:
    podman exec wattson-harness bash -lc "just --justfile /app/justfile lab1-agent {{flags}}"

# Run a frozen suite: smoke | heldout | judgement | unsolvable | planning | derived. Add --enforce for the runtime provenance guard.
eval suite="smoke" *flags:
    podman exec wattson-harness bash -lc "just --justfile /app/justfile eval {{suite}} {{flags}}"

# Interactive pi agent, wired to the model server and the WattsOn tools.
pi:
    podman exec -it wattson-harness bash -lc "just --justfile /app/justfile pi"

# A shell in the harness container.
shell:
    podman exec -it wattson-harness bash -l

# --- the demo --------------------------------------------------------------------------------------------------------------

# The whole demo, in order, with a banner before each step (docs/demo.md). Needs a GPU. Does not run pi (interactive).
demo model="granite":
    #!/usr/bin/env bash
    set -euo pipefail
    banner() { printf '\n\n==================== %s ====================\n' "$1"; }
    banner "1/6  labs 1-3, in a container (no GPU, no model)"; just labs
    banner "2/6  start the pod: model puller, CSIRO fetcher, model server, harness"; just up {{model}}
    echo "waiting for the model server..."; until curl -sf "http://127.0.0.1:{{host_port}}/health" >/dev/null 2>&1; do sleep 5; done
    banner "3/6  diagnostics, including one real completion"; just doctor
    banner "4/6  one question through the tool harness"
    just ask "Using the power flow solver, find the scale factor s in [0.9, 1.1] applied to the active-power dispatch of generator 2 on case 'snem-sa-2000' such that the loading of transformer 4 equals 0.184093 percent (within 0.0006941900815668939 percent). Return only the scale factor."
    banner "5/6  lab 1's task given to the model, next to the lab's own bisection result"; just lab1-agent --enforce
    banner "6/6  20 tasks graded by re-solving, with the guard on"; just eval smoke --enforce
    banner "done. 'just pi' for the interactive agent, 'just down' to stop"

# --- build and publish locally (CI does this for the registry) -----------------------------------------------------------

# Build all four images from this checkout.
build:
    podman build -f containers/Containerfile --target wattson -t {{lab_image}} .
    podman build -f containers/Containerfile --target harness -t {{image}} .
    podman build -f containers/hf/Containerfile -t {{hf_image}} .
    podman build -f containers/csiro/Containerfile -t {{csiro_image}} .
