# Install

## What you need

| For | You need |
|---|---|
| labs 1-3 (`just labs`) | Linux, [Podman](https://podman.io) 5 or newer, [`just`](https://just.systems). No GPU. |
| a model (`just up ...`) | the above, plus an NVIDIA GPU, its driver, the NVIDIA Container Toolkit, and a CDI specification (below) |

Tested: Podman 6.1.3 on aarch64 (NVIDIA GB10) and Podman 5.7.0 on x86_64, `just` 1.58, rootless. **Not tested:** macOS, Windows,
Docker (the commands are Podman's), rootful Podman, AMD or Intel GPUs. Expect to adapt anything outside that list.

## Podman and just

Use your distribution's package manager for Podman. `just` is a single binary; see its
[installation page](https://just.systems/man/en/packages.html).

## GPU: one-time setup

The model server runs in its own container and is given the GPU through a CDI device. The pod asks for `nvidia.com/gpu=all`, so that
name has to exist:

```bash
nvidia-smi                                   # the driver works
nvidia-ctk --version                         # the NVIDIA Container Toolkit is installed
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list                          # must list nvidia.com/gpu=all
podman run --rm --device nvidia.com/gpu=all docker.io/library/ubuntu:24.04 nvidia-smi
```

If the last command prints your GPU, the model server will see it. If it fails, nothing in this repository can fix that.

## How much GPU memory

| Model | Weights | Notes |
|---|---|---|
| `qwen` (Qwen3.5-4B, thinking student) | 8.5 GB | verified running on an NVIDIA GB10 |
| `granite` (Granite 4.2 8B student) | 17 GB | needs a GPU with comfortably more than 17 GB free |

`WATTSON_GPU_UTIL` sets the fraction of GPU memory the server reserves (default `0.85`). On a unified-memory machine such as the
GB10 the GPU shares RAM with everything else, so use a small value (we use `0.15` for Qwen and `0.26` for Granite). See [serving](serving.md).

## Check it

```bash
just check           # the container environment: python, pinned packages, data hashes, solver, tool server
just labs            # labs 1-3 against their committed expected results
```

`just check` fetches nothing and needs no GPU. `just labs` downloads the two CSIRO case files (about 1 MB) into a volume the first time.
