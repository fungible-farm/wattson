# 0023 — The SA case (snemSA) has no real branch ratings, one cost, and one slack

- **Status:** **proposals 1 and 3 done** 2026-10-23 (below); proposal 2 (derived snemSA ratings) not done: IEEE 39 now provides thermal signal from real data. Found 2026-10-23 while building the G2 tools (`powercoder/engineering/`, [0016](0016-g2-verified-engineering-mathematics.md)).
- **Affects:**
  - G2's OPF and thermal checks, and G3's task generator ([0017](0017-g3-verifier-guided-investigative-learning.md));
  - any "outperform engineers" claim that relies on this network.

## Evidence (measured)
- **No real branch ratings.** All 698 branches in `data/snemSA.m` have rateA = rateB = rateC = 10000 MVA
  (`grep -cE '10000\s+10000\s+10000' data/snemSA.m` gives 698), MATPOWER's conventional "no limit" value.
  The highest AC loading over all 698 N-1 outages is 3.76% (2.66% in the base case), so thermal
  screening finds nothing.
- **DC OPF is plain economic dispatch with many equal optima.** All 57 generators cost the same
  10 €/MWh, and no branch limit enters pandapower's OPF. The objective is exactly 10 × load, and
  the dispatch is arbitrary: pandapower and HiGHS choose dispatches up to 137 MW apart, both
  optimal.
- **One slack generator:** gen 29 at bus 985, behind transformer 0.
  - Its outage de-energizes 502 of 503 buses, because pandapower energizes only islands that
    contain a slack, even though 2,008.8 MW of generation remains in that island.
  - 70 N-1 outages strand generation this way. Their de-energized load is an upper bound, reported
    as `de_energized_generation_mw`.
- **Voltage is the only binding signal.** Of the 698 outages, 133 introduce new voltage
  violations and none introduces a thermal one.

## Consequence
On this case, OPF, thermal-limit and transfer-capacity tasks (PRD-0010 Demonstration A) have no
signal, and "largest permissible transfer" is unbounded by the thermal limits. Voltage tasks are
meaningful. The slack artifact makes islanding outages look far worse than a system with
distributed reserve would.

## Proposal
1. **For G2/G3 tasks needing limits and costs,** use cases that have them: IEEE 118 and PEGASE 89
   from RestoreBench (CC BY 4.0), or other CSIRO cases once checked. Record each case's provenance.
2. **If snemSA stays in use,** add documented, derived ratings (for example, a percentile rule from
   conductor type, stated as a named assumption), and per-generator costs from a cited source.
   Never present invented values as data.
3. **Use distributed slack** (pandapower `distributed_slack`, or a gen per island) for islanding
   studies, and report both conventions.

## Proposal 1 done (2026-10-23): cases with limits and costs, measured
`powercoder/engineering/cases.py` records each case's source, licence and measured
suitability. The cases load from the already-fetched PowerAgentBench checkout. They are not in
the evaluation's network enum, so prompts are unchanged.

The G2 DC OPF needed one extension: **quadratic costs**, which IEEE 118 has, are now checked by an
independent HiGHS QP (`dc_opf.solve_independent_qp`; `highspy` 1.15.1, MIT). Strictly convex
costs make the dispatch unique. A test shows the QP reproduces the LP exactly on linear costs
(same objective, dispatch and LMPs), which pins its dual sign. The "no independent formulation"
test now uses piecewise costs.

| Case | DC OPF | Thermal N-1 (exhaustive AC) | Use for |
|---|---|---|---|
| `ieee39-pab-stressed` (PowerAgentBench Level 1) | solved, cross-check agrees; 1 flow limit and 8 generator limits bind; LMPs 11.5–36.0 EUR/MWh | 31 of 46 outages add a thermal violation (max 175%) | **OPF, congestion and thermal tasks.** Costs are identical across units |
| `ieee118-restorebench` | solved, QP agrees; only p_min binds; flat LMP 39.38 | none (ratings 16.6–41.4 kA; max 5.3%) | dispatch with varied costs, no congestion |
| `pegase89-restorebench` | infeasible (HiGHS certifies) | 31 of 210, but 101 of 210 ratings are placeholders and the base case has 16 overloads | infeasibility and restoration tasks only |

No available case has varied costs **and** binding ratings. IEEE 39 gives congestion and thermal
signal; IEEE 118 gives cost-merit signal.

## Proposal 3 done (2026-10-23): both slack conventions for islanding outages
`contingency.reassign_island_slacks` / `slack_reassignment_study` re-solve each outage that
strands generation. Every otherwise-unslacked island that has generators gets a slack: its
largest in-service generator by `max_p_mw`, **a named convention, not data**. Both results are
reported side by side.

| snemSA, the 70 stranding outages | Single slack | Slack per island |
|---|---|---|
| Outages with de-energized buses | 70 | 0 (all solved; Kirchhoff residual ≤ 8.5e-9 MVA) |
| Outages where an island's slack must leave its generator's [min_p, max_p] | — | 20 |

So "energized" holds for 50 outages using the island's own generation. In the other 20 the island
balances only under an idealised slack: trafo 0's 502-bus island has 2,008.8 MW scheduled against
1,766.6 MW of load, so its slack would have to absorb 160.5 MW. Those islands need redispatch, and
are flagged rather than counted as served. snemSA has 7 negative loads (to −76 MW), so de-energized
load is compared per outage, never summed.
