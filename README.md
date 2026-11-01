# WattsOn

Small language models that plan and run verified power-system studies, and never do arithmetic themselves: every calculation is a
Python program the model writes and a restricted shell runs against a real solver (pandapower). Every answer is graded by
re-solving the network, not by matching text.

**Status: v0.1.0, untagged, with two released models.** Weights are on Hugging Face under Apache-2.0:
[`PromptExecutionAU/wattson-granite-4.2-8b-py`](https://huggingface.co/PromptExecutionAU/wattson-granite-4.2-8b-py) and
[`PromptExecutionAU/wattson-qwen3.5-4b-pyt`](https://huggingface.co/PromptExecutionAU/wattson-qwen3.5-4b-pyt).
Measured results, with seeds and limits, are in [`docs/results.md`](docs/results.md).

## Quick start

You need [Podman](https://podman.io) 5 or newer and [`just`](https://just.systems). Nothing else is installed on your machine.

```bash
git clone https://github.com/fungible-farm/wattson && cd wattson

just labs            # labs 1-3, in a container. CPU only, no model, no GPU. About a minute.
```

To run a model, which needs an NVIDIA GPU (see [install](docs/install.md) for the one-time driver setup):

```bash
just up granite      # first run downloads 17 GB from Hugging Face and verifies every file's hash
just doctor          # checks everything, including one real completion from the model
just ask "Using the power flow solver, find the scale factor s in [0.9, 1.1] applied to the active-power dispatch of generator 2 on case 'snem-sa-2000' such that the loading of transformer 4 equals 0.184093 percent (within 0.0006941900815668939 percent). Return only the scale factor."
just eval smoke      # 20 tasks, graded by re-solving the network
just pi              # the pi coding agent, wired to the model and its tools
just down            # stop the pod; the downloaded model is kept
```

`just up qwen` starts the smaller Qwen3.5-4B student (8.5 GB). If something else on your machine uses port 8000, set
`WATTSON_PORT=8100`. If anything is wrong, `just doctor` says what and how to fix it; [troubleshooting](docs/troubleshooting.md)
lists every failure we hit while building this.

## What is here

| | |
|---|---|
| [`docs/`](docs/index.md) | install, quick start, containers, serving, the `pi` agent, data, training, evaluation, results, troubleshooting |
| `src/wattson/` | the tool server and Python shell, the harness, the scorers, the trajectory builder, the `wattson` CLI |
| `benchmarks/` | labs 1-3, deterministic power-system tasks with committed expected results |
| `data/` | every task set, frozen evaluation suite and oracle trajectory used to train and score the released models, with a SHA-256 manifest |
| `containers/`, `kube/`, `justfile` | the layered images, the pod definition, and the commands above |
| `model-cards/` | the cards published with each model |

## Use it, and what may flow back

Anyone may use this **downstream**: MIT or Apache-2.0, at your option. Fine-tune it, run it on your own networks, redistribute it.
What flows **upstream** into this repository (task sets, benchmarks, trajectories, weights published from here, contributions)
must be derived from public data, with its provenance recorded. [`docs/policy.md`](docs/policy.md) says exactly what that means.

## How scores are computed

A fit is correct if re-solving the network at the answered scale reaches the target within the task's tolerance. A report is
correct if each of the five solver values appears as a distinct number in the answer, whatever the wording, after every number is
normalised to 4 decimals with exact decimal arithmetic; voltages within 1e-3 pu are the same voltage. Every number in an answer
must also trace to the prompt, the tool schema, a tool reply or a printed result. A separate suite
(`data/suites/derived-heldout-20261030`) asks questions whose answer is a sum, difference or mean over several solver results, so
a model that adds in its head gets it wrong. See [evaluation](docs/evaluation.md).

## Limits

- **Scope:** AC power flow on a handful of networks (the CSIRO synthetic NEM cases, IEEE 39, others from pandapower). One tool
  protocol. Not a general agent.
- **"No arithmetic" means this, exactly:** a shell student writes the computation as Python and reports what the program printed. That is a
  trained behaviour that we measure (`arithmetic_exposure`), plus an enforced guarantee about *answers*: with `--enforce`, an answer
  containing a number no tool produced is refused. It does not police a model's private reasoning or the constants in its code. See
  [evaluation](docs/evaluation.md#runtime-guard-refusing-an-unverified-number) for the measured numbers.
- **Security:** the Python shell is a restricted interpreter with resource limits. It is **not a security boundary**; do not run
  it on untrusted input. Containment is out of scope for this release.
- **`pi` needs special configuration** or these models invent commands and numbers; `just pi` and `wattson pi` apply it
  ([why](docs/pi.md)).

## Licence and data

Licensed under either of MIT (`LICENSE-MIT`) or Apache-2.0 (`LICENSE-APACHE`), at your option. Unless you state otherwise, any
contribution you submit for inclusion is dual licensed as above, without additional terms or conditions. The CSIRO
Synthetic-NEM-2000bus case files are fetched, not redistributed (CC BY 4.0, CSIRO). pandapower is BSD-3-Clause.

## Lineage

Extracted from the PowerCoder lab (`fungible-farm/nem-poweragent-lab`), renamed. Evidence, backlog notes and the experiment
history stay there; the labs' environment is described in that repository's README, which now points here.
