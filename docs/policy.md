# Use and contribution policy

## Downstream: anyone may use this

The code is under **MIT or Apache-2.0, at your option**. The released models are Apache-2.0. You may use them, fine-tune them,
run them on your own networks and data, and redistribute the result, including privately and commercially. You do not need to tell us
or publish what you build.

## Upstream: what flows back must be derived from public data

"Upstream" means anything that enters this repository or is published from it: task sets, evaluation suites, benchmarks,
trajectories, training configurations, model weights, and contributions of any kind. Those must be derived from **public data and
public sources only**, and their provenance recorded alongside them (a `manifest.json`, a licence note, or a source URL with a
version or commit).

In practice:

- A model you fine-tune on your own utility's network data stays yours. Do not contribute that data, tasks generated from it, or
  weights trained on it. A pull request that adds them will be declined.
- A task set built from a public case (CSIRO, pandapower, MATPOWER, IEEE test systems, published datasets) is welcome, with the case
  named and its licence stated.
- Results measured on private data may be described in general terms, but not reproduced in `docs/results.md`.
- Evaluation suites stay frozen once published. A change is a new suite with a new name.

## Why

The released results are only worth anything if someone else can reproduce them. Every number in [results](results.md) can be traced
to files in `data/` and to public networks. One private row in the training set would make that untrue for everything downstream of it.

## What this policy is not

It is a rule about what we accept into this repository. It is not a restriction on what you do with your copy, and it is not a
licence term: the licence is MIT or Apache-2.0, unmodified.
