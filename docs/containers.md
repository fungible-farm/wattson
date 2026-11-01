# Containers

## Layers

`containers/Containerfile` builds in stages so a code change does not re-download the numerical stack:

| Stage | Contents | Changes when |
|---|---|---|
| `base` | Python 3.14, OS libraries, `uv`, an unprivileged user | rarely |
| `deps` | the locked environment from `uv.lock`, including the labs and Hugging Face extras | dependencies change |
| `wattson` | the package, labs, scripts and `data/` | every commit |
| `harness` | `wattson` plus Node, the `pi` agent and `just` | the interactive image |

The published images are `ghcr.io/fungible-farm/wattson` (the `wattson` stage) and `ghcr.io/fungible-farm/wattson-harness`.
Both are multi-architecture (amd64 and arm64), each built natively on its own runner.

## Where the model weights are

**Not in the images.** Hugging Face does not serve models through an OCI registry, so `podman pull` cannot fetch them. Two other routes
exist: put the weights into image layers on a registry, or download them into a volume. This project uses the volume:

- the weights are 8.5 GB and 17 GB, the larger one near registry per-layer limits, and a copy on GHCR would be a second source of truth;
- the Hugging Face repository carries a `hash_manifest.json`, so the download can be verified file by file;
- a volume survives pod restarts, so the download happens once.

The pod's init container runs `wattson models pull`, which downloads into the `wattson-models` volume and verifies every hash. A second
run finds the marker file, re-verifies, and downloads nothing. The server container mounts the same volume read-only and stays up.

## The pod

`kube/wattson.yaml` is played by `podman kube play` (the command is `play`, not `start`). It contains:

| | |
|---|---|
| init `pull-model` | downloads and verifies the student |
| init `fetch-cases` | downloads the CSIRO case files into the `wattson-cases` volume, checksum-verified |
| `vllm` | upstream `vllm/vllm-openai`, its own container, the GPU attached by CDI |
| `harness` | the tools, `pi` and `just`; shares the pod's network, so the server is at `127.0.0.1:8000` |

Your current directory is mounted at `/work` in the harness, so logs, journals and evaluation output appear next to where you ran
`just up`. The pod maps your user to the image's user (`keep-id`), so those files belong to you.

## Build and test locally

```bash
just build                    # both images from this checkout
scripts/test_pod.sh           # the pod without its GPU parts, checked from outside as a user would
```

The same steps run in CI (`.github/workflows/container-smoke.yml`) and can be run on a developer machine with
`wrkflw run --runtime emulation .github/workflows/container-smoke.yml`.

## Publishing

`.github/workflows/containers.yml` runs on pushes to `main` that touch the container inputs. It runs the smoke workflow first, builds each
image on a native runner per architecture, pushes by digest, and joins the digests into one multi-architecture manifest tagged
`latest` and `sha-<short commit>`. It needs only the repository's own `GITHUB_TOKEN`.

## Maintainers: after the first publish

GitHub creates a new container package **private**, even from a public repository, and there is no API to change that. Once, in the
GitHub UI, for each of `wattson` and `wattson-harness`: Package settings, then Danger Zone, then Change visibility, then Public. Check with an
unauthenticated request:

```bash
tok=$(curl -s "https://ghcr.io/token?scope=repository:fungible-farm/wattson:pull" | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $tok" -H "Accept: application/vnd.oci.image.index.v1+json" \
  https://ghcr.io/v2/fungible-farm/wattson/manifests/latest      # 200 when public, 403 while private
```

The publish job fails unless the manifest lists both `linux/amd64` and `linux/arm64` (`scripts/check_platforms.py`). That guard exists
because a matrix mistake once published an arm64-only image under a green run.
