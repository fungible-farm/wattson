# 0025 — Package the services for Podman Desktop: several optional, on-demand, memory-heavy containers

- **Status:** awaiting requirements. The user said on 2026-10-23 that a set of project packaging
  requirements is coming. This records what exists and what has been measured, so those
  requirements can be checked against facts. Nothing here is a design decision yet.
- **Affects:** `install.sh` / `just deploy`, `kube/`, the PowerCoder serving in
  `training/run_agent_study.py`, and DEC-001 (Podman) and DEC-014 (suspendable model hosting) in the
  r00t register.

## What exists
- **Kubernetes manifests for `podman kube play`** (`kube/`):
  - `llamacpp-phi-pod.yaml`, `powermcp-pandapower-pod.yaml` and `villasnode-tap-pod.yaml` were each
    run with podman, each with one disclosed limitation (see `kube/README.md`).
  - `benchmark-runner-job.yaml` runs one pod, not 3, because `podman kube play` doesn't implement Job
    `completions`/`parallelism`.
- **Containerfiles:** `Containerfile.base`, `Containerfile.powermcp`, `Containerfile.bakeoff`.
- **Model serving for the agent study:** `run_agent_study.serve()` starts vLLM in podman with
  readiness checks (FM-12) and stops containers first (FM-13), supervised by systemd `--user` units
  (FM-11).
- **llama-swap:** a host service that swaps models on demand, with `globalTTL: 600` (FM-10).

## Measured memory (GB10, 121 GB unified, CPU and GPU share it)

| Service | Peak used | Source |
|---|---|---|
| vLLM, Qwen3.5-4B (base or merged student) | 24.5–24.6 GB | r6b `memory_after_load`, 2026-10-23 |
| vLLM, Granite 8B | 36.9–39.3 GB | r6b `memory_after_load` (base 36.9, student 39.3) |
| llama-swap `qwen3.8-flash-next` | ≈ 96 GB | FM-10 (with the benchmark vLLMs it caused a host OOM kill, 2026-10-21) |

The table means "on-demand" is a memory admission problem, not only a start/stop problem:
- any two of the large services can't both be resident;
- the existing safeguard is `run_comparison.await_memory()`, which waits for free memory before each
  GPU stage.

## Questions the requirements should answer
1. **Who and where.** Which hosts? GB10 (ARM64, unified memory), the x86 peers, Windows/macOS
   desktops through Podman Desktop's VM? This decides the image architectures. The repo's images are
   built on ARM64 here, and DEC-002/DEC-034 note r0cky builds ARM64 natively.
2. **Which services, and which are optional.** For example: PowerMCP tools (small), a model server
   (24–96 GB), the RestoreBench stand-in, kr0ki, the RDF/SPARQL service from
   [0024](0024-element-discovery-and-graph-search.md).
3. **What "on demand" means.** Each choice changes what Podman Desktop has to manage:
   - start on first request (a llama-swap-style proxy);
   - start by hand from the Podman Desktop UI;
   - start by systemd socket activation / Quadlet.
4. **The memory policy.** Refuse to start, queue (as `await_memory` does), or evict the
   least-recently-used service?
5. **Model weights.** Bundled, pulled from Hugging Face at first start, or mounted from a shared
   cache (`/mnt/sm3lly` per DEC-010)? Licences are recorded per PSCADOSSE.

## To verify before relying on it (not yet checked here)
- **Podman Desktop features:** which features apply on this host, with the version noted. Playing a
  Kubernetes YAML and Compose are the commonly cited ones.
- **GPU access:** whether it reaches the GB10 GPU through CDI the way the CLI does (REQ-007a: the CLI
  passed with podman 6.1.3).
- **Supervision:** whether a Quadlet `.kube` unit gives the same supervision guarantees as the current
  transient units (FM-11, FM-13, FM-15).
