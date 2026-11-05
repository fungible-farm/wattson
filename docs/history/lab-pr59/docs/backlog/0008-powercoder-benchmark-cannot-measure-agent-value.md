# 0008 — PowerCoder benchmark cannot measure agent value (priority 1)

- **Status:** open, and this is **gate G0 of [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md)**: baselines, frozen benchmark definitions, scoreboard. Merit 5 in the [independent review](reviews/2026-10-23-r6-merit-review.md), idea 8. Must be stated as a caveat beside any r6 number.
- **Found:** 2026-10-23. Measured on the r6 inputs (`domain-stratified-100-20261022`, `smoke/splits/train.jsonl`).

## Evidence
- All 100 benchmark tasks and all 273 training rows use one network, `snem-sa-2000`.
- Every fit task gives the same interval, [0.9, 1.1].
- 36 of the benchmark's 69 (subfamily, element) keys also occur in training, with different targets.
- Every task is solved by a fixed ~30-line policy (bisection, or one `operation="none"` trial), and the students are trained to imitate exactly that policy. A line-rating fit is solvable from one trial: `max_i_ka` scales and the current does not, so s = loading(1)/target (derived from the code, not run).
- `compare_agents.py` gated on the point estimate. `domain_threshold_met_ci_lower` now records the paired 95% interval's lower bound beside it. The six pairwise comparisons on one sample have no multiplicity correction.

## Consequence
A student that reproduces the template will clear +10 pp against bases near 5% without showing domain or agentic skill. r6 measures "can a LoRA learn the tool protocol", which is a legitimate harness result, not agent value.

## Proposal
1. **Baselines.** (a) The script as an agent: the oracle, scored with pass rate, tool calls and wall time. (b) Each base model with the procedure written into its system prompt. Report student − prompted base and script − student, with paired intervals.
2. **Tasks that need judgement.**
   - Choosing the study from free-text intent.
   - Fits with no interval given, or with a non-monotone or non-convergent end.
   - "New violations versus the base case", which needs a violation-listing tool.
   - Multi-step goals across contingencies.
   - A held-out network.
   - RestoreBench (Lab 10).
3. **Score efficiency against the script:** calls, tokens and wall time.

## Cheapest decisive experiment
Baseline (b) on the 20 smoke tasks for both base models, about 1–2 h of GPU. If a prompted base matches the student, fine-tuning added nothing measurable.

## Judgement suite built (2026-10-23): `powercoder/datasets/judgement.py`, `just powercoder::judgement-suite`
The suite has 40 tasks, frozen with a manifest. It is audited clean against the training split:
40 unique prompts, no overlap. Every reference answer passes its own solver-checked grader. The
four families, 10 each:

| Family | What the model must do | Grader | Scripted procedure |
|---|---|---|---|
| intent | Answer a report phrased as an engineer's question (no procedure named) | existing report grader | 10/10* |
| open fit | Fit with no interval in the prompt | existing independent fit checker | 10/10* |
| worst contingency (`pf-worst-contingency`) | Study 3 outages and name the one with the highest max line loading, and that loading | new, re-solves every candidate; tool evidence for each | **0/10** |
| held-out network (`snem-1803`) | Report on a network no training row uses | existing report grader | 10/10* |

\*The scripted procedure reads each task's structured `inputs` (case, contingency, interval),
which a model never sees. Rewording or an omitted interval cannot affect it, but a model must
recover them from the prose. **The template ceiling here is 30/40.** Worst contingency needs a
multi-study comparison the trained template does not do.

**What it took:**
- **Ranking by loading, not voltage.** On snemSA an outage moves the minimum voltage by as little as
  1.8e-7 pu, so a voltage ranking would be ill-posed. Candidate sets are kept only when the top
  two loadings differ by at least 0.05 %.
- **A flat-start retry so `snem-1803` solves at all.** Its zero-reactance branches break
  pandapower's default DC pre-solve (`FloatingPointError`). `powermcp._runpp_counted` now retries
  with `init="flat"` on exactly that error, as Lab 2 documents for the same case. snemSA never
  takes that path: the trajectory rebuild is byte-identical (`e7988135`).

"Violations versus the base case" is not built, because no tool lists violating elements by id.

**Next (GPU):** run the four models on the suite. It is the first r6-style measurement where
reproducing the template cannot pass every task.
