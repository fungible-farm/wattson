# Serving a model

`just up granite` (or `qwen`) starts upstream vLLM in its own container with these flags. They are the flags the students were
evaluated with, so a served model behaves as the published results describe.

| Flag | Value | Why |
|---|---|---|
| `--dtype` | `bfloat16` | the dtype of the released weights |
| `--max-model-len` | `32768` | prompt plus completion; the longest evaluation prompt is about 7.6k tokens and replies can run to 8k |
| `--enforce-eager` | on | the setting the evaluations used |
| `--enable-auto-tool-choice`, `--tool-call-parser` | `qwen3_coder` | both students emit tool calls in that format |
| `--reasoning-parser` | `qwen3` (Qwen), `nemotron_v3` (Granite) | separates reasoning from the answer |
| `--generation-config` | `vllm` | do not take sampling defaults from the model repository |
| `--served-model-name` | `wattson` | what the harness asks for |
| `--max-num-seqs` | `8` | concurrency; the evaluator runs 4 at a time |

Temperature is 0 in every evaluation.

## Tuning for your GPU

| Variable | Default | Meaning |
|---|---|---|
| `WATTSON_GPU_UTIL` | `0.85` | fraction of GPU memory vLLM reserves |
| `WATTSON_MAX_MODEL_LEN` | `32768` | lower it if the server runs out of KV cache |
| `WATTSON_PORT` | `8000` | the port on **your** machine; inside the pod the server is always on 8000 |
| `WATTSON_VLLM_IMAGE` | `docker.io/vllm/vllm-openai:v0.31.0` | upstream is published for amd64 and arm64 |

On a unified-memory machine (the NVIDIA GB10) the GPU shares RAM with the rest of the system, so reserve little: we use `0.15` for Qwen and
`0.26` for Granite. Measured there: Qwen was healthy about 2 minutes after the pod started, once the model was downloaded.

## Talking to it

The server is on `127.0.0.1:<WATTSON_PORT>` and listens on loopback only. It speaks the OpenAI chat-completions API at `/v1`, and any
OpenAI-compatible client can use it. Use the harness (`just ask`, `just eval`) rather than a bare chat client: the students were
trained with the harness's tool definitions and system prompt, and measured results hold only for that. For the same reason see
[pi](pi.md).

## Without the pod

You can start vLLM yourself with the flags above against the weights in the `wattson-models` volume
(`podman volume inspect wattson-models`), or against a directory made by `wattson models pull REPO --dest DIR`.
