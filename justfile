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

set positional-arguments := true

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
kit_image := env("WATTSON_KIT_IMAGE", registry + "/wattson-kit:" + tag)
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
    podman run --rm -v wattson-cases:/cases {{lab_image}} lab "$1"

# Labs 1, 2 and 3 in turn.
labs: cases
    @for n in 1 2 3; do echo "=== lab $n"; podman run --rm -v wattson-cases:/cases {{lab_image}} lab $n; done

# Check the container environment (no model server).
check:
    ./wattson check

# --- the pod: model server + harness ---------------------------------------------------------------------------------------

# Start the pod with a released student: granite (Granite 4.2 8B) or qwen (Qwen3.5-4B thinking).
up model="granite":
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$PWD/out"
    for v in wattson-models wattson-cases; do podman volume exists "$v" || podman volume create "$v" >/dev/null; done
    just pod-render "$1" "$PWD/out/wattson.rendered.yaml"
    # WATTSON_POD_HOST_LOOPBACK=1 lets the pod reach services on the host's loopback (169.254.1.2), e.g. a development registry on 127.0.0.1:5001.
    # Off by default: it widens what the pod can reach.
    net=""; [ "${WATTSON_POD_HOST_LOOPBACK:-0}" = "1" ] && net="--network=pasta:--map-host-loopback,169.254.1.2"
    podman kube play --replace $net "$PWD/out/wattson.rendered.yaml"
    echo "pod started. The first run downloads the model; watch it with:  just logs pull-model"
    echo "ready when:  just doctor"

# Render the pod spec to a file. With WATTSON_MODEL_KIT=<registry/repo@sha256:...> (and WATTSON_COSIGN_PUBKEY=<path to the cosign public key>)
# the pull-model init container is wattson-kit (signature verified, digest-pinned); otherwise it is the Hugging Face downloader.
# WATTSON_KIT_INSECURE=1 for a plain-HTTP development registry. Nothing is started.
pod-render model="granite" out="out/wattson.rendered.yaml":
    #!/usr/bin/env bash
    set -euo pipefail
    case "$1" in
      granite) repo=PromptExecutionAU/wattson-granite-4.2-8b-py; dir=granite-4.2-8b-py; reasoning=nemotron_v3 ;;
      qwen)    repo=PromptExecutionAU/wattson-qwen3.5-4b-pyt;    dir=qwen3.5-4b-pyt;    reasoning=qwen3 ;;
      *) echo "unknown model '$1': use granite or qwen" >&2; exit 2 ;;
    esac
    kit="${WATTSON_MODEL_KIT:-}"; pub="${WATTSON_COSIGN_PUBKEY:-}"
    if [ -n "$kit" ]; then
        case "$kit" in *@sha256:*) ;; *) echo "WATTSON_MODEL_KIT must be pinned by digest (registry/repo@sha256:...), not a tag" >&2; exit 2 ;; esac
        [ -r "$pub" ] || { echo "WATTSON_COSIGN_PUBKEY must name a readable cosign public key when WATTSON_MODEL_KIT is set" >&2; exit 2; }
        drop='/@HF-BEGIN@/,/@HF-END@/d'
    else
        drop='/@KIT-BEGIN@/,/@KIT-END@/d'
    fi
    sed -e "$drop" kube/wattson.yaml | sed -e "/@HF-BEGIN@/d;/@HF-END@/d;/@KIT-BEGIN@/d;/@KIT-END@/d" \
        -e "s|@IMAGE@|{{image}}|g" -e "s|@HF_IMAGE@|{{hf_image}}|g" -e "s|@KIT_IMAGE@|{{kit_image}}|g" -e "s|@CSIRO_IMAGE@|{{csiro_image}}|g" -e "s|@VLLM_IMAGE@|{{vllm}}|g" -e "s|@MODEL_REPO@|$repo|g" -e "s|@MODEL_DIR@|$dir|g" \
        -e "s|@MODEL_KIT@|$kit|g" -e "s|@COSIGN_PUB@|$pub|g" -e "s|@KIT_INSECURE@|${WATTSON_KIT_INSECURE:-0}|g" \
        -e "s|@REASONING@|$reasoning|g" -e "s|@GPU_UTIL@|{{gpu_util}}|g" -e "s|@MAX_LEN@|{{max_len}}|g" \
        -e "s|@LOG_LEVEL@|{{log_level}}|g" -e "s|@HOST_PORT@|{{host_port}}|g" -e "s|@WORK@|$PWD|g" > "$2"
    echo "pod-render: wrote $2 ($([ -n "$kit" ] && echo "model from kit $kit" || echo "model from Hugging Face $repo"))"

# The pod spec renders both ways (Hugging Face / signed kit), keeps exactly one pull-model init container, and refuses a tag or a missing key. Starts nothing.
pod-render-check:
    @python3 scripts/pod_render_check.py

# Stop and remove the pod. The wattson-models and wattson-cases volumes stay, so the next `just up` does not download again.
down:
    -podman pod rm --force wattson

# Follow a container's log: pull-model | fetch-cases | vllm | harness
logs service="vllm":
    podman logs --follow "wattson-$1"

# Containers in the pod and their state.
ps:
    podman ps --all --pod --filter pod=wattson --format "table {{{{.Names}}}}\t{{{{.Status}}}}\t{{{{.Image}}}}"

# Full diagnostics, including one real completion from the model server.
doctor:
    podman exec wattson-harness bash -lc "just --justfile /app/justfile doctor"

# One question through the real tool harness. Add --enforce to refuse any answer containing a number no tool produced (docs/evaluation.md).
ask question *flags:
    podman exec wattson-harness bash -lc 'exec just --justfile /app/justfile ask "$@"' _ "$@"

# Lab 1's task given to the served model, graded by re-solving, next to the lab's committed bisection result. Add --enforce for the guard.
lab1-agent *flags:
    podman exec wattson-harness bash -lc 'exec just --justfile /app/justfile lab1-agent "$@"' _ "$@"

# Run a frozen suite: smoke | heldout | judgement | unsolvable | planning | derived. Add --enforce for the runtime provenance guard.
eval suite="smoke" *flags:
    podman exec wattson-harness bash -lc 'exec just --justfile /app/justfile eval "$@"' _ "$@"

# Interactive pi agent, wired to the model server and the WattsOn tools.
pi *args:
    podman exec -it wattson-harness bash -lc 'exec just --justfile /app/justfile pi "$@"' _ "$@"

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
    banner "2/6  start the pod: model puller, CSIRO fetcher, model server, harness"; just up "$1"
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

# Build the CPU-only developer tools and migrated checks; no host Python/Rust install.
developer-build:
    podman build -f containers/Containerfile --target developer -t localhost/wattson-developer:port .

# Run developer checks offline. Input artifacts must already be approved and staged.
developer-test:
    podman run --rm --pull=never --network=none --memory=4g --memory-swap=4g --cpus=4 --pids-limit=256 --tmpfs /tmp -v "$PWD/.artifacts:/app/.artifacts:ro,z" -v "$PWD/data:/cases:ro,z" localhost/wattson-developer:port -m pytest -q -rs
# --- independent dbt MCP integration (CPU, no model or cloud credentials) -------------------------------------------------

# Build pinned upstream dbt MCP + dbt Core/DuckDB in an independent environment.
dbt-mcp-build:
    integrations/dbt-mcp/run.sh build

# Prove stdio initialization, the tool allowlist, and a real offline DuckDB build.
dbt-mcp-check:
    integrations/dbt-mcp/run.sh smoke

# Start stdio MCP for an agent; do not allocate a terminal or mix stdout with logs.
dbt-mcp-stdio:
    @integrations/dbt-mcp/run.sh serve

# Baseline real build followed by injected build failure; require no stale success proof.
dbt-mcp-regression:
    integrations/dbt-mcp/run.sh regression

# List runtime components, isolation boundaries and acquisition requirements.
components:
    @./wattson components

# Delegate a bounded read-only task to local pi using the harness image.
# Override URL/model/timeout: WATTSON_DELEGATE_URL=http://host:8002/v1 WATTSON_DELEGATE_MODEL=neo-coder WATTSON_DELEGATE_TIMEOUT=300 just delegate task.txt output-dir
delegate task output:
    @./wattson delegate "$@"

# Install the kit CLI binary (downloads, verifies checksum, installs to .tools/kit).
kit-install:
    python3 scripts/install_kit.py

# Guarded pack check: walk a directory and reject anything not explicitly allowed.
kit-pack-check dir allow:
    python3 scripts/kit_pack_check.py "$1" --allow-file "$2"

# Model supply by digest (#48): a throwaway registry, an init-container unpack, a wrong-digest refusal and an overwritten-tag demonstration. Needs kit-install.
kit-supply-check:
    @python3 scripts/kit_supply_check.py

# Import a released student from Hugging Face as a ModelKit and hash-compare every unpacked file with the Hub originals (#48). About 9 GB for the default; needs kit-install.
kit-import-check *repo:
    @python3 scripts/kit_import_check.py {{repo}}

# Install the cosign CLI (checksum verified before it is installed; to .tools/cosign/cosign).
cosign-install:
    python3 scripts/install_cosign.py

# Signed model supply (#47): cosign verifies a signed kit and refuses an unsigned or tampered one and a wrong key. Throwaway local registry. Needs kit-install and cosign-install.
cosign-supply-check:
    @python3 scripts/cosign_supply_check.py

# Build the init-container image that pulls a signed, digest-pinned ModelKit (#48): wattson-kit (kit + cosign + pull-kit).
kit-image-build:
    podman build -f containers/kit/Containerfile -t localhost/wattson-kit:local .

# The model-supply init container end to end (#48): signed digest unpacks; unsigned digest, a tag and a wrong key are refused. Needs kit-install, cosign-install, kit-image-build.
kit-init-check:
    @python3 scripts/kit_init_check.py

# Publish evaluation evidence as a kit package.
kit-evidence run ref *flags:
    python3 scripts/kit_publish.py evidence "$1" --ref "$2" "${@:3}"

# Publish the agent kit (gateway config, skills, justfile, diagrams) as a kit package.
kit-agent ref *flags:
    python3 scripts/kit_publish.py agent --ref "$1" "${@:2}"

# Prepare core images without host Python/Rust/Node; select optional profiles explicitly.
install profile="core":
    ./wattson install "$@"

# One interface for optional services: service NAME build|check|stdio [ARGS...].
service name action *args:
    @./wattson service "$@"

# Package the experimental offline runtime (see its separate acceptance gates).
package:
    ./wattson package

# --- Excel/XLSM read (deterministic, read-only, no network; every layer logged to stderr) -------------------------------------
# e.g.  just excel-read ~/isp.xlsm "Approval sheet" A2:A2        just excel-read ~/isp.xlsm "Approval sheet" A1:C3 --mode formulas
excel-read workbook sheet range *flags:
    python3 scripts/excel_read.py "$1" "$2" "$3" "${@:4}"

excel-build:
    integrations/excel-mcp/run.sh build

# --- Diagrams (beta): render docs/diagrams/*.d2 (also .dot) to SVG through a Kroki server; the text goes only to the server you name -----
# Needs a Kroki at WATTSON_KROKI_URL (default http://127.0.0.1:8010). Our sibling project kr0ki (github.com/promptexecution/kr0ki) runs one
# (x86_64 only for now, see kr0ki#150); on arm64 point WATTSON_KROKI_URL at an x86_64 peer. Mermaid needs a Kroki companion, so use .d2 or .dot.
diagrams:
    #!/usr/bin/env bash
    set -euo pipefail
    url="${WATTSON_KROKI_URL:-http://127.0.0.1:8010}"
    curl -fsS "$url/health" >/dev/null || { echo "no Kroki at $url - set WATTSON_KROKI_URL, or run one from github.com/promptexecution/kr0ki (just dev-kroki-up)" >&2; exit 1; }
    shopt -s nullglob
    python3 scripts/justfile_diagram.py > docs/diagrams/recipes.d2   # the recipe graph is generated from this justfile
    for f in docs/diagrams/*.d2 docs/diagrams/*.dot; do
        case "$f" in *.d2) kind=d2 ;; *.dot) kind=graphviz ;; esac
        out="${f%.*}.svg"; tmp="$(mktemp)"
        echo "[diagrams] render source=$f kind=$kind kroki=$url" >&2
        curl -fsS -H 'Content-Type: text/plain' --data-binary @"$f" "$url/$kind/svg" -o "$tmp"
        grep -q '<svg' "$tmp" || { echo "[diagrams] FAIL $f: reply is not an SVG" >&2; rm -f "$tmp"; exit 1; }
        mv "$tmp" "$out"
        echo "[diagrams] wrote $out bytes=$(wc -c < "$out")" >&2
    done

# --- model gateway: agentgateway in front of llama-swap ---------------------------------------------------------------------
# Config lives in gateway/agentgateway.yaml (git). The container reaches llama-swap's loopback-only port through pasta's host-loopback map.

# Content-pinned: the tag is for reading, the digest is what podman resolves (multi-arch INDEX digest).
# Refresh both with:  skopeo inspect docker://ghcr.io/agentgateway/agentgateway | jq -r .Digest
gw_image := env("WATTSON_GATEWAY_IMAGE", "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b")
gw_port  := env("WATTSON_GATEWAY_PORT", "4000")
gw_name  := "wattson-gateway"

# Start the local model gateway on 127.0.0.1:4000 (OpenAI-compatible, forwards to llama-swap on 127.0.0.1:8000).
gateway-up:
    @echo "gateway-up: starting {{gw_name}} ({{gw_image}}) on 127.0.0.1:{{gw_port}} -> llama-swap 127.0.0.1:8000"
    @podman rm -f {{gw_name}} >/dev/null 2>&1 || true
    @podman run -d --name {{gw_name}} --pull=missing --restart=on-failure:3 --read-only --cap-drop=all --security-opt=no-new-privileges --memory=512m --memory-swap=512m --cpus=1 --pids-limit=128 --network=pasta:--map-host-loopback,169.254.1.2 -p 127.0.0.1:{{gw_port}}:4000 -v "$PWD/gateway:/etc/agentgateway:ro,Z" {{gw_image}} -f /etc/agentgateway/agentgateway.yaml >/dev/null
    @for i in $(seq 1 20); do curl -fs -o /dev/null --max-time 2 http://127.0.0.1:{{gw_port}}/v1/models && { echo "gateway-up: ready"; exit 0; }; sleep 0.5; done; echo "gateway-up: not answering; see just gateway-logs" >&2; exit 1

# Stop and remove the gateway container.
gateway-down:
    @echo "gateway-down: removing {{gw_name}}"
    @podman rm -f {{gw_name}} >/dev/null 2>&1 || true

# Show whether the gateway container is running and answering.
gateway-status:
    @echo "gateway-status: container state and a probe of http://127.0.0.1:{{gw_port}}/v1/models"
    @podman ps -a --filter name=^{{gw_name}}$ --format '{{"{{.Names}}  {{.Status}}  {{.Ports}}"}}'
    @curl -sS -o /dev/null -w 'GET /v1/models -> http %{http_code}\n' --max-time 3 http://127.0.0.1:{{gw_port}}/v1/models || true

# Show the gateway's request log (one 'request' record per call); pass -f to follow.
gateway-logs *args:
    @echo "gateway-logs: request records from {{gw_name}} (stdout access log)"
    @podman logs {{args}} {{gw_name}} 2>&1 | grep -E '\srequest\s' || true

# Refuse a model that would not fit in memory BEFORE downloading or loading it. Args: file, directory or size literal (233GB, 121GiB); optional --fraction F.
model-guard *args:
    python3 scripts/model_mem_guard.py {{args}}

# Live half of #27: prove gateway hot-reloads MCP tool list without restart.
gateway-reload-check:
    @uv run python scripts/gateway_reload_check.py

# Validate and render the four gateway registries (backends, tools, skills, prompts).
registry-check *args:
    @python3 scripts/registry_check.py --render {{args}} 2>&1 | grep -E '^(PASS|FAIL) '

# Failure behaviour of the gateway (#24): a request is logged; a stopped backend gives a 5xx, a non-zero client exit and an error record.
gateway-failure-check:
    @uv run python scripts/gateway_failure_check.py

# Two harnesses, one gateway (#28): pi and Open Notebook each make a model call configured with only the gateway URL; compares the gateway log records. Loads a model on llama-swap.
harness-compare:
    @python3 scripts/harness_compare.py

# What the gateway log records for an MCP tool call (#28): tool name and target by default, arguments only by opt-in. Throwaway server and gateways.
gateway-mcp-log-check:
    @uv run python scripts/gateway_mcp_log_check.py

# Print the gateway as a Quadlet (starts at boot under your user systemd) and the install commands. Installs nothing.
gateway-quadlet:
    @python3 scripts/gateway_quadlet_check.py --print

# Reboot survival (#24), checked without installing: podman's generator turns the Quadlet into a service equal to gateway-up.
gateway-quadlet-check:
    @python3 scripts/gateway_quadlet_check.py

# A Candle (mistral.rs CPU) backend behind the gateway (#24): answered and logged; stopped gives a 5xx, a non-zero client exit and error records. First run downloads about 1.2 GB.
gateway-candle-check:
    @python3 scripts/gateway_candle_check.py

# Model-name routing through the REAL gateway config (#24): candle-qwen3-0.6b goes to Candle, other names to llama-swap. Starts and stops what it needs.
gateway-routing-check:
    @python3 scripts/gateway_routing_check.py

# Measured memory footprint of each engine this repo starts itself (#29): embedding, reranker, Candle CPU. Starts and stops them; needs about 8 GiB free.
engine-footprint:
    @python3 scripts/engine_footprint.py

# pi (built-in MCP) calls a tool over the gateway MCP route; pi's own record and the gateway journal must agree (#28). Throwaway server and gateway; uses the sm3lly model endpoint.
pi-mcp-check:
    @uv run python scripts/pi_mcp_check.py

# The concurrent-hosting gate (#15, #29): PASS/FAIL per engine set under the owner rule (peak memory <= 85% of MemTotal and zero failed requests). Starts and stops the engines.
engine-pairs:
    @python3 scripts/engine_pairs.py

# Run a foreground command while holding the exclusive GPU lease (#29); a second engine is refused with exit 75. Usage: just gpu-run llm-chat <command> [args]
gpu-run purpose *command:
    python3 scripts/gpu_lease_run.py {{purpose}} {{command}}

# The #48 acceptance: just up with weights from a signed, digest-pinned ModelKit, then doctor. Needs the GPU and a ~10 GB first download; refuses if a wattson-models volume exists.
kit-pod-check:
    bash scripts/kit_pod_check.sh

# Demo 1 (#98): an audited grid study. pi asks the solver through the gateway; the gateway journals the call; the answer must equal the solver's own numbers. Uses the sm3lly model endpoint.
demo-grid-study:
    @uv run python scripts/demo_grid_study.py

# Check that every command named in the hand-written state charts exists, that every transition names a command and a pass observation, and that the generated recipe graph is current.
diagrams-check:
    python3 scripts/diagram_check.py docs/diagrams/pod-lifecycle.d2 docs/diagrams/gateway-lifecycle.d2 docs/diagrams/delivery-flow.d2 docs/diagrams/model-load.d2 docs/diagrams/kit-lifecycle.d2 docs/diagrams/retrieval-lifecycle.d2 docs/diagrams/auto-ontology-lifecycle.d2 docs/diagrams/registry-change.d2 docs/diagrams/gateway-reload.d2 docs/diagrams/gateway-failure.d2 docs/diagrams/engine-footprint.d2 docs/diagrams/engine-pairs.d2 docs/diagrams/gateway-routing.d2 docs/diagrams/gateway-candle.d2 docs/diagrams/gateway-boot.d2 docs/diagrams/gateway-mcp-log.d2 docs/diagrams/gpu-lease.d2 docs/diagrams/kit-supply.d2 docs/diagrams/cosign-supply.d2 docs/diagrams/kit-init.d2 docs/diagrams/kit-pod.d2 docs/diagrams/demo-grid-study.d2 docs/diagrams/harness-compare.d2 docs/diagrams/kit-import.d2 docs/diagrams/pi-mcp.d2
    python3 scripts/justfile_diagram.py --check docs/diagrams/recipes.d2

# --- retrieval: embedding and reranker model servers (CPU, separate containers, behind the gateway) ---------------------------
# Embedding: nomic-embed-text-v1.5 (768-dim). Reranker: bge-reranker-v2-m3. Both Q4_K_M GGUF, each <1 GB.
# Models are downloaded once into .models/ (gitignored). Each server is a separate llama.cpp container with a 2 GB memory limit.

# Pinned: a Hugging Face commit and the file's sha256 (verified after download), and the llama.cpp image by multi-arch INDEX digest.
embed_model := "nomic-embed-text-v1.5.Q4_K_M.gguf"
embed_repo := "nomic-ai/nomic-embed-text-v1.5-GGUF"
embed_rev := "0188c9bf409793f810680a5a431e7b899c46104c"
embed_sha256 := "d4e388894e09cf3816e8b0896d81d265b55e7a9fff9ab03fe8bf4ef5e11295ac"
embed_url := "https://huggingface.co/" + embed_repo + "/resolve/" + embed_rev + "/" + embed_model
rerank_model := "bge-reranker-v2-m3-Q4_K_M.gguf"
rerank_repo := "gpustack/bge-reranker-v2-m3-GGUF"
rerank_rev := "3093af03b1a635e67b084b1d8c03c5f5e020fd05"
rerank_sha256 := "e186a244ed455b4ab66ec64339ce7427a6ae13f5c0b5e544de96e50f0f8b3673"
rerank_url := "https://huggingface.co/" + rerank_repo + "/resolve/" + rerank_rev + "/" + rerank_model
llama_image := env("WATTSON_LLAMA_IMAGE", "ghcr.io/ggml-org/llama.cpp:server@sha256:c0a11067c72bb1eb330b8bddbf9869274c195e596bf4fae55fd527102e644b85")

# --- Candle (mistral.rs) CPU backend for small jobs, behind the gateway as model `candle-qwen3-0.6b` (about 3 GB, about 10 tokens/s) ---
candle_image := env("WATTSON_CANDLE_IMAGE", "ghcr.io/ericlbuehler/mistral.rs:cpu-latest@sha256:aec36c75207e094d6ab6a86d35697268141b96ffcbd0fb980440fd3001d8e0e6")

# Start the Candle CPU server on 127.0.0.1:3301 (first run downloads about 1.2 GB into .models/hf-cache).
candle-up:
    @mkdir -p .models/hf-cache && chmod 777 .models/hf-cache
    @podman rm -f wattson-candle >/dev/null 2>&1 || true
    @podman run -d --name wattson-candle --pull=missing -p 127.0.0.1:3301:1234 -e HF_HOME=/cache -v "$PWD/.models/hf-cache:/cache:z" {{candle_image}} serve --cpu --host 0.0.0.0 -p 1234 -m Qwen/Qwen3-0.6B >/dev/null
    @for i in $(seq 1 300); do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:3301/v1/models)" = 200 ] && { echo "candle-up: ready"; exit 0; }; sleep 3; done; echo "candle-up: not ready after 900 s; see podman logs wattson-candle" >&2; exit 1

# Stop and remove the Candle CPU server.
candle-down:
    @echo "candle-down: removing wattson-candle"
    @podman rm -f wattson-candle >/dev/null 2>&1 || true

# --- a 2048-dimension embedder for Auto Ontology (its vector column is vector(2048); nomic emits 768) -------------------------
# jina-embeddings-v4 (text-retrieval GGUF, llama.cpp, no remote code): LICENCE is the Qwen Research License, non-commercial: see NOTICE.md.
# Spike use only. Needs `--pooling mean`; inputs ideally carry a "Query: " / "Passage: " prefix, which Auto Ontology does not add.
jina_repo := "jinaai/jina-embeddings-v4-text-retrieval-GGUF"
jina_rev := "6c6ba828f6ad0faee901b4bce25d96faade908d1"
jina_model := "jina-embeddings-v4-text-retrieval-Q4_K_M.gguf"
jina_sha256 := "4e24d1b6631fe21139b360001da86b519d40e1881a2c06f1183dda8aa0ba7fc1"

# Start the 2048-dim embedder on 127.0.0.1:8083 (downloads about 1.9 GB, sha256-verified, into .models). Use with WATTSON_EMBED_PORT=8083 WATTSON_EMBED_MODEL=jina-embeddings-v4 just ao-up.
embed2048-up:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p .models
    f=".models/{{jina_model}}"
    if [ ! -f "$f" ] || [ "$(sha256sum "$f" | cut -d' ' -f1)" != "{{jina_sha256}}" ]; then
        echo "embed2048-up: downloading {{jina_model}} (pinned revision {{jina_rev}}; Qwen Research License, see NOTICE.md)"
        rm -f "$f.part"; curl -L -f -o "$f.part" "https://huggingface.co/{{jina_repo}}/resolve/{{jina_rev}}/{{jina_model}}"
        [ "$(sha256sum "$f.part" | cut -d' ' -f1)" = "{{jina_sha256}}" ] || { rm -f "$f.part"; echo "embed2048-up: sha256 mismatch; nothing installed" >&2; exit 1; }
        mv "$f.part" "$f"
    fi
    python3 scripts/model_mem_guard.py "$f" || { echo "embed2048-up: memory guard failed" >&2; exit 1; }
    podman rm -f wattson-embed2048 >/dev/null 2>&1 || true
    podman run -d --name wattson-embed2048 --pull=missing --restart=on-failure:3 --read-only --cap-drop=all --security-opt=no-new-privileges --memory=6g --memory-swap=6g --cpus=4 --pids-limit=128 -p 127.0.0.1:8083:8080 -v "$PWD/.models:/models:ro,Z" {{llama_image}} -m "/models/{{jina_model}}" --embedding --pooling mean -c 8192 -b 8192 -ub 8192 --host 0.0.0.0 --port 8080 >/dev/null
    for i in $(seq 1 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8083/health)" = 200 ] && { echo "embed2048-up: ready"; exit 0; }; sleep 2; done
    echo "embed2048-up: not ready after 120 s; see podman logs wattson-embed2048" >&2; exit 1

# Stop and remove the 2048-dim embedder.
embed2048-down:
    @echo "embed2048-down: removing wattson-embed2048"
    @podman rm -f wattson-embed2048 >/dev/null 2>&1 || true

# Start the embedding and reranker servers as separate containers (ports 8081, 8082). Downloads models if missing; runs memory guard.
retrieval-up:
    #!/usr/bin/env bash
    set -euo pipefail
    # Download to a .part file, verify the pinned sha256, and only then move it into place: a failed or tampered
    # download can never be mistaken for a model on the next run.
    fetch() {  # file url sha256
        if [ -f ".models/$1" ] && [ "$(sha256sum ".models/$1" | cut -d' ' -f1)" = "$3" ]; then
            echo "retrieval-up: $1 present, sha256 verified"; return 0
        fi
        echo "retrieval-up: downloading $1 (pinned revision)"
        rm -f ".models/$1.part"
        curl -L -f -o ".models/$1.part" "$2"
        got="$(sha256sum ".models/$1.part" | cut -d' ' -f1)"
        if [ "$got" != "$3" ]; then
            rm -f ".models/$1.part"
            echo "retrieval-up: sha256 mismatch for $1 (expected $3, got $got); nothing installed" >&2
            return 1
        fi
        mv ".models/$1.part" ".models/$1"
        echo "retrieval-up: $1 downloaded, sha256 verified"
    }
    mkdir -p .models
    fetch "{{embed_model}}" "{{embed_url}}" "{{embed_sha256}}"
    fetch "{{rerank_model}}" "{{rerank_url}}" "{{rerank_sha256}}"
    echo "retrieval-up: running memory guard on embedding model"
    python3 scripts/model_mem_guard.py ".models/{{embed_model}}" || { echo "retrieval-up: memory guard failed for embedding model" >&2; exit 1; }
    echo "retrieval-up: running memory guard on reranker model"
    python3 scripts/model_mem_guard.py ".models/{{rerank_model}}" || { echo "retrieval-up: memory guard failed for reranker model" >&2; exit 1; }
    echo "retrieval-up: starting embedding server (wattson-embed) on 127.0.0.1:8081"
    podman rm -f wattson-embed >/dev/null 2>&1 || true
    podman run -d --name wattson-embed --pull=missing --restart=on-failure:3 --read-only --cap-drop=all --security-opt=no-new-privileges --memory=2g --memory-swap=2g --cpus=1 --pids-limit=128 -p 127.0.0.1:8081:8080 -v "$PWD/.models:/models:ro,Z" {{llama_image}} -m /models/{{embed_model}} --embeddings --pooling mean -c 2048 --host 0.0.0.0 --port 8080 >/dev/null
    echo "retrieval-up: starting reranker server (wattson-rerank) on 127.0.0.1:8082"
    podman rm -f wattson-rerank >/dev/null 2>&1 || true
    podman run -d --name wattson-rerank --pull=missing --restart=on-failure:3 --read-only --cap-drop=all --security-opt=no-new-privileges --memory=2g --memory-swap=2g --cpus=1 --pids-limit=128 -p 127.0.0.1:8082:8080 -v "$PWD/.models:/models:ro,Z" {{llama_image}} -m /models/{{rerank_model}} --reranking --pooling rank -c 2048 --host 0.0.0.0 --port 8080 >/dev/null
    echo "retrieval-up: waiting for servers to become healthy"
    for i in $(seq 1 40); do
        e=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://127.0.0.1:8081/health 2>/dev/null || echo 000)
        r=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://127.0.0.1:8082/health 2>/dev/null || echo 000)
        if [ "$e" = "200" ] && [ "$r" = "200" ]; then
            echo "retrieval-up: both servers healthy"
            exit 0
        fi
        sleep 1
    done
    echo "retrieval-up: servers not healthy after 40s; check just retrieval-status" >&2
    exit 1

# --- auto-ontology: NVIDIA's SQL-generation backend + ingestion service --------------------------------
# Pinned upstream: github.com/NVIDIA/auto-ontology @ 2a24f5d34ef6b6e168a7f22d308504d3ab36a198 (Apache-2.0).
# Build takes ~10 min (942 MB image). Requires `just retrieval-up` running first (ports 8081/8082).
# The backend listens on 127.0.0.1:13001, the ingestion service on 127.0.0.1:13002.

ao_sha := "2a24f5d34ef6b6e168a7f22d308504d3ab36a198"

# Clone upstream into .tools/auto-ontology and check out exactly the pinned commit (idempotent).
ao-fetch:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "ao-fetch: fetching NVIDIA/auto-ontology @ {{ao_sha}}"
    if [ -d .tools/auto-ontology ]; then
        cd .tools/auto-ontology
        current=$(git rev-parse HEAD)
        if [ "$current" = "{{ao_sha}}" ]; then
            echo "ao-fetch: already at pinned commit"
            exit 0
        fi
        git fetch origin {{ao_sha}} --depth=1 2>/dev/null || true
        git checkout {{ao_sha}}
    else
        git clone https://github.com/NVIDIA/auto-ontology.git .tools/auto-ontology
        (cd .tools/auto-ontology && git checkout {{ao_sha}} && git reset --hard {{ao_sha}})
    fi
    cd .tools/auto-ontology
    actual=$(git rev-parse HEAD)
    if [ "$actual" != "{{ao_sha}}" ]; then
        echo "ao-fetch: ERROR: checked out $actual, expected {{ao_sha}}" >&2
        exit 1
    fi
    echo "ao-fetch: verified at $actual"

# Build the auto-ontology image (skips if already built).
ao-build:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "ao-build: building localhost/auto-ontology:{{ao_sha}}"
    if podman image inspect localhost/auto-ontology:{{ao_sha}} >/dev/null 2>&1; then
        echo "ao-build: image already exists, skipping"
        exit 0
    fi
    podman build --memory=8g -t localhost/auto-ontology:{{ao_sha}} -f .tools/auto-ontology/Dockerfile .tools/auto-ontology
    echo "ao-build: done"

# Generate secrets (idempotent) and render the pod spec.
ao-secrets:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "ao-secrets: generating secrets into .secrets/ao.env"
    mkdir -p .secrets
    if [ -f .secrets/ao.env ]; then
        echo "ao-secrets: .secrets/ao.env already exists, reusing"
    else
        python3 -c 'import secrets; open(".secrets/ao.env","w").write(f"AO_PG_PASSWORD={secrets.token_urlsafe(24)}\nAO_AUTH_SECRET={secrets.token_urlsafe(32)}\nAO_ADMIN_PASSWORD={secrets.token_urlsafe(16)}\n")'
        chmod 0600 .secrets/ao.env
        echo "ao-secrets: generated"
    fi

# Start the auto-ontology pod (requires retrieval servers on 8081/8082).
ao-up: ao-fetch ao-build ao-secrets
    #!/usr/bin/env bash
    set -euo pipefail
    embed_port="${WATTSON_EMBED_PORT:-8081}"
    echo "ao-up: checking the embedding server on $embed_port and the reranker on 8082"
    for port in "$embed_port" 8082; do
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:$port/health 2>/dev/null || echo 000)
        if [ "$code" != "200" ]; then
            echo "ao-up: retrieval server on port $port not answering (got $code). Run 'just retrieval-up' first." >&2
            exit 1
        fi
    done
    echo "ao-up: retrieval servers healthy"
    echo "ao-up: rendering pod spec"
    source .secrets/ao.env
    mkdir -p out
    sed -e "s|@AO_SHA@|{{ao_sha}}|g" \
        -e "s|@EMBED_PORT@|$embed_port|g" -e "s|@EMBED_MODEL@|${WATTSON_EMBED_MODEL:-nomic-embed-text-v1.5}|g" -e "s|@CHAT_MODEL@|${WATTSON_CHAT_MODEL:-qwen3.8-flash-next}|g" \
        -e "s|@PG_PASSWORD@|$AO_PG_PASSWORD|g" \
        -e "s|@AUTH_SECRET@|$AO_AUTH_SECRET|g" \
        -e "s|@ADMIN_PASSWORD@|$AO_ADMIN_PASSWORD|g" \
        kube/auto-ontology.yaml > out/auto-ontology.rendered.yaml
    echo "ao-up: starting pod"
    podman kube play --replace --network=pasta:--map-host-loopback,169.254.1.2 out/auto-ontology.rendered.yaml
    echo "ao-up: waiting for health endpoints (up to 120s)"
    for i in $(seq 1 24); do
        backend=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:13001/api/health 2>/dev/null || echo 000)
        ingestion=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:13002/health 2>/dev/null || echo 000)
        if [ "$backend" = "200" ] && [ "$ingestion" = "200" ]; then
            echo "ao-up: both endpoints healthy after $((i * 5))s"
            exit 0
        fi
        sleep 5
    done
    echo "ao-up: endpoints not healthy after 120s; check pod logs with 'just ao-logs <container>'" >&2
    exit 1

# Smoke test: both health endpoints must return 200.
ao-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "ao-smoke: testing backend health"
    backend=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:13001/api/health 2>/dev/null || echo 000)
    if [ "$backend" = "200" ]; then
        echo "ao-smoke: backend health: PASS (200)"
    else
        echo "ao-smoke: backend health: FAIL ($backend)"
        exit 1
    fi
    echo "ao-smoke: testing ingestion health"
    ingestion=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:13002/health 2>/dev/null || echo 000)
    if [ "$ingestion" = "200" ]; then
        echo "ao-smoke: ingestion health: PASS (200)"
    else
        echo "ao-smoke: ingestion health: FAIL ($ingestion)"
        exit 1
    fi
    echo "ao-smoke: all checks passed"

# Auto Ontology answers a question over the demo database (#20): compiles the semantic layer, asks, checks the returned count. Pod must be up (ao-up) and seeded (ao-fixture).
# Needs a chat model: WATTSON_CHAT_MODEL=<llama-swap model> when the pod was started (a small one such as granite-4.2-8b avoids a 100 GB load).
ao-ask-check:
    @python3 scripts/ao_ask_check.py

# Show pod and container state.
ao-status:
    @echo "ao-status: pod and container state"
    @podman ps -a --filter pod=auto-ontology --format "table {{{{.Names}}}}\t{{{{.Status}}}}\t{{{{.Ports}}}}" || true
    @curl -sS -o /dev/null -w 'backend /api/health -> http %{http_code}\n' --max-time 3 http://127.0.0.1:13001/api/health || true
    @curl -sS -o /dev/null -w 'ingestion /health -> http %{http_code}\n' --max-time 3 http://127.0.0.1:13002/health || true

# Follow a container's log: postgres | backend | ingestion (the migration runs inside the backend container)
ao-logs service="backend":
    podman logs --follow "auto-ontology-$1"

# Seed the demo fixture database: create demo, orders table, and 5 rows (idempotent).
ao-fixture:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "ao-fixture: seeding demo database"
    podman exec auto-ontology-postgres psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE demo" 2>/dev/null || true
    podman exec auto-ontology-postgres psql -U postgres -d demo -v ON_ERROR_STOP=1 -c "CREATE TABLE IF NOT EXISTS orders (id int PRIMARY KEY, customer text NOT NULL, amount numeric(12,2) NOT NULL, shipped date)"
    podman exec auto-ontology-postgres psql -U postgres -d demo -v ON_ERROR_STOP=1 -c "INSERT INTO orders (id, customer, amount, shipped) VALUES (1,'acme',100.00,'2025-01-10'),(2,'globex',250.50,'2025-01-12'),(3,'initech',75.25,NULL),(4,'umbrella',500.00,'2025-01-15'),(5,'wayne',125.75,'2025-01-18') ON CONFLICT (id) DO NOTHING"
    count=$(podman exec auto-ontology-postgres psql -U postgres -d demo -t -A -c "SELECT count(*) FROM orders;")
    if [ "$count" = "5" ]; then
        echo "ao-fixture: demo.orders rows=5"
    else
        echo "ao-fixture: ERROR expected 5 rows, got $count" >&2
        exit 1
    fi

# Turn semantic compilation on (#20). It is an opt-in flag in frontend.configurations, a table the Next.js frontend owns; this pod has no
# frontend, so without this row POST /semantic/compile returns 202 and does nothing, and chat answers 409. Idempotent.
ao-enable-semantic:
    #!/usr/bin/env bash
    set -euo pipefail
    podman exec auto-ontology-postgres psql -U postgres -d auto_ontology -v ON_ERROR_STOP=1 -q -c "CREATE SCHEMA IF NOT EXISTS frontend" -c "CREATE TABLE IF NOT EXISTS frontend.configurations (key text PRIMARY KEY, value text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())" -c "INSERT INTO frontend.configurations (key, value) VALUES ('semantic_compilation_enabled', 'true') ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = now()"
    v=$(podman exec auto-ontology-postgres psql -U postgres -d auto_ontology -t -A -c "SELECT value FROM frontend.configurations WHERE key = 'semantic_compilation_enabled'")
    [ "$v" = "true" ] && echo "ao-enable-semantic: semantic_compilation_enabled=true" || { echo "ao-enable-semantic: ERROR flag reads '$v'" >&2; exit 1; }

# Stop and remove the auto-ontology pod (keeps retrieval servers running).
ao-down:
    @echo "ao-down: removing auto-ontology pod"
    @podman pod rm --force auto-ontology >/dev/null 2>&1 || true
    @echo "ao-down: done"

# Stop and remove the embedding and reranker containers.
retrieval-down:
    @echo "retrieval-down: removing wattson-embed and wattson-rerank"
    @podman rm -f wattson-embed wattson-rerank >/dev/null 2>&1 || true

# Show container state and probe both /health endpoints.
retrieval-status:
    @echo "retrieval-status: container state and health probes"
    @podman ps -a --filter name=wattson-embed --filter name=wattson-rerank --format '{{"{{.Names}}  {{.Status}}  {{.Ports}}"}}'
    @curl -sS -o /dev/null -w 'embed /health -> http %{http_code}\n' --max-time 3 http://127.0.0.1:8081/health || true
    @curl -sS -o /dev/null -w 'rerank /health -> http %{http_code}\n' --max-time 3 http://127.0.0.1:8082/health || true

# Log verified evaluation run directories to an MLflow tracking server (loopback by default). e.g. just mlflow-log --tracking-url http://127.0.0.1:5001 --experiment wattson-evals --arm NAME RUN_DIR...
mlflow-log *args:
    python3 scripts/mlflow_log.py "$@"
