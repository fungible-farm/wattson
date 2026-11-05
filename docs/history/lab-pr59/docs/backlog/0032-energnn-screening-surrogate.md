# 0032 — EnerGNN as a screening ranker, never as a source of reported numbers

- **Status:** deferred. **Merit: fifth** of the 2026-10-23 tool review.
- **Affects:** large N-k and remedy searches (PowerAgentBench Level 2-style), and PC-REQ-004
  provenance.

## Why
EnerGNN (RTE, MPL-2.0, v0.5.0, JAX/Flax, checked 2026-10-23) trains graph neural networks on
energy networks in RTE's H2MG hypergraph format. RTE says it is used in full-scale cases. RTE also
publishes `pandapower-to-energnn` and `pypowsybl-to-energnn` converters (MPL-2.0).

## The constraint
A surrogate's output is an approximation, not solver output, so under the provenance rule it can
never appear in an answer. Its only role is to **rank** what an exact solver then validates. That
is the pattern PowerAgentBench Level 2 uses, pairing an LODF screen with `validate`.

## Preconditions
- **A search space too big to solve exhaustively.** snemSA's full N-1 takes about a second per
  outage, so it isn't one yet. N-2 on a larger network, or remedy combinations, would be.
- **JAX with GPU on GB10/aarch64,** unverified. Measure it before planning around GPU training.
- **Training data from our own solvers,** with seeds disjoint from every scored task (FM-21).

## Acceptance (when picked up)
- **Every reported number** traces to an exact solve, never to the surrogate (a test).
- **Ranking quality is measured:** the share of the true top-k, from an exhaustive solve, found
  within the validation budget, compared with the LODF screen.
