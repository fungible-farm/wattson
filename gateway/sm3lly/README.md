# sm3lly: model router from existing projects

agentgateway (front door, already packaged in `../agentgateway.yaml`) in front of upstream llama-swap v272 (loads one model at a time on the
single RTX 3090). No custom router code. `llama-swap.yaml` holds the two models (`neo-coder`, `arete-27b`) with the same flags as their
systemd units, but every container is memory-capped and none uses the cap-bypass annotation.

`stage.sh` runs on sm3lly beside the live setup on spare ports (llama-swap 18010, gateway 18011; 8010 belongs to kr0ki-dev-kroki). It stops
neo-coder for the test and always restarts it on exit. Measured on 2026-11-13 (log in issue #131): models listed through the gateway were
`arete-27b` and `neo-coder`; a call for `neo-coder` returned in 11 s, a call for `arete-27b` unloaded it and returned in 38 s with one
resident container, and `neo-coder` again returned in 118 s. The live `:8000` forwarder is not changed by this script.
