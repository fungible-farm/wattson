# sm3lly: model router from existing projects

agentgateway (front door, already packaged in `../agentgateway.yaml`) in front of upstream llama-swap v272 (loads one model at a time on the
single RTX 3090). No custom router code. `llama-swap.yaml` holds the two models (`neo-coder`, `arete-27b`) with the same flags as their
systemd units, but every container is memory-capped and none uses the cap-bypass annotation.

`stage.sh` runs on sm3lly beside the live setup on spare ports (llama-swap 18010, gateway 18011; 8010 belongs to kr0ki-dev-kroki). It stops
neo-coder for the test and always restarts it on exit. Measured on 2026-11-13 (log in issue #131): models listed through the gateway were
`arete-27b` and `neo-coder`; a call for `neo-coder` returned in 11 s, a call for `arete-27b` unloaded it and returned in 38 s with one
resident container, and `neo-coder` again returned in 118 s. The live `:8000` forwarder is not changed by this script.

## Live since 2026-11-13 (cutover and rollback)

`cutover.sh` (run on sm3lly after `stage.sh` has put the binary and `gw/` in `~/llama-swap-stage`) disables the old neo-coder unit and the socat
forwarder, then starts `wattson-llama-swap.service` and `wattson-gateway.service`. The gateway serves `127.0.0.1:8000` and `127.0.0.1:8002`, so
kr0ki's `OPENAI_API_URL` needs no change. It checks the model list and a chat call on both ports and runs `rollback.sh` on any failure.
`rollback.sh` removes the new units and containers and re-enables `b00t-hive-inference-heretic-neo-coder.service` and `kr0ki-llm-socat.service`.

Limits: only one model is resident, so a request for the other model unloads the first (kr0ki loses its model for the length of a swap, about 1 to
2 minutes). Anything that takes the GPU for a long job must stop both new units first and restore them afterwards. Not verified: persistence
across a reboot, and kr0ki vision calls through the gateway (only its health endpoint was checked).

Correction (2026-11-13): the first cutover bound `:8002` to loopback, but the old neo-coder unit published it on all interfaces, so LAN clients
(`scripts/pi_delegate.py` and the Demo 3 recipe use `192.168.1.137:8002`) got connection refused. The gateway now publishes `:8002` on all
interfaces again (verified HTTP 200 from another host); `:8000` stays loopback-only. `cutover.sh` did not check LAN reachability.
