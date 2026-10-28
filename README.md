# WattsOn

Small language models that plan and run verified power-system studies, and never do arithmetic themselves: every calculation is a
Python program the model writes and a restricted shell runs against a real solver (pandapower). Every answer is graded by
re-solving the network, not by matching text.

**Status: v0.1.0, a smoke check.** This release exists to answer one question: *does this toolchain reproduce on a machine
that is not the author's?* It ships the tool server, the Python shell, the harness, the scorers and the oracle trajectory
builder, plus a CPU-only `scripts/smoke.sh` that exercises all of them. It does **not** ship model weights; there is no
released model yet.

## Run the smoke check

```bash
git clone https://github.com/fungible-farm/wattson && cd wattson
scripts/smoke.sh
```

Needs Python 3.14 (uv fetches it) and [`uv`](https://docs.astral.sh/uv/) and network access to PyPI and GitHub (for the CSIRO case files). No GPU. It takes a few
minutes and exits non-zero on the first failing stage. It checks, in order:

1. **Environment.** `uv sync` from the lockfile on Python 3.14 (the numerical stack is pinned to the exact versions that produced the lab's evidence).
2. **Data.** The CSIRO Synthetic-NEM case files are fetched at a pinned commit and verified by sha256.
3. **Tests.** The CPU test suite (solver tools, the Python shell and its sandbox limits, the scorers including the
   float-tolerant normalised scorer, the trajectory builder, the DAgger relabeller, the train/serve parity machinery).
4. **Oracle trajectories.** The oracle writes and runs real programs through the real tool server for a handful of tasks (a fit,
   a report, a refusal, a three-outage planning question) and each is graded; a failure here means the solver, the shell and
   the grader disagree on your machine.
5. **Re-scoring shipped journals.** 20 recorded model attempts (`fixtures/`) are re-scored by the normalised scorer from
   scratch, re-solving the networks on your machine, and the counts must equal `fixtures/expected.json`. This is the check
   that floating-point and library differences do not change a score.

A passing run prints `SMOKE OK` and the versions of the libraries it used. Please report a failing or surprising run, with the
output, as an issue: that is exactly what this release is for.

## What is and is not here

- **Base model for the first release:** IBM Granite 4.2 8B (`ibm-granite/granite-4.2-8b`, Apache-2.0 per IBM's model card). The
  lab's earlier work used Qwen3.5-4B; it is not used here because its provenance is not established to our satisfaction.
- **No weights, no training data dumps.** Training and evaluation of the students (GPU) live in the lab repository for now.
- **Scope:** AC power flow on a handful of networks (the CSIRO synthetic NEM cases, IEEE 39, others from pandapower). One tool
  protocol. Not a general agent.
- **Security:** the Python shell is a restricted interpreter with resource limits. It is **not a security boundary**; do not run
  it on untrusted input. Containment is out of scope for this release.

## How scores are computed

A fit is correct if re-solving the network at the answered scale reaches the target within the task's tolerance. A report is
correct if each of the five solver values appears as a distinct number in the answer, whatever the wording, after every number is
normalised to 4 decimals with exact decimal arithmetic; voltages within 1e-3 pu are the same voltage. Every number in an answer
must also trace to the prompt, the tool schema, a tool reply or a printed result. See `src/wattson/inference/domain_scorecard.py`.

## Licence and data

MIT (see `LICENSE`). The CSIRO Synthetic-NEM-2000bus case files are fetched, not redistributed (CC BY 4.0, CSIRO). pandapower is
BSD-3-Clause. The shipped `fixtures/` are model outputs and generated synthetic tasks.

## Lineage

Extracted from the PowerCoder lab (`fungible-farm/nem-poweragent-lab`), renamed. Evidence, backlog notes and the experiment
history stay there.
