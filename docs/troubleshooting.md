# Troubleshooting

`just doctor` (with a model) and `just check` (without) report what is wrong in plain terms. Every entry below is a failure that
actually occurred while building this; each now has a test.

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to infer device type` from vLLM | the GPU was not attached to the container | the pod must request `nvidia.com/gpu=all`; plain `nvidia.com/gpu` silently attaches nothing under `podman kube play`. Check `nvidia-ctk cdi list` shows it |
| `rootlessport listen tcp 127.0.0.1:8000: bind: address already in use` | something on your machine uses port 8000 | `WATTSON_PORT=8100 just up granite` |
| `PermissionError: ... '/work/out'` | rootless Podman maps your user to root, and the image's user could not write your directory | the pod sets `keep-id` so this works; if you run the image yourself add `--userns=keep-id:uid=1000,gid=1000` |
| `Case file ... snemSA.m not found` | the tool server could not see the case files | `wattson fetch-data`; the MCP library does not pass your environment to the tool server, so `WATTSON_*` variables are forwarded explicitly |
| `404 ... /v1/v1/chat/completions` | a base URL ending in `/v1` was passed to a client that adds it | the CLI now accepts either form |
| `wattson: command not found` in a `bash -l` shell | a login shell resets `PATH` | `wattson` is linked into `/usr/local/bin` in the image |
| the model answers a number that fails the check, or runs `npx ...` | `pi` with its default tools | use `just pi` ([why](pi.md)) |
| `volume with name wattson-cases already exists` | a leftover from an earlier run | `podman volume rm wattson-cases` |
| the model download is slow, with a warning about unauthenticated requests | anonymous Hugging Face downloads get lower rate limits | pass an `HF_TOKEN` to the pull-model container |
| first completion takes 30 s | the first request warms the server | not a fault |
| labs 2 and 3 skip two tests on arm64 | `pypowsybl` ships no aarch64 wheel | expected; x86_64 runs them |

## Logs

Every component logs one JSON object per line:

```bash
just logs vllm                                  # the model server
just logs pull-model                            # the download
jq 'select(.level=="ERROR")' out/wattson.log    # the harness log written to your directory
WATTSON_LOG_LEVEL=DEBUG just up granite         # more detail, including per-file hash timings
```
