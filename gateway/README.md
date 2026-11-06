# Model gateway

One local door for model calls. [agentgateway](https://github.com/agentgateway/agentgateway) (Apache-2.0, pinned to v1.7.0 by
image digest, so the bytes are reproducible; the justfile keeps the tag only for reading)
listens on `127.0.0.1:4000`, speaks the OpenAI API, and forwards to llama-swap on `127.0.0.1:8000`.
Clients point at `http://127.0.0.1:4000/v1` instead of llama-swap; model names pass through unchanged.
No code here: just `agentgateway.yaml`.

## Start and stop

    just gateway-up        # start (Podman, read-only, 512 MB, 1 CPU, loopback only)
    just gateway-status    # container state and a probe of /v1/models
    just gateway-logs      # the request log
    just gateway-down      # stop and remove

## Restart behaviour

- A crash of the gateway process is recovered: the container runs with `--restart=on-failure:3` (3 retries).
- Stopped by hand stays stopped: `just gateway-down`, `podman stop`, `podman kill` bypass the policy.
- A host reboot does NOT bring back a hand-started gateway. For that, `gateway/wattson-gateway.container` is a Quadlet for your user systemd (`just gateway-quadlet` prints it with the install commands; `just gateway-quadlet-check` verifies it generates a service equal to `gateway-up` without installing anything). Installing it is a host change and is yours to run; surviving an actual reboot has not been observed.

## Add a backend or a model rule

Edit `gateway/agentgateway.yaml`. The gateway watches the file and reloads it in place; no restart.
Under `llm.models`, add an entry with a `name` and a `provider`; the first match wins, so put specific
names above `'*'`. Example: another OpenAI-compatible server is `provider: openAI` with
`params.baseUrl: http://HOST:PORT/v1`; an alias is a `name` plus `params.model` for the real name.
A keyed backend uses `params.apiKey: {file: /path}`, never a literal key in git.
The directory is mounted, not the single file, because editors that replace the file (rename) would
otherwise leave the container watching the old copy.

## Why the odd network flag

llama-swap listens on host loopback only, which a container cannot normally reach. `gateway-up` uses
Podman's pasta network with `--map-host-loopback 169.254.1.2`, so the container sees host
`127.0.0.1` as `169.254.1.2` and nothing else on the host. (`--network=host` would work but exposes
every host loopback service to the gateway.)

## Where the request log goes

Container stdout: one `request` line per call, read with `just gateway-logs` (or `podman logs wattson-gateway`).
It has the model asked for, the upstream endpoint, HTTP status, duration, and for chat calls the input and
output token counts. Errors are logged at `error` level with a `reason` (for example `UpstreamFailure`).
It has no request or response hashes, no role, no real caller identity (the client address is the
host's LAN address, not a user or agent), and no stable error field beyond `error=` text.
Nothing is stored beyond the container's log; add `config.logging.database` (SQLite or Postgres) when
a durable record is wanted.

Harmless start-up warning: the config is read-only so agentgateway cannot write a generated MCP session
key (MCP is not used here).

## Embedding and rerank routes
`POST /v1/embeddings` goes to the embedding server (127.0.0.1:8081) and `POST /v1/rerank` to the reranker (127.0.0.1:8082), both started by
`just retrieval-up` (see `docs/diagrams/retrieval-lifecycle.svg`). Chat requests still go to llama-swap. These are plain routes: the gateway
logs them but does not change them.
