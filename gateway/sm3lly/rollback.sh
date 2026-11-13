#!/usr/bin/env bash
# Runs ON sm3lly. Back to the plain setup: neo-coder on :8002 and the socat forwarder on :8000.
U=$HOME/.config/systemd/user
systemctl --user disable --now wattson-gateway.service wattson-llama-swap.service 2>/dev/null
podman rm -f swap-neo-coder swap-arete wattson-gateway >/dev/null 2>&1
rm -f $U/wattson-llama-swap.service $U/wattson-gateway.service; systemctl --user daemon-reload
systemctl --user enable --now b00t-hive-inference-heretic-neo-coder.service kr0ki-llm-socat.service
for i in $(seq 1 90); do curl -fs http://127.0.0.1:8002/health >/dev/null && break; sleep 5; done
echo "rollback: neo-coder=$(systemctl --user is-active b00t-hive-inference-heretic-neo-coder.service) health=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8002/health) socat=$(systemctl --user is-active kr0ki-llm-socat.service)"
