---
license: apache-2.0
base_model: ibm-granite/granite-4.2-8b
library_name: transformers
pipeline_tag: text-generation
language: [en]
tags: [granite, tool-calling, power-systems, pandapower, wattson, lora-merged]
---

# wattson-granite-4.2-8b-py

Granite 4.2 8B fine-tuned (LoRA, merged to bf16) to answer AC power-flow questions about transmission networks by **writing
Python that calls a solver**, and never computing numbers itself. Part of [WattsOn / wattson](https://github.com/fungible-farm/wattson).

## What it does

It is given a `run_python` tool (a restricted Python shell with a `solver` module: `solver.trial(...)` runs a pandapower AC power
flow, `solver.exists(...)` checks that a line or bus exists) and tasks such as fitting a load scale to a voltage target, reporting
an N-1 contingency, choosing the worst of several outages, or refusing a task that names an element that does not exist.

## Evaluation (held-out networks, one seed)

| Suite | Result |
|---|---|
| Held-out fits and reports (90) | 90 of 90 |
| Smoke (20) | 20 of 20 |
| Judgement: worst outage (40) | 36 of 40 |
| Unsolvable: must refuse (28) | 14 of 28 |
| Planning: multi-candidate (40) | 40 of 40 |
| Held-out attempts containing a number no tool or prompt gave the model | 0 of 90 |
| Derived-arithmetic suite (40) | 37 of 40 |
| Derived-suite attempts containing a number no tool or prompt gave the model | 1 of 40 |

One training seed. The Qwen3.5-4B students of the same recipe vary by up to 9 tasks between seeds on the unsolvable suite, so
the unsolvable score here should not be read as stable. Full tables and limits: `docs/results.md` in the repository.

## Training

- Base: `ibm-granite/granite-4.2-8b` at revision `b3791992239dce12d0038f1f54a8adb7b02d59ab` (Apache-2.0).
- Data: 826 oracle-scripted trajectories (the programs, tool replies and answers are produced by a deterministic script that runs
  the real solver; no model-generated text). Networks: CSIRO Synthetic-NEM-2000-Bus (CC BY 4.0, CSIRO) and pandapower cases.
  Held-out evaluation networks are not in the training rows.
- LoRA r=32, alpha=64, all attention and MLP projections; 2 epochs; learning rate 1e-4 cosine; sequence length 8192; seed 42.
- Weight integrity: SHA-256 of every file is listed in `hash_manifest.json` in this repository.

## Intended use and limits

- For the WattsOn evaluation harness and research on tool-grounded numerical reasoning. It is **not** a power-system engineering
  tool and its answers must not be used for operational decisions.
- The Python shell it calls is **not a security boundary**: it is a restricted `python -I` child with a module allowlist,
  a wall-clock limit and a memory limit. Do not expose it to untrusted input.
- Evaluated on AC power flow only, on a handful of networks. Refusal of non-existent elements is the weakest behaviour.
