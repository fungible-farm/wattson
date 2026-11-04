# Validation evidence

Verified in this workspace on 2026-11-04. This is a new teaching example, not a
claim that the full Wattson application or the existing lab Justfile is portable.

This document's original deployment proof covers the IPC example only. See
[the shift handoff](HANDOFF-2026-11-04.md) for the later NEMO/HF work, rejected
initial model choice, remote preparation state, and remaining acceptance gates.
The IPC proof now uses `install-ipc`, so it does not depend on model downloads.
At wrap-up, the corrected full `sh ./proof.sh` passed again, including image
export/reload, with its final output in `.wattson/handoff-ipc-proof-final.log`.
Four runtime tests and five NEMO provenance/scoring tests also passed. A repeated
archive export defect found during this rerun was fixed and re-verified.

## Passed

- POSIX shell syntax checks for `wattson`, `proof.sh`, and `tour.sh`.
- Both Justfiles parsed and their recipes listed with the installed `just`.
- Four focused unittest checks: real socket/FIFO communication across independent
  processes, clean service SIGTERM, intentional exit 17, bounded Kubernetes Job
  configuration, and OpenShift UID/group compatibility (the latter checks share
  the manifest tests).
- Both OCI image build targets completed on the local Podman host.
- `sh ./proof.sh fresh` completed with
  `PASS: Wattson local runtime proof (pod retained; ./wattson down removes it)`.
  It created fresh containers, verified cross-container socket/FIFO bytes against
  `expected_check.json`, observed the deliberate command exit **17**, captured actual
  engine inspection and logs, recorded and parsed a real asciinema v2 cast, stopped
  the helper and checked its external termination state, restarted it, exported all
  three required images, and re-imported the archive successfully.
- The service log after the final stop included both `service_started` and
  `service_stopped`. The host recorded termination independently in
  `.wattson/service-stopped.json`.
- OpenShift objects rendered using Python inside the runtime image, including an
  organization-registry image override, without a host Python dependency.

Final built image IDs, as recorded by Podman:

```text
runtime:   971bca12e70770c7190961ef29a61ae0ede681d3db77aa8c1475e042005be2a4
developer: 543eedd7e9e4cc3bf162014409787cda7271a67c3e0cc71b02668cdbca6712f4
pause:     afb61768ce381961ca0beff95337601f29dc70ff3ed14e5e4b3e5699057e6aa8
```

These identify this build; they are not promised reproduction hashes. Registry
tags and the base build inputs must be promoted through an approved digest/SBOM
process before controlled distribution.

## Local artifacts

- `.wattson/recordings/wattson.cast`: actual recorded walkthrough; play with
  `./wattson play` in an interactive terminal.
- `.wattson/runs/invocation-*/`: invocation requests, results, logs, and container
  inspection. Earlier failed development invocations remain visible as failures.
- `.wattson/wattson-images.tar`: archive containing runtime, developer, and pause
  images, successfully re-imported into this user's Podman storage.
- `.wattson/job.openshift.json`: rendered cluster objects.

These generated artifacts are intentionally gitignored. The source and committed
proof regenerate them after a pull. The example pod is retained by the proof for
inspection; use `./wattson down` to remove only this example's pod and IPC volume.

## Not yet verified

- Kubernetes or OpenShift admission and execution on an actual cluster; objects
  have been rendered and configuration-tested, not deployed in this session.
- A fresh, disconnected workstation or a second Linux distribution. Archive
  re-import into existing storage is useful evidence, not a clean-machine proof.
- Full runtime execution on a second CPU architecture, hardened workstation
  cgroup/user-namespace policy, or target-site registry approval. HF tooling alone
  has now built and passed its MCP probe on x86-64 sm3lly; this is narrower evidence.
- Interactive playback was not exercised; the generated cast format and actual
  output were checked by the deployment proof.
- Full Wattson services, selected Wattson-model inference, GPU workloads, or
  host-execution adapters. The later locally patched just-mcp integration did pass
  initialize/list/call and its regression; see the handoff for its exact scope.

## Session observations

The sandbox's read-only Podman runtime and uv cache needed host escalation for
verification. This is specific to this agent context. After that approval, image
fetches from `ghcr.io`, `docker.io`, and `registry.k8s.io`, and the developer image's
uv package installation, worked. No repository capability matrix was changed.
