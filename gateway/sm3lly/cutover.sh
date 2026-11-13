#!/usr/bin/env bash
# Runs ON sm3lly (ssh sm3lly bash -s < cutover.sh, repo copied to ~/wattson-sync after stage.sh has put the binary and gw/ in ~/llama-swap-stage).
# Replaces neo-coder-on-:8002 + socat-on-:8000 with llama-swap + agentgateway serving BOTH ports. Any failed check runs rollback.sh.
set -u
R=$HOME/wattson-sync/gateway/sm3lly; U=$HOME/.config/systemd/user; S=$HOME/llama-swap-stage
fail() { echo "FAIL $1"; bash "$R/rollback.sh"; echo "CUTOVER=FAILED"; exit 1; }
for f in llama-swap llama-swap.yaml gw/agentgateway.yaml; do [ -e "$S/$f" ] || fail "missing $S/$f (run stage.sh first)"; done
cp "$R/llama-swap.yaml" "$S/llama-swap.yaml"
systemctl --user disable --now kr0ki-llm-socat.service b00t-hive-inference-heretic-neo-coder.service || fail "stop old units"
for i in $(seq 1 40); do [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)" -lt 2000 ] && break; sleep 5; done
cp "$R/systemd/wattson-llama-swap.service" "$R/systemd/wattson-gateway.service" "$U/" && systemctl --user daemon-reload
systemctl --user enable --now wattson-llama-swap.service wattson-gateway.service || fail "start new units"
for i in $(seq 1 30); do curl -fs -o /dev/null http://127.0.0.1:8000/v1/models && break; sleep 1; done
for p in 8000 8002; do
  m=$(curl -s http://127.0.0.1:$p/v1/models | python3 -c 'import sys,json;print(sorted(x["id"] for x in json.load(sys.stdin)["data"]))' 2>&1)
  echo "port $p models: $m"; echo "$m" | grep -q neo-coder || fail "models on :$p"
done
for p in 8000 8002; do
  code=$(curl -s -o /tmp/cut-resp.json -w %{http_code} --max-time 900 http://127.0.0.1:$p/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"neo-coder","max_tokens":24,"messages":[{"role":"user","content":"Reply with the single word: ready"}]}')
  echo "chat via :$p http=$code served_by=$(python3 -c 'import json;d=json.load(open("/tmp/cut-resp.json"));print(d.get("model"),"|",(d["choices"][0]["message"].get("content") or "")[:30])' 2>&1 | tail -1)"; [ "$code" = 200 ] || fail "chat on :$p"
done
echo "kr0ki agent health: $(curl -s --max-time 30 -o /dev/null -w %{http_code} http://127.0.0.1:8789/health)"
echo "units: swap=$(systemctl --user is-active wattson-llama-swap.service) gateway=$(systemctl --user is-active wattson-gateway.service) enabled=$(systemctl --user is-enabled wattson-gateway.service)"
echo "CUTOVER=OK"
