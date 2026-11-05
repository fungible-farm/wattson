# 0017 — G3: verifier-guided investigative learning

- **Status:** open; **CPU groundwork done** 2026-10-24, incl. held-out networks and pools v2/v3 (below). The GRPO run needs the GPU and starts from the r7 students. [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md) G3, priority A4. Depends on G0 ([0008](0008-powercoder-benchmark-cannot-measure-agent-value.md))
  and G2 ([0016](0016-g2-verified-engineering-mathematics.md)).
- **Requirements:** PC-REQ-050–055, PC-CON-004.

## Deliverables
- **Procedural task generator:** many networks (CSIRO cases, plus IEEE 118 and PEGASE 89),
  operating points, contingencies, free-text objectives and unbracketed fits. Today's set is one
  network and one interval.
- **Rewards from the existing grader:** `grade()`, provenance, and a tool-call cost. Unsupported
  conclusions are penalised.
- **GRPO run:** starting from the r6 students. Rollouts go through the r6 harness (`tool_probe`, the
  MCP server, `evaluate_agent`) so training and evaluation match.
- **Distillation:** `powercoder/distillation/teacher.py` with solver-verified rejection sampling
  for tasks that no script covers.
- **Inference-time search:** the strict-tool-calling 2×2 queued after r6b is the first data point.

## Ablations (all with tools, equal budgets, frozen unseen networks)
1. Prompted base.
2. r6 student.
3. RL student.
4. RL student with verifier-guided search.
5. Larger planner with student workers.

## Exit criteria
- **Improvement:** over the strongest baseline on a primary benchmark declared in advance, with a
  paired confidence interval that excludes zero (`compare_agents.domain_threshold_met_ci_lower`).
- **No regression** on safety and evidence checks.
- **Cost recorded:** tool calls and wall time.
- **Robustness tests done:** reward hacking and verifier disagreement.
- **Isolation:** training and hidden evaluation stay separate.

## Cheapest first step
A GRPO pilot on the r6 Qwen student over generated load-scale fits drawn from two networks, scored
on calls to reach tolerance against bisection. Pair it with 0013's Newton tool, to see whether RL
discovers sensitivity-guided steps.

## Groundwork for the RL pilot (2026-10-24)
- **Procedural pool:** `powercoder/datasets/procedural.py` (`just powercoder::procedural-pool`).
  - Load-scale fits come from **two networks**, snem-sa-2000 and snem-1803. The latter is solvable
    since the flat-start retry (0008).
  - Each network's elements are *discovered*: buses whose voltage is strictly monotone over five
    load scales and spans at least 1e-3 pu; 40 per network.
  - References come from the benchmark's own real bisection.
  - A 40-task pool (20 + 20) is audited clean against all four evaluation suites (306-task,
    stratified 100, smoke 20, judgement 40).
- **Reward:** `training/rl_reward.py`.
  - +1 for an answer that passes the solver re-check, v3 field provenance and tool evidence.
  - −1 for a final answer with an untraced number.
  - −0.02 per tool call (0.02 × 24 = 0.48 < 1, so correct always beats incorrect).
  - Tested on a real scripted attempt: reward exactly 1 − 0.02 × calls.
- **The baseline to beat** (bisection replayed through the study tool):

  | Network | Median calls | Range |
  |---|---|---|
  | snem-sa-2000 | 5 | 4–7 |
  | snem-1803 | 6 | 4–7 |

  An RL student "discovers sensitivity-guided steps" (0013) only if it reaches tolerance in
  fewer calls than these, at the same pass rate.

## Held-out networks and pool v2 (2026-10-24) — resolves the caveat above

The v1 pool drew from snem-1803, the network r7 holds out. The tools now also serve pandapower's
built-in IEEE 39, IEEE 118 and PEGASE 89 cases (BSD-3; `powermcp.STANDARD_CASES`, identified by
the hash of their serialised data). All three solve in 3-4 Newton iterations. The network roles
are fixed in `procedural.py`, and an assert forbids overlap:

| Role | Networks |
|---|---|
| SFT trajectories (r7) | snem-sa-2000 |
| RL pool (`POOL_NETWORKS`) | snem-sa-2000, ieee118, pegase89 |
| Held out (`HELD_OUT_NETWORKS`) | snem-1803 (r7 judgement and unsolvable suites), ieee39 |

The widened network list is in the schema r7 trains on (trajectories v5), so RL rollouts later
use the same tool block as the r7 students: no train/serve skew.

Pool v2 (`training/unsloth/out/procedural-pool-20261024-v2`, `just powercoder::procedural-pool`):

- 150 load-scale fits, 50 per pool network, with 40 discovered elements each;
- prompt wording v2; 36 fits are unbracketed (no interval in the prompt);
- audited clean against six suites: benchmark 100 v1/v2, smoke 20 v1/v2, judgement v2 and
  unsolvable v2. `main` refuses to freeze a pool that is unclean or audited against nothing.

The baseline to beat, bisection replayed through the study tool over pool v2:

| Network | Median calls | Range |
|---|---|---|
| snem-sa-2000 | 6 | 4–8 |
| ieee118 | 6 | 4–7 |
| pegase89 | 6 | 4–7 |

## Pool v3 (2026-10-24): contingencies, unbracketed fits and free-text objectives

`training/unsloth/out/procedural-pool-20261024-v3`, 150 fits (50 per pool network):

| Variant | Fits | How |
|---|---|---|
| N-1 contingency | 48 | every 3rd draw, one line out, from up to 20 outages per network that solve and isolate no bus (`safe_outages`, seeded) |
| Unbracketed | 36 | no interval in the prompt; the bracket comes from the tool's limits |
| Free-text objective | 39 | two engineer-phrased templates, each stating the network, outage, what is scaled and over what range, the bus, the target and the tolerance |

N-1 fits needed a grader change. `filter._check_fit` re-solved the intact network, so it now
applies the task's outage (`generate.with_outage`) first, and the generator's reference
bisection uses the same composition. `test_procedural.py` checks the grader's metric equals the
tool's with the outage applied and differs from the intact one. The scripted procedure passes
150/150 over v3 (`script-baseline-procedural-pool-20261024-v3`), in every variant. The pool
audits clean against all six evaluation suites.

Bisection baseline over v3 (median calls, range):

| Network | Intact | N-1 |
|---|---|---|
| snem-sa-2000 | 5 (4–7), n=34 | 6 (4–8), n=16 |
| ieee118 | 6 (4–7), n=34 | 6 (5–7), n=16 |
| pegase89 | 6 (4–7), n=34 | 6 (4–7), n=16 |

## Operating points and pool v4 (2026-10-24)

IR 0.3.0 adds an optional `load_level` to `run_study_trial`, in [0.9, 1.1] like `scale`: every
load's P/Q times it before the study.
- It is refused with `load_scale`, which already scales every load.
- Omitted, responses are byte-identical, and the r6b replay is unchanged.
- `relevant_evidence` requires a study at the task's own operating point; a test shows base-load
  evidence is rejected.
- The generator, fit checker and procedures compose it like an outage, and the prompt states
  "with every load at L times its base value".

Pool v4 (`procedural-pool-20261024-v4`) has 210 fits:
- v3's 150 load-scale fits;
- 30 line-rating and 30 gen-dispatch fits at operating points 0.92/0.96/1.04/1.08, on all three
  pool networks.

Elements are discovered per network: lines loaded at least 1%, and generator–transformer pairs
whose loading is strictly monotone in dispatch with a span of at least 0.1%. The scripted procedure
passes 210/210, and the pool audits clean against all six suites. r7 trains on trajectories v6,
with the 0.3.0 tool block; its messages are identical to v5's.

## GRPO pilot, built and armed (2026-10-24)

**Code:**
- `training/grpo.py`, two steps:
  - `rollout` (host): k sampled attempts per task through the evaluation's own harness, graded
    by `grade()` and rewarded by `rl_reward`. vLLM returns exact prompt and completion token ids,
    so no re-rendering is involved (FM-22/23).
  - `update` (training image): group-relative advantages, then −A × mean completion-token
    log-probability per sampled turn. The adapter is saved, independently reloaded, merged and
    hashed, as `agent_sft` does.
  - Named stand-in: REINFORCE with GRPO's group baseline; no ratio, clipping or KL.
- `training/run_grpo_pilot.py` drives it:
  - 2 iterations × 64 tasks × 4 samples at temperature 0.8 on pool v4. Serving and training
    alternate, never overlap.
  - Then the r7 Qwen student against the RL student, greedy, on the held-out pool
    `procedural-heldout-20261024-v4` (90 fits on snem-1803 and ieee39) and the smoke suite.
  - It records strict and solver-verified comparisons, calls against bisection, and error ledgers.
- `scripts/grpo_wait.sh` (unit `powercoder-grpo-wait`): waits for r7's `pipeline_complete`, then
  launches the pilot. It refuses if r7 failed or the r7 Qwen student failed its smoke gate.

**Verified on CPU:**
- advantages; distinct task slices per iteration;
- token-exact sequences;
- a full rollout through the real harness, grader and reward, with a scripted policy;
- the loss direction in the training image (a positive advantage raises the completion's
  probability, a negative one lowers it);
- the calls metric reads 0 against bisection over the scripted baseline's 50 held-out fits.

On the real model and adapter, the update ran through base verification, trainable-adapter
loading and gradient checkpointing. It then stopped at Qwen3.5's linear attention, whose Triton
kernels need a GPU, so the first update runs on the GPU. The scripted procedure solves 90/90 of
the held-out pool.

**Left, and why:**
- **The pilot's GPU run:** queued behind r7.
- **Distillation:** blocked on a ruling. `powercoder/distillation/teacher.py`'s teacher is the
  llama-swap Qwen3.8 GGUF models on this host. r00t DEC-013 forbids GGUF inference on shinyDGX
  (OPEN-001 is the user's to resolve), sm3lly's GPU is off-limits by user direction, and a 96 GB
  teacher beside the GPU queue is the FM-10 OOM shape.
