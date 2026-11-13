#!/usr/bin/env bash
# Stage llama-swap + agentgateway on sm3lly beside the live setup, test both models through the gateway, restore neo-coder.
# Runs ON sm3lly:  ssh sm3lly bash -s < gateway/sm3lly/stage.sh   (run from the repo root with the repo copied to ~/wattson-sync)
# Spare ports only (8010 is taken by kr0ki-dev-kroki): llama-swap 127.0.0.1:18010, gateway 127.0.0.1:18011. Live :8000 (socat) and :8002 are not touched, except that the
# neo-coder unit is stopped for the test (one 24 GB GPU) and ALWAYS restarted on exit. Rollback needs nothing else.
set -u
NEO=b00t-hive-inference-heretic-neo-coder.service
S=$HOME/llama-swap-stage; REPO=$HOME/wattson-sync
SWAP_VER=272; GW_IMAGE='ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b'
mkdir -p "$S/gw"
for p in 18010 18011; do ss -ltn "( sport = :$p )" | grep -q LISTEN && { echo "FAIL port $p in use"; exit 1; }; done
gpu_free() { for i in $(seq 1 40); do [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)" -lt 2000 ] && return 0; sleep 5; done; return 1; }
restore() {
  podman rm -f wattson-gateway-stage >/dev/null 2>&1
  systemctl --user stop wattson-llama-swap-stage.service 2>/dev/null
  podman rm -f swap-neo-coder swap-arete >/dev/null 2>&1
  gpu_free; systemctl --user start $NEO
  for i in $(seq 1 90); do curl -fs http://127.0.0.1:8002/health >/dev/null && break; sleep 5; done
  echo "restore: neo-coder unit=$(systemctl --user is-active $NEO) health=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8002/health) socat=$(systemctl --user is-active kr0ki-llm-socat.service)"
}
trap restore EXIT
# 1. llama-swap release binary, checksum-verified, fetched inside a capped container (nothing is fetched by the host).
if [ ! -x "$S/llama-swap" ]; then
  podman run --rm --user 0 --memory 256m --memory-swap 256m -v "$S:/out:z" --entrypoint sh docker.io/curlimages/curl:8.11.1 -c "
    cd /out && curl -fsSLO https://github.com/mostlygeek/llama-swap/releases/download/v$SWAP_VER/llama-swap_${SWAP_VER}_linux_amd64.tar.gz &&
    curl -fsSLO https://github.com/mostlygeek/llama-swap/releases/download/v$SWAP_VER/llama-swap_${SWAP_VER}_checksums.txt" || { echo "FAIL download"; exit 1; }
  (cd "$S" && grep "linux_amd64.tar.gz" llama-swap_${SWAP_VER}_checksums.txt | sha256sum -c - && tar xzf llama-swap_${SWAP_VER}_linux_amd64.tar.gz llama-swap) || { echo "FAIL checksum"; exit 1; }
fi
cp "$REPO/gateway/sm3lly/llama-swap.yaml" "$S/llama-swap.yaml"
sed 's#169.254.1.2:8000#169.254.1.2:18010#g' "$REPO/gateway/agentgateway.yaml" > "$S/gw/agentgateway.yaml"
echo "llama-swap $("$S/llama-swap" --version 2>&1 | head -1)"
# 2. free the GPU, start llama-swap (its own transient unit) and the gateway
systemctl --user stop $NEO || { echo "FAIL cannot stop neo-coder"; exit 1; }
gpu_free || { echo "FAIL gpu not free"; exit 1; }
systemd-run --user --unit=wattson-llama-swap-stage --collect "$S/llama-swap" --config "$S/llama-swap.yaml" --listen 127.0.0.1:18010 >/dev/null
podman run -d --rm --name wattson-gateway-stage --read-only --cap-drop=all --security-opt=no-new-privileges --memory=512m --memory-swap=512m --cpus=1 --pids-limit=128 \
  --network=pasta:--map-host-loopback,169.254.1.2 -p 127.0.0.1:18011:4000 -v "$S/gw:/etc/agentgateway:ro,Z" "$GW_IMAGE" -f /etc/agentgateway/agentgateway.yaml >/dev/null
for i in $(seq 1 30); do curl -fs -o /dev/null http://127.0.0.1:18011/v1/models && break; sleep 1; done
echo "models via gateway: $(curl -s http://127.0.0.1:18011/v1/models | python3 -c 'import sys,json;print(sorted(m["id"] for m in json.load(sys.stdin)["data"]))' 2>&1)"
# 3. through the gateway: neo-coder, then arete-27b (neo-coder must unload), then neo-coder again
ask() { # $1 model
  local t0=$(date +%s)
  code=$(curl -s -o /tmp/swap-resp.json -w %{http_code} --max-time 900 http://127.0.0.1:18011/v1/chat/completions -H 'Content-Type: application/json' \
    -d "{\"model\":\"$1\",\"max_tokens\":24,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: ready\"}]}")
  echo "ask $1: http=$code secs=$(( $(date +%s)-t0 )) served_by=$(python3 -c 'import json;d=json.load(open("/tmp/swap-resp.json"));print(d.get("model"),"|",(d["choices"][0]["message"].get("content") or "")[:40].replace("\n"," "))' 2>&1)"
  echo "  resident containers: $(podman ps --format '{{.Names}}' | grep '^swap-' | tr '\n' ' ') gpu_mib=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)"
}
ask neo-coder; ask arete-27b; ask neo-coder
echo "DONE"
