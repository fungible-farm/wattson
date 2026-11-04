# Hugging Face tools and portable model artifacts

Model weights are independent of CPU architecture. A serving executable and its native
libraries depend on the host architecture. Package these independently: replace or
rebuild the server without downloading another copy of its model. No emulator is
needed to download, hash, archive, or mount weights. Emulation is relevant only when
executing a binary built for a different CPU.

The pinned inputs in [models.json](models.json) are:

| Alias | Repository | Immutable revision | Full snapshot bytes |
|---|---|---|---:|
| qwen | [PromptExecutionAU/wattson-qwen3.5-4b-pyt](https://huggingface.co/PromptExecutionAU/wattson-qwen3.5-4b-pyt) | d1826df681c97602c2c93ebfdafdfba03a2d1729 | 9,388,515,622 |
| granite | [PromptExecutionAU/wattson-granite-4.2-8b-py](https://huggingface.co/PromptExecutionAU/wattson-granite-4.2-8b-py) | dd8af31440cd01c69c4fde173327b326810bec8a | 17,996,010,795 |

Both model cards declare Apache-2.0. The snapshots include merged weights, tokenizers,
templates, provenance manifests, and the original LoRA adapter subdirectories. The
adapter weights are retained for provenance; do not apply them again to the already
merged root weights. These models were trained for Wattson's `run_python` workflow;
that training does not establish that an unrelated GenCost tool will work unchanged.

Two software images provide `hf` (huggingface_hub 2.4.0, Apache-2.0, uv.lock) and the
official HF MCP npm package (@llmindset/hf-mcp-server 0.4.32, MIT, package-lock.json).
The published MCP image advertised only amd64 when inspected; our Containerfile
builds that same package natively. This limitation concerns the software image, not
the model weights. The MCP profile disables the default remote image-generation
tool and exposes Hub discovery. HF MCP needs Hub access; bundling it does not create
an offline mirror of huggingface.co.

```sh
# Run in this local-runtime directory on the intended container host.
# No host Python, Node, hf, or MCP server installation is needed.
sh huggingface/environment.sh build

# Use the official MCP server to read both actual model cards; retain evidence.
sh huggingface/environment.sh probe

# Download through the official hf CLI at the committed revisions.
# Each download resumes through HF's local-dir cache after interruption.
sh huggingface/environment.sh model qwen
sh huggingface/environment.sh model granite

# Each model becomes a separate scratch OCI image containing /models.
# Verify those image mounts with networking disabled.
sh huggingface/environment.sh verify qwen
sh huggingface/environment.sh verify granite

# Inspect runtime-managed image sizes; shared and physical usage need separate accounting.
podman images --filter label=ai.wattson.role=model-data
podman system df -v

# After the complete environment is prepared, export software AND model OCI archives,
# image inspections, source, checksums and local Syft CycloneDX SBOMs.
just package
```

The model images have no executable or base OS. Podman mounts their contents using
`--mount type=image,source=MODEL_IMAGE,destination=/model-image,rw=false`; inference
reads `/model-image/models`. A mount shares the managed image contents and does not
copy weights into each serving image. OCI image platform metadata must be suitable
for the target runtime even though the contained weight bytes are portable. Build
the scratch image on the intended host; doing so never recompiles the weights.

Downloading initially stages a repository under `.wattson/huggingface/`, then copies
it into the image store. Offline export adds another archive copy. These consume
disk separately: image size is not a disk quota, and an OCI mount is not automatically
a CSI volume. The generated model lock records logical bytes and SHA-256 for every
file, checked against Hub LFS hashes where provided. Package manifests also capture
image IDs and archive hashes. A hash records identity, not publisher trust.

On Kubernetes, [image volumes](https://kubernetes.io/docs/tasks/configure-pod-container/image-volumes/)
can mount OCI content read-only when the cluster and CRI runtime support them. A PVC
is a storage claim, and a CSI driver supplies storage; neither automatically mounts
an arbitrary OCI image. For clusters without image-volume support, provision a PVC
and populate it once from the approved model artifact, accounting for that extra
copy. k0s distribution, Kubernetes version, runtime support and OpenShift admission
policy must be checked on the actual cluster. k9s is an inspection client.

Current verification: CLI and MCP images build natively on the ARM64 preparation
host and x86-64 sm3lly; actual MCP initialization, tool listing, and reading both
model cards through `hf_fs` passed on both. Repository identities, architectures,
licenses and logical sizes were read from the Hub. Complete model images,
inference, package generation and offline restoration remain unverified.
`fung1` rejected the available SSH key, so preparation moved to the isolated
`~/wattson-test/local-runtime` directory on `brianh@sm3lly.lan`. The Qwen transfer
was stopped for shift handoff with its partial cache retained; Granite had not
started. Resume with `sh huggingface/environment.sh install` on that host. See
[the handoff](../HANDOFF-2026-11-04.md) for logs, the shared-node resource hook,
remaining gates and the existing Wattson harness. No emulator is required.

The original GenCost slice reads NEMO's literal GenCost 2024-25 capital-cost table;
it does not run NEMO's optimizer. The maintained just-mcp source is packaged with
two explicit local fixes (stderr diagnostics and exact Justfile path handling),
plus a lockfile and offline Rust builder. `usage` may later describe CLI help and
completion; it is not a query authorization DSL. `mise` adds little to this already
containerized, pinned runtime and is not a required dependency. Excel ingestion,
ODBC/Oracle drivers, Spark and marimo remain separate connector work; they are not
part of this demonstrated NEMO slice.
