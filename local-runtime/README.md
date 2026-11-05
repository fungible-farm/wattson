# Wattson local runtime: package a task, run it, inspect it

This is a container teaching environment for a Linux workstation. It demonstrates
the packaging and execution boundary that a Wattson command service can use. It is
**not a deployment of the full Wattson application or the repository's power-system
labs**. Its task is deliberately simple: ask a separate service for a message, verify
real Unix-socket and named-pipe communication, then inspect a deliberate failure.

The [Hugging Face/model profile](huggingface/README.md) adds containerized HF CLI/MCP
and separately versioned model-data OCI images for the two PromptExecutionAU Wattson
models. `install` now prepares that profile too. Model downloads total approximately
27.4 GB; the original IPC walkthrough below remains a separate, much smaller example.
The local Pi/Candle integration and offline bundle restore are still awaiting end-to-end
validation with these selected models; see that profile's current evidence and blockers.

Start with this business explanation: **“We deliver a task with its tools. You run it
the same way on each workstation. Every execution leaves a result we can inspect.”**
Do not start the client demonstration with cluster terminology. Let the first task
succeed, inspect a failure, then explain the machinery underneath.

## Minimum host contract

Containers package user-space software; they still need a host kernel and container
runtime. An arbitrary bare Linux machine cannot acquire these capabilities simply
by running an image. The supported starting point is:

- Linux with a working **rootless Podman 5.4+** installation and its OCI/runtime helpers.
- Working user namespaces, subordinate UID/GID allocation, and cgroup v2 delegation.
  Provision these through the organization's supported workstation image. This installer
  does not edit `/etc`, install packages, change cgroups, or configure privileged services.
- A POSIX shell and ordinary core utilities (`cat`, `dirname`, `mkdir`, `mktemp`, `sleep`).
- The repository, copied or pulled through the organization's approved process.

No host Python, uv, just, asciinema, kubectl, AI access, GPU, systemd user service, or
container-engine socket is required by the local example. Registry access is needed
only while preparing images. All task and service containers use `--network=none`.
If you already have `just`, use `just install`; otherwise `./wattson install` is the
equivalent bootstrap. Neither installs a permanent binary into the host OS.

This is OS-distribution independent **above that host contract**, not independent
of the Linux kernel or runtime configuration. A read-only/locked-down workstation
still needs its administrator to provision a supported runtime once.

## Five-minute walkthrough

From the repository root:

```sh
# Enter the example; all following files are in this directory.
cd local-runtime

# Connected preparation: build missing images, create the private pod, start
# the helper service, and run the committed socket + FIFO check.
./wattson install-ipc
# With host just already present, the same entry point is:
# just install-ipc
# You should see: PASS: Wattson IPC example installed

# A familiar task interface. One new container runs one packaged just recipe.
./wattson hello
# You should see: hello from the packaged service
# Followed by a result with exit_code: 0 and an evidence directory.

# Show the service and the retained command containers.
./wattson status

# Deliberately fail. This invocation should return a nonzero shell status.
./wattson fail

# Now inspect the failure rather than guess what happened.
./wattson inspect
# You should see a fail result with a nonzero exit_code.
# Each .wattson/runs/invocation-*/ directory contains:
# request.json, container-id, output.log, inspect.json, and result.json.

# Prove both IPC mechanisms against expected_check.json again.
./wattson check

# Stop the service while retaining containers, images, and evidence.
./wattson stop
./wattson logs
# External termination inspection is saved in .wattson/service-stopped.json.
# Restart and check again. This does not rebuild existing images.
./wattson install

# Remove this example's pod, commands, and scratch volume when finished.
# Images, recordings, and run evidence remain available.
./wattson down
```

You should observe a long-lived service and short-lived commands. A failed task does
not require rebuilding the service or reinstalling workstation software. Evidence
comes from actual container inspection and logs, including the resolved image ID,
timestamps, and termination state. If the host adapter is interrupted, an invocation
directory without `result.json` means **unfinished observation**, not success: inspect
the container ID it retained. The demonstration records completion synchronously;
it is not yet a crash-recovering controller or durable audit service.

## First principles: what is being separated?

An **image** is a package: executable programs, libraries, and defaults. A **container**
is a running instance of that package. The image does not need its own kernel.
An **entrypoint** specifies the executable to start. Here that is `just`; `hello`,
`check`, or `serve` selects a recipe from the justfile baked into the image.

The **data plane** does the work: `ipc.py serve` answers requests, and the command
container asks for them. This is real IPC, not a simulation of a power study.
The **control plane** creates, starts, waits for, inspects, and stops those containers.
In this first example, the short host script `wattson` performs that role using Podman.
It is intentionally small enough to read; Kubernetes supplies a richer controller
model in the next exercise.

```text
Operator: ./wattson hello             Host adapter: lifecycle + evidence
                                                 |
                        private pod, no network access
                     +---------------------------+-------------------+
                     | command: just hello       | service: just serve|
                     |          |                |          ^        |
                     |          +--- /shared/service.sock --+        |
                     | check also uses a FIFO in the shared volume    |
                     +-----------------------------------------------+
```

The **runtime image** contains only Python, just, and the example's files. The
**developer image** extends it with uv and asciinema. These are separate build targets;
recording utilities never enter the service/command runtime. `COPY --from` explicitly
imports just and uv from upstream images; Python and its needed system libraries come
from the Python base image. Copying an arbitrary dynamically linked binary alone would
not copy its library dependencies.

The checkout is not mounted into workers, so editing local source does not silently
change an installed command. Rebuild deliberately after code changes. For this example,
`install` checks image presence, not whether local source has changed. Use an approved
digest reference for release promotion; development tags are not immutable locks.

## Pods, namespaces, pipes, sockets, and cgroups

| Concept | Meaning here | What it does not imply |
|---|---|---|
| Pod | Closely related containers with a shared network/IPC context | An entire workstation or VM |
| Volume | A directory mounted into both containers | All image filesystems becoming shared |
| Unix socket | A filesystem-addressed connection at `/shared/service.sock` | Access to the host Podman socket |
| Named pipe / FIFO | A filesystem object carrying a byte stream between processes | An anonymous shell pipe automatically crossing containers |
| Linux namespace | Kernel isolation for networking, processes, mounts, etc. | A Kubernetes Namespace object |
| Kubernetes Namespace | API grouping and a scope for policy/RBAC | A VM or automatic network isolation |
| Cgroup | Kernel accounting and limits for workload resources | A bus for exchanging data |

We share a directory because Unix socket paths and FIFO paths must be visible to both
processes. We do not need host PID sharing, host IPC, cgroup mounts, a writable host
filesystem, or privileged containers. Containers retain their own resource limits;
a pod can also have a parent cgroup. A pod is not a single shared unrestricted cgroup.
The local budgets are tutorial choices: 128 MiB and one CPU per runtime container,
256 MiB for recording, and 64 processes. Command execution has a 120-second runtime
timeout. These are not measured budgets for Wattson solvers or GPU workloads.

Local rootless Podman runs these containers as container root, which maps to the
invoking host user, to simplify shared-volume ownership. All capabilities are dropped.
The Kubernetes version runs nonroot and uses group permissions instead. The local
example is an observability tutorial, not a certification of hostile-code isolation.

## Real asciinema example, without a host recorder

```sh
# Start the environment if it is stopped.
./wattson install

# Record the actual hello, check, and deliberate-failure recipes.
# asciinema runs only in the developer container, with no network or upload.
./wattson record
# File: .wattson/recordings/wattson.cast

# Play the recording locally; this needs an interactive terminal.
./wattson play

# Re-derive install, IPC, failure evidence, recording contents, and restart.
sh ./proof.sh
# After rebuilding, explicitly remove old example instances before proving new ones:
# sh ./proof.sh fresh
```

`tour.sh` is the commented, reproducible presentation script. The cast records its
real output; it is not a manufactured transcript. It demonstrates the commands inside
the boundary. To demonstrate the *outer* install/status/stop lifecycle on screen, use
an approved external terminal recorder; the command image does not receive the host
engine socket just to record its own controller. No pre-recorded cast is claimed here:
the proof generates it and verifies its actual contents.

## Carry it into a disconnected workstation

On a connected, approved preparation machine of the target CPU architecture:

```sh
# Build the runtime and developer images and fetch the explicitly named pause image.
./wattson build
# Re-derive the deployment proof before distributing it.
sh ./proof.sh
# Bundle all three required images, including the pod infrastructure image.
./wattson export
# Record an integrity checksum through the organization's artifact process.
sha256sum .wattson/wattson-images.tar > .wattson/wattson-images.tar.sha256
```

Transfer this directory, the tar archive, and the approved checksum to the isolated
machine. A checkout alone does not contain registry layers or Python package archives.
Do not expect it to build offline without those inputs.

```sh
# Verify using the supplied checksum from the same artifact directory.
cd .wattson
sha256sum -c wattson-images.tar.sha256
cd ..
# Import into the target user's Podman storage.
podman load --input .wattson/wattson-images.tar
# Refuse a build if anything is missing; perform no registry acquisition.
WATTSON_OFFLINE=1 ./wattson install
WATTSON_OFFLINE=1 ./wattson hello
```

Use `WATTSON_IMAGE` and `WATTSON_DEV_IMAGE` to select organization-managed references.
Tags, source pins, and `uv tool install` in the developer build are a readable starting
point, not a fully locked supply chain. Promote approved built images by digest and
preserve their inspection/SBOM metadata. Build each target architecture separately.

## The same pattern on Kubernetes, OpenShift Local, or k0s

This exercise uses an **existing cluster**. It does not silently install a privileged
local Kubernetes distribution. OpenShift Local has its own VM/runtime requirements;
k0s has node and cgroup requirements. A namespace is not a replacement for either.
`k9s` is an optional UI for watching the cluster, not a cluster distribution.

| Kubernetes object | Purpose in this exercise |
|---|---|
| Namespace | Groups just this example's API objects |
| Pod | Places the helper and command on the same node with one shared `emptyDir` |
| Job | Creates the Pod and records successful/failed completion |
| Native sidecar | Starts the helper first and stops it when the command finishes |
| Startup probe | Confirms that the helper can answer before starting the command |
| `emptyDir` | Scratch volume for socket and FIFO; removed with the Pod |
| NetworkPolicy | Denies Pod network traffic when the cluster's network plugin enforces it |
| Service account / RBAC | Identity and permitted API operations for a future controller |

This Job needs Kubernetes **1.29+ with native sidecars enabled**. The Job has no API
token and needs no engine socket. Your operator account needs permission to create
the namespace, policy, and Job. An eventual `just-mcp` controller should have a scoped
service account for its namespace; discovering recipes does not require giving every
worker permission to create other workloads.

First push the runtime image to a registry accessible to the cluster nodes. A
`localhost/…` image in workstation Podman storage is **not** automatically available
inside an OpenShift Local VM or k0s/containerd storage.

```sh
# Replace this with your approved registry (and preferably an approved image digest).
podman tag localhost/wattson-command:example registry.example/team/wattson-command:example
podman push registry.example/team/wattson-command:example
export WATTSON_KUBE_IMAGE=registry.example/team/wattson-command:example

# Render actual Kubernetes objects using Python from the image, not the host.
./wattson manifest > .wattson/job.json
# For OpenShift: omit the fixed group so its SCC assigns a permitted fsGroup/UID.
# ./wattson manifest openshift > .wattson/job.json

# kubectl (or oc) is supplied by the existing cluster administration environment.
kubectl apply -f .wattson/job.json
kubectl -n wattson-local-example get jobs,pods
kubectl -n wattson-local-example wait --for=condition=complete job/wattson-ipc-check --timeout=150s
kubectl -n wattson-local-example logs job/wattson-ipc-check -c command
# You should see: PASS: socket + FIFO round trips
kubectl -n wattson-local-example logs job/wattson-ipc-check -c service
kubectl -n wattson-local-example get pods -o json > .wattson/kubernetes-evidence.json

# Save required logs before deleting these explicitly scoped resources.
kubectl delete -f .wattson/job.json
```

The name is fixed for a readable first example. Completed Jobs cannot simply be
re-applied with a different Pod template: delete the old Job or use a new invocation
name. A production controller allocates a unique Job name per request. This example
uses no retries, a 120-second deadline, and a one-day cleanup TTL. Persist logs and
status before TTL removes evidence; the cluster does not give you permanent audit
storage by default. `PreStop` is not a successful-completion callback: observe Job
and container termination status externally.

There is one deliberate topology difference: local Podman retains a helper between
commands; the Kubernetes Job starts a helper **for that invocation** and stops it
with the Job. Both run the same protocol and fixture. Unix sockets and FIFOs in an
`emptyDir` are only for containers in that one Pod. If the next client task needs an
independently scaled, long-lived service, put it in a Deployment and expose its TCP
API through a Kubernetes Service. A Job in a different Pod then calls that API; it
does not inherit this Pod's filesystem or pipe. Introduce that extra boundary when
the business task needs it.

## Where upstream just-mcp fits

The `nemo-agent` profile packages upstream just-mcp 0.2.0 from pinned source with
two explicit local fixes: send tracing diagnostics to stderr, and execute the exact
discovered Justfile path. Its Rust regression and real MCP initialization/list/call
probe passed. This is a locally patched build, not an upstream release of those fixes.
The integration contract is: choose a known recipe, allocate an invocation, pass
arguments as an array, execute the pinned image, collect logs/status, retain evidence,
and return the observed result. Only the controller needs orchestration access.

An image entrypoint of `just` runs recipes **inside that container**. Neither a shared
volume nor the Kubernetes API turns that process into a host command. If a legacy
task truly needs host execution, implement a separately authorized host adapter and
label that execution location in its result. Do not make that the default story for
this client's first demonstration.

## Scope of “all the requirements in the justfile”

Every recipe in **this directory's Justfile** is accounted for: the host adapter owns
installation/lifecycle, runtime recipes execute in the command image, and recording
executes in the developer image. It does not promise to run the repository root's
whole Justfile unchanged. In particular:

| Existing recipe dependency | Packaging work before moving it |
|---|---|
| Python / Rust libraries | Build into a versioned utility image, execute existing proof gates |
| CSIRO case files / model weights | Stage approved data artifacts and mount read-only |
| AEMO or other outbound fetch | Acquire in a permitted environment; consume a verified cache |
| systemd / host SSH | Replace with a Job/controller or retain an explicit host adapter |
| GPU inference | Node/device-plugin provisioning, scheduling, and measured memory limits |
| Raw Ethernet / physical devices | Explicit node placement and privileges; separate from this minimal pod |

For the client, choose one real, low-risk business task next—such as validating an
approved local input and producing a report—and package its existing deterministic
checker in place of `ipc.py`. Keep the operator's install/run/inspect interface while
changing the work behind it. The benefit to demonstrate is repeatable delivery and
visible execution, not the number of technologies installed.

## Validation and source references

`proof.sh` is the deployment gate; `test_runtime.py` checks the actual two-process
protocol, clean SIGTERM shutdown, failure status, and Kubernetes configuration.
Deployment status for this checkout is recorded in [VALIDATION.md](VALIDATION.md).
No Wattson physics results are asserted or fabricated by this tutorial.

Dependency licenses at adoption: just CC0-1.0; Python PSF-2.0; uv MIT/Apache-2.0;
asciinema 2.4.0 GPL-3.0; Podman and Kubernetes/pause Apache-2.0. Retain upstream/base
image license notices and review the complete image contents through your normal
artifact process. No new dependency is added to the repository Python environment.

Primary references: [Podman pod creation](https://docs.podman.io/en/latest/markdown/podman-pod-create.1.html),
[Kubernetes Pods](https://kubernetes.io/docs/concepts/workloads/pods/),
[Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/),
[sidecars](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/),
[container lifecycle hooks](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/),
[OpenShift arbitrary-user image guidance](https://developers.redhat.com/blog/2020/10/26/adapting-docker-and-kubernetes-containers-to-run-on-red-hat-openshift-container-platform),
[asciinema](https://docs.asciinema.org/manual/cli/),
[packaged just-mcp source](https://github.com/toolprint/just-mcp).
