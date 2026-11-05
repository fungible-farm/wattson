# PowerCoder handoff — 21 October 2026

> **Superseded by [POWERCODER_HANDOFF_2026-10-23.md](POWERCODER_HANDOFF_2026-10-23.md).** Kept as the
> r1–r6 history; its "live state" paragraphs are out of date.

Snapshot: **22 October 2026, 22:15 AEDT (UTC+11)**. Branch: `chore/powercoder-recovery`.
Implementation: r6 runs from `90f849a` and later; r5 ran from `6caf63c` (stopped); `f3bab24` ran r4; `a279d7b` ran r3; `8442962` ran r2.
Earlier revisions of this handoff: `b1a7b44`, `bb2a90c` (both describe a live
pipeline that no longer runs).

## Objective and honest completion status

The user requested committing local work, completing the full 306-task comparison
of Qwen3.5-4B base against its original Unsloth student, demonstrating at least a
**10 percentage-point domain pass@1 improvement**, downloading Granite 4.2 8B,
training it on the same dataset, and evaluating/comparing its base and student.
The detailed original request/audit is local to this host at
`/home/brianh/.codex/attachments/573df69a-e50a-4716-b01e-1e68b2cd83bc/pasted-text-1.txt`.
The goal record is `.pi/goals/active_goal_2026102108123965_mvh6j65n-98gogh.md`
(contracts match the real output files, auto-continue off).

**The experiment is incomplete.** The Qwen comparison is complete and **missed
the ≥10 pp target**: the student scored below the base (see "r4 results").
Granite training is complete. The Granite evaluations crashed in r4 on an
inference timeout (FM-6); r5 re-ran them supervised and was **stopped
deliberately** at Granite base 34/306 because of benchmark and training defects
(see "r5 run"). r6 replaces r1–r5 with fixed tools, retrained students and a
stratified sample (see "r6"). Failure modes are recorded in `AGENTS.md` (FM-1…FM-20).
At 10:01 AEDT, on user
instruction, the r2 pipeline was stopped as unsupervised: driver PID `987032`,
Qwen base evaluator PID `954658`, its MCP child and `uv` wrappers (SIGTERM), then
`podman stop powercoder-qwen-base-domain-r1` (stopped, **not removed**). Granite
training and all other evaluations never started. Only Granite download and
llama-swap serving are done. Do not claim GRPO, the Windows audit, or the full G5
gate is complete on the strength of earlier goal records: the paused goal
`mvg58a4q-t1fzwh` marks M08/M09 `complete` with `evidence` fields that merely
restate their contracts.

### Partial r2 evidence (preserved, not a result)

`training/unsloth/out/agent-domain-r2/base/results.jsonl`: **31/306** results,
0 strict passes, 0 numerical passes, 1 tool-evidence pass, 11 empty answers. No
`summary.json`. This is **not a representative sample**: tasks run in subfamily
order, so the 31 are 26 `pf-report` + 5 `pf-load-scale-fit`, with no line-rating
or dispatch fits. The empty answers are a lead worth inspecting after a complete
run (e.g. whether reasoning exhausts the 2048-token response budget); do not change
the budget mid-benchmark to chase it. The driver journal
`pipeline-scorecards.jsonl` ends at `adopt_base`; it records no stop event because
the driver was killed. This section is the record of that stop.

Measured r2 throughput: 31 tasks in ~94 minutes (≈3 min/task), so one 306-task
run takes ≈15 h; four runs plus Granite training is roughly 2–3 days of GPU time,
and Granite 8B will be slower than the 4B.

## Why it stopped: memory, not slowness

GB10 memory is unified (121 GB shared by CPU and GPU). At 10:00 AEDT, 118 GB was in
use: the Qwen base vLLM (13 GB GPU), the then-persistent llama-swap Granite vLLM
(26 GB), and llama-swap's `qwen3.8-flash-next` llama-server loading. At **10:02:16**
the kernel's global OOM-killer killed that llama-server (pid 1026915), and pi's
requests through llama-swap returned HTTP 500 (`upstream command exited
prematurely`). Measured afterwards in isolation: `qwen3.8-flash-next` alone takes
≈96 GB used, leaving ≈21 GB available. At the time neither `training/run_comparison.py` nor
`training/granite/train.py` checked available memory before allocating (the
driver now does), so Granite
training (≥16.8 GB of weights alone) and a 0.22-fraction Granite eval backend
(≈26 GB) would very likely have been OOM-killed while flash-next was resident.

## llama-swap changes (applied 10:05–10:10 AEDT)

1. **Granite backend is on demand.** `configs/powercoder/llama-swap/granite42-vllm.container`
   no longer has `[Install] WantedBy=llama-swap.service`; the installed copy
   `~/.config/containers/systemd/granite42-vllm.container` matches it. It still
   stops with the gateway (`PartOf`). Start it only when needed:
   `systemctl --user start granite42-vllm.service`. Until then, `granite-4.2-8b`
   requests through the gateway fail its `/health` check. It is currently inactive.
2. **Idle unload.** `~/.config/llama-swap/config.yaml` gained `globalTTL: 600`
   (backup `config.yaml.bak-ttl-20261021`). Before, `/running` reported `"ttl":0`
   (never unload). The existing `unloadTimeout: 60` key is not an idle timeout.
   After restart `/running` reports `ttl: 600` for `qwen3.8-flash-next`.
3. `llama-swap.service` restarted; a real `qwen3.8-flash-next` chat returned
   HTTP 200 `ready` (cold load ≈80 s). `granite42-vllm.service` exited 137 when
   stopped (SIGKILL after stop timeout) and was `reset-failed` to inactive.
   The unrelated `llama-vllm-27b.service` remains inactive; leave it so.

Persistent proxy backends (`llama-vllm-27b`, `granite42-vllm`) are systemd units
that `globalTTL` cannot unload; they must be stopped explicitly before
memory-heavy PowerCoder stages.

## r6 — fixed tools, retrained students, stratified sample (in progress)

> **Read before quoting any r6 number (docs/backlog/0008).** The benchmark uses one network and one
> fit interval; 36 of 69 task keys also occur in training; and every task is solved by the ~30-line
> policy the students imitate. A gain shows the LoRA learned the tool protocol, not agent value.
> Gates: `domain_threshold_met` (point estimate) and `domain_threshold_met_ci_lower` (paired 95%
> lower bound). The strict-tool-calling arm runs as a 2×2 on the smoke suite after r6 (0011). Lab 10 runs
> as a 5-scenario pilot. Recovery training no longer supervises the injected mistake (`8314e14`,
> 0009); training restarted 00:22 on 2026-10-23.

Goal set 2026-10-22: (1) fix FM-17/FM-18; (2) mixed, stratified task order;
(3) retrain both students on tool-call examples behind a 20-task smoke gate;
(4) evaluate all four models on ~100 stratified tasks; (5) measure latency and
peak memory separately; and consider a separate PowerAgentBench lab (Lab 10).

| Piece | Where | State |
|---|---|---|
| FM-17/FM-18 fixes | `powercoder/inference/studies.py` (+ `test_studies.py`) | Done |
| 100-task sample | `powercoder/datasets/sample_benchmark.py` → `benchmarks/power-agent-bench-lite/results/domain-stratified-100-20261022/` (sha256 `e0ae550f…`, 25 per subfamily, interleaved) | Done |
| 20-task smoke set | validation split minus every task shared with training (FM-21), `training/unsloth/out/smoke-validation-20-20261022/` (sha256 `566885d9…`; 7 load / 7 line / 3 dispatch / 3 report; audit clean) | Done |
| Trajectories | `powercoder/datasets/trajectories.py` → `training/unsloth/out/trajectories-20261022/` (238 kept / 238, all pass `grade()`; sha256 `e20982a4…`) | Done |
| Trainer | `training/agent_sft.py`, configs `training/agent/config.{qwen35-4b,granite42-8b}.yaml`, image `training/agent/Containerfile` (+fla 0.5.2 → `c890642db172`) | Retraining both after the seam-token fix |
| Training unit | `./scripts/launch_agent_sft.sh` → `powercoder-agent-sft` | Running |
| Driver | `training/run_agent_study.py`, `./scripts/launch_agent_study.sh <root>` | Written, tested on CPU |
| Latency | `powercoder/inference/latency_bench.py` (20 smoke first turns, 512 tokens, `ignore_eos`, concurrency 1, idle server) | Written |
| Lab 10 | `labs/10-poweragentbench-restorebench/` | CPU steps done; agent stage runs inside r6 |

**Structured generation (xgrammar, checked 22:50–22:57).** xgrammar
(Apache-2.0, mlc-ai) is vLLM 0.31's structured-output backend here (0.2.7, with
llguidance 1.7.6); llama.cpp uses its own GBNF engine instead, so the llama-swap
llama.cpp server needs no xgrammar change. Measured on CPU with the vLLM image:
xgrammar's built-in `qwen_3_5` structural tag (the Qwen XML tool-call format,
which Granite's template also emits) accepts all 2,207 trained turns of the
357 trajectories and rejects 70 of the 119 injected failures (every FM-7
`"None"` and FM-1 `run_power_flow` call); the 49 line-0 calls are schema-valid,
so only training addresses FM-18. That check forced trajectory arguments into
schema order (`a9fe73d`). The forced arm needs no grammar of its own (an
earlier committed grammar file, tool snapshot and drift check were removed as
tech debt): vLLM 0.31's unified parser builds xgrammar's model-native
structural tag per request from that request's tools. Measured live on the v1
Qwen student: `tool_choice="required"` produced native Qwen XML (vLLM's grammar
drops the two newlines after `</think>`), and a server started with
`--tool-strict-level parameter` accepted the flag and did the same with the
tools unchanged. The forced arm runs that way: strict servers, turn 0
`required`, later turns `auto`, prompt unchanged.

**Sorted-tool-schema defect (22:50).** Training prompts rendered the tool block
with sorted keys (`common.write_jsonl`), unlike the served prompt (AGENTS.md
FM-23). The v2 run was stopped at ~step 10/45 with the waiters disarmed first;
trajectories are rebuilt with `write_in_order` and both students retrained. The
v1 Qwen student below was trained with sorted tools too.

**Live state (22:57 AEDT, superseding 22:15).** Training relaunched on the
schema-ordered, served-order v2 trajectories (`f89074d4…`, commit `a9fe73d`).

**Earlier live state (22:15 AEDT).** The seam-fixed Qwen student trained on the
238 plain trajectories (`training/unsloth/out/agent-sft-qwen35-4b-20261022/`,
30 steps, 1.9 h, final loss 0.0027) passed `generation_check` 8/8: from the
served prompt it closes `<think>` and opens a `run_study_trial` call. It is
kept as the plain-procedure baseline. On user direction both students are now
retrained on `trajectories-20261022-v2` (238 plain + 119 failure-mode recovery
variants: 61 `"None"`, 49 line-0, 9 `run_power_flow`; all pass `grade()`
including provenance) into `agent-sft-*-20261022-v2/`, which r6 evaluates.
`powercoder-agent-study-r6-wait` launches r6 when that training succeeds. When r6 records
`pipeline_complete`, `powercoder-agent-study-r6-forced-wait` launches the
separate forced first-turn tool-call arm (`training/unsloth/out/agent-study-r6-forced`,
`run_agent_study --forced-arm-from`), which compares each model with itself.
Watch with:

```bash
podman logs --tail 3 powercoder-agent-sft-qwen35-4b 2>&1 | tr '\r' '\n' | tail -2
cat training/unsloth/out/agent-sft-*-20261022-v2/generation_check.json
systemctl --user status powercoder-agent-study-r6-wait powercoder-agent-study-r6
tail -3 training/unsloth/out/agent-study-r6/pipeline.jsonl
```

Plumbing verified before launch with the defective student as a stand-in:
vLLM serves the merged Qwen3.5 checkpoint; `latency_bench` measured TTFT
0.40 s and 8.5 tok/s (GPU shared with training); Lab 10's local transport
reached vLLM after capping responses at 8,192 tokens (RestoreBench's 16,384
plus its ~43k-character IEEE 118 first turn overran the 32,768 context, so the
first attempt recorded `LLM_FAILURE` on every call).

Training facts (preflight, both students): 238 conversations as 1,457 turn
sequences (each = separately tokenised served prompt + target), 30 optimizer
steps (8 conversations ≈ 49 sequences a step, 1 epoch, LR 1e-4, LoRA r32 on
decoder linears): Qwen 158,855 and Granite 160,127 supervised tokens. Image
`localhost/powercoder-agent-sft:local` (`c890642db172`) adds fla 0.5.2: the
reference Gated-DeltaNet kernels ran ~125 s/step and held ~93 GB.

**Seam-token defect (found and fixed 2026-10-22 ~20:00).** The first complete
Qwen student (`training/unsloth/out/agent-sft-qwen35-4b-20261022-defect-seam-token/`,
kept as evidence) reached teacher-forced loss 0.0006, yet served by vLLM it
answered reports with invented numbers and no tool call, and opened fits with
long reasoning. Measured cause: prompt and target were tokenised as one string;
Qwen's prompt ends `<think>\n` and its target starts `\n</think>`, and the two
newlines merge into one `\n\n` token, so training never contained the served
prompt's final token. vLLM's `/tokenize` and the HF render of the served prompt
are identical (1,074 tokens), so serving was not at fault. `agent_sft.encode`
now concatenates separately tokenised prompt and target, and the trainer's
post-merge `generation_check` (8 first turns, 32 tokens each, greedy) refuses
to finish otherwise; on the defective student it reported 0/8. Granite's
seam (`<think>\n` + `</think>`) does not merge (supervised tokens unchanged),
but its partial run was discarded too, and training was cut to one epoch
(Qwen's loss was 0.0016 by epoch 0.67).

Incident ~19:05: a full `pytest tests/` run hit `tests/integration/test_teacher_gateway.py`,
which made llama-swap start `qwen3.8-flash-next`; the kernel OOM-killed
llama-swap's model process. Training survived. Don't run the gateway tests
during GPU stages.

Lab 10 result: RestoreBench witnesses on this aarch64 node score IEEE 118
45/46 (S0081 `BUDGET_EXHAUSTED`, reproduced) and PEGASE 89 46/46; its own
verifier agrees. Reported as PowerAgentBench#13; the batch scorer's fixed IEEE 118 data
directory as #14.

## r5 run — stopped deliberately 2026-10-22 17:46 AEDT

Unit `powercoder-agent-domain-r5`, root `training/unsloth/out/agent-domain-r5/`,
launched 15:50 with `--qwen-from training/unsloth/out/agent-domain-r4` (Granite only,
one server at a time, same budgets as r4). Stopped by `systemctl --user stop`; the
journal recorded `driver_stopped` (exit 143), no containers or evaluators remained.
Granite student was never evaluated.

Granite base reached 34/306 (`failure_modes`): reports 15 `solver_correct_strict_rejected`,
11 `answered_wrong`; load-scale fits 7 `turn_budget_exhausted`, 1 `response_token_cap`.
Solver-verified reports 15/26 vs Qwen base 4/26; strict 0/34. Fits ran ~40 min each
(12 turns × 130–370 s).

Why stopped: the remaining 272 tasks are fits measured through benchmark defects
that a new run root must fix, so the result would be re-run anyway:
- every `run_study_trial` on snem-sa-2000 returns `constraint_violation` (r4 Qwen
  base 1045/1045, r5 Granite 77/77) because the base case already has 39 bus-voltage
  violations — the status carries no signal and models retry as if it failed;
- `contingency_line: 0` is a valid line index, so "no contingency" written as `0`
  silently outages line 0 (Granite 66 of 104 calls) — FM-1 in a new form;
- students are trained on `{prompt → expected.answer}` text with no tools, a
  different system prompt and `seq_len: 1024` (`training/unsloth/train.py`), then
  evaluated as multi-turn tool agents — the likely cause of the 0% Qwen student.

Next: fix those in a new root, interleave/stratify task order, retrain students on
tool-call trajectories behind a 20-task smoke gate, evaluate all four models on a
stratified sample, and measure latency/peak memory in a dedicated benchmark.

## r4 results (Qwen complete; Granite crashed)

Qwen stage finished 2026-10-22 00:50 AEDT; both runs 306/306, identical input
hashes and budgets (`agent-domain-r4/{base,student}/summary.json`).

| Metric | Qwen base | Qwen student | Δ (95% paired bootstrap) |
|---|---|---|---|
| Strict agent pass@1 (`qwen-comparison.json`) | 3.9% (12) | 0.0% (0) | −3.9 pp [−6.2, −2.0] |
| Solver-verified pass@1 (`qwen-numerical-comparison.json`) | 5.6% (17) | 0.0% (0) | −5.6 pp [−8.2, −3.3] |

The ≥10 pp gate is **not met**, and is recorded as missed; budgets and scoring
are unchanged. `failure_modes` on the student: 252 `response_token_cap` (the FM-16
loop), 52 `empty_stop`, 2 `turn_budget_exhausted`. Base: 190
`response_token_cap`, 93 `turn_budget_exhausted`, 12 pass, 5
`solver_correct_strict_rejected`, 4 `invalid_tool_protocol`, 2 `answered_wrong`.

Granite stage started 00:55 with both 8B servers side by side and failed at 04:41:
one request exceeded the 1800 s timeout (`httpx.ReadTimeout`), aborting both
evaluators at 26 (base) and 30 (student) of 306. Partial early signal, reports
only: Granite base was solver-correct on 17/26 reports (all strict-rejected on
wording), against Qwen base's 4/26. Too small and too early to be a result.

## r4 run (launch record)

Launched 13:51 AEDT as transient unit **`powercoder-agent-domain-r4`** from
`f3bab24`, root `training/unsloth/out/agent-domain-r4/`. It differs from r1–r3 in
the AGENTS.md "Fixed from r4 on" list: FM-1 unknown tool arguments rejected,
8192-token responses, 32K context, 1800 s timeout, no wall-clock values shown to
the model, context overflow recorded as `context_exhausted`. It also runs 8
concurrent attempts per server, with base and student side by side, so **r4
results are not comparable with r1–r3**.

Measured at start:
- Memory gate: the first server waited (23.2 GB < 29.5 GB needed) while
  llama-swap's flash-next was resident, then was admitted at 127 GB once the
  600 s TTL unloaded it.
- vLLM 0.31's overflow text was confirmed live on :8101 ("This model's maximum
  context length is 32768 tokens…"), matching `tool_probe.CONTEXT_OVERFLOW`.
- Throughput: 54–58 tok/s per server with Granite training alongside, then
  66–70 tok/s per server (8 running each) after training: ≈ 7× r3's 19 tok/s
  single stream.
- **Granite training complete at 14:25** (stage exit 0): 100 steps in 713 s,
  273 examples, 18,601 supervised tokens, `benchmark_access: false`, loss 2.19
  (step 10) → 0.15 (step 100). Adapter SHA256 `0ba1cd6e…7f1`; reloaded and merged
  into `training/unsloth/out/granite42-sft-r1/merged-bf16/`, with
  `hash_manifest.json` and `preflight.json`.
- **Qwen student degenerate loop (FM-16).** All 8 student tasks finished by
  14:25 ended `incomplete_generation`. On turn 1 the student repeats the same few
  reasoning lines 40–60 times, debating `run_power_flow` vs `run_study_trial`,
  until it hits 8192 tokens; or it stops with reasoning and no content. The base
  model answered normally (median 620 tokens per turn). This is a model result;
  it is not patched.

```bash
systemctl --user status powercoder-agent-domain-r4
tail -3 training/unsloth/out/agent-domain-r4/pipeline.jsonl
wc -l training/unsloth/out/agent-domain-r4/{base,student}/results.jsonl
uv run python -m powercoder.inference.failure_modes training/unsloth/out/agent-domain-r4/base
```

`results.jsonl` is committed in benchmark order, so it lags the `ex_*.jsonl`
journals while earlier tasks are still in flight. Stop only with
`systemctl --user stop powercoder-agent-domain-r4`. Never relaunch into an
existing root.

### Earlier runs (evidence only)

- r3 (`agent-domain-r3/`, unit launched 10:35:48 from `a279d7b`): 48/306 base
  results, then stopped by the user's decision at 13:43:57 because it was measuring
  known harness defects. Its stop left no journal record (FM-15). The first launch
  attempt is kept as `agent-domain-r3-attempt1-readiness-reset/` (FM-12).
- r3 vs r2 task 1 was the reproducibility finding (FM-3): identical until the
  solver's `execution_time_s` differed, then the trajectories diverged.

## Sequence and acceptance evidence

`training/run_comparison.py` (r4) requires complete, consistent 306-task outputs
before advancing:

1. Verify the pinned Granite download. Serve Qwen base (:8101) and the original
   Unsloth student (:8102) side by side, and evaluate both into `base/` and
   `student/` while Granite trains for 100 steps on the same 273 examples and its
   saved adapter is reloaded and merged. Then write paired strict and numerical
   Qwen comparisons.
2. Serve and evaluate Granite base and student side by side into `granite-base/`
   and `granite-student/`, then compare Granite with its base and with both Qwen
   variants.

Strict comparison files: `qwen-comparison.json`, `granite-comparison.json`,
`granite-vs-qwen-base.json`, `granite-vs-qwen-student.json`. Numerical
counterparts: `qwen-numerical-comparison.json`, `granite-numerical-comparison.json`,
`granite-vs-qwen-base-numerical.json`, `granite-vs-qwen-student-numerical.json`.
`compare_agents.py` reports pass@1 with paired uncertainty; it does **not** report
latency or peak memory, which the goal's Granite comparison asks for.

Before declaring success, verify all four complete 306-task runs, their task/input
hashes and equal budgets, raw attempt journals, final summaries, paired reports,
and actual Granite training/reload/merge receipts. Report the measured gain and
uncertainty. **At least 10 percentage points is a requirement to assess, not a
result to assume.** If the experiment misses it, report that and scope further
work without changing the completed benchmark retrospectively.

The strict agent metric requires matching real tool/study evidence as well as a
correct final answer. The separate `solver-verified-domain-v1` numerical metric in
`powercoder/inference/domain_scorecard.py` regrades saved terminal answers with
fresh physical solves; it issues no new model attempts and gives no feedback to
models. Report both metrics distinctly. Numerical success does not establish zero
fabrication or full G5; `g5_met` remains false without the remaining
general-Python/critical-fabrication suites. Smoke receipt (one example, not a
benchmark): `agent-domain-r2/numerical-grader-smoke.json`. Base attempts include
invalid tool requests; preserve those as model failures. Do not alter prompts,
tool schemas, scoring criteria, or generation budgets mid-run.

## Inputs, checkpoints, and training lineage

- Held-out tasks:
  `benchmarks/power-agent-bench-lite/results/domain-held-out-20261020/examples.jsonl`.
  SHA256 `f2523a141ca1f3160617647b595e2b3a7cfbd16c8bded70caf0a4430fed29bca`.
  Counts: 26 reports, 120 load-scale fits, 120 line-rating fits, 40 dispatch fits.
  Independent prompts/concrete inputs were audited against all original 500
  examples; this is not a held-out-topology/template experiment.
- Excluded original source: `powercoder/datasets/smoke/examples.jsonl`, SHA256
  `bb035acdd5760cab5338dec03893c69bed0590793f108805da34c02acc7f00b5`.
  Training split: `powercoder/datasets/smoke/splits/train.jsonl` (273 rows).
- Fixed evaluation budgets: one greedy attempt, temperature 0, at most 12 model
  turns, 24 tool calls, 2048 tokens per response, and 600-second HTTP timeout.
  Actual canonical MCP stdio calls and fresh study solves are recorded in
  `ex_*.jsonl`; results accumulate in `results.jsonl`.
- Qwen base revision: `Qwen/Qwen3.5-4B` at
  `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`.
  Use the **original Unsloth** student in
  `training/unsloth/out/merged-bf16`, with adapter in `qwen3.5-4b-lora`.
  Do not substitute the separate Axolotl reproduction.
  Adapter SHA256: `d749dd82e787625e5a217c8092eec06603a58c69236ff3cbf5a9385f64cde319`.
  Merged weights SHA256: `84a4ab6d5f39b17c38b4046444ec708d230ea642d5191a510beccaa3e0bee20f`.
  Thirteen adapter/merged files were rechecked without mismatches; receipt:
  `training/unsloth/out/agent-domain-r2/qwen-student-integrity.json`.
- Granite: `ibm-granite/granite-4.2-8b`, Apache-2.0, revision
  `b3791992239dce12d0038f1f54a8adb7b02d59ab`.
  Download: `training/unsloth/out/granite-4.2-8b-base`, four shards totaling
  16,761,102,336 weight bytes; all shard hashes matched pinned metadata.
  Receipt: `training/unsloth/out/granite42-download-verified.json`.
- Granite training implementation: `training/granite/train.py`; configuration:
  `training/unsloth/config.granite42.yaml`; guide: `training/granite/README.md`.
  Real Transformers/PEFT CUDA training, not Unsloth model patches. Same 273 rows
  and Qwen hyperparameters: 100 steps, BF16, LoRA rank 32/alpha 64, all-linear,
  dropout 0, effective batch 4, warmup 10, learning rate 2e-5, seed 42, sequence
  length 1024, AdamW 8-bit, cosine, weight decay 0.01. All 273 native Granite
  tokenizations passed supervision checks; maximum 281 tokens. **Trained in r4**
  (2026-10-21 14:13–14:25, see "Live r4 run") into
  `training/unsloth/out/granite42-sft-r1/` (`granite42-8b-lora/`, `merged-bf16/`,
  `train_log.json`, `preflight.json`, `hash_manifest.json`); log
  `agent-domain-r4/granite-training.log`. The script refuses existing output, so a
  rerun needs a new output directory.
- vLLM image ID
  `d53cb735dd1822378c352da65b4ef1580c9764df7aed4bc8805c6c26f6b827de`
  (`localhost/vllm-lmcache:gb10`, vLLM 0.31.0). Training image ID
  `74feb3e2e84cfde5d1d2d21c34213d60114d7eb7a80bb1f9352ff2dda15c00ff`
  (`localhost/unsloth-gb10-eval:local`). Qwen eval backends use GPU memory
  fraction 0.12; Granite uses 0.22, BF16, context 16384, one sequence, eager
  execution (0.12 was insufficient for its weights).

Models, datasets, training outputs, and raw evaluation artifacts are intentionally
gitignored. A fresh clone alone cannot resume this experiment; these paths are
local to this host. Preserve historical failed/partial diagnostics, including
`agent-domain-r1/`, `agent-domain-r2/` and `eval306-r1-raw/`.

## Granite through llama-swap

Endpoint `http://127.0.0.1:8000/v1`; model ID `granite-4.2-8b`, alias
`ibm-granite/granite-4.2-8b`. Source files and guide: `configs/powercoder/llama-swap/`;
registry entry: `configs/powercoder/models.yaml`. Installed:
`~/.config/llama-swap/config.yaml` (backups `config.yaml.bak-granite42-20261021`,
`config.yaml.bak-ttl-20261021`) and
`~/.config/containers/systemd/granite42-vllm.container` (unit
`granite42-vllm.service`, container `llama-vllm-granite42`, private port 18102 in
the gateway's network namespace, pinned local weights/image, offline). Now on
demand — see "llama-swap changes" above.

Verified earlier the same morning, while the backend was persistent: real chat
returned “Granite is ready.”; an alias request produced a parsed `echo` tool call
with `{"message":"ready"}`. These verify serving/tool parsing, not domain accuracy.
Receipts: `training/unsloth/out/granite42-llama-swap-smoke.json` and
`granite42-llama-swap-tool-smoke.json`.

## Committed implementation and validation

- `bbad284`: complete held-out agent evaluator, integrity checks, pinned Granite
  experiment; durable raw attempts and explicit failure outcomes.
- `c416703`: sequential driver and verified Granite training lineage.
- `d3b9b7b`: Granite llama-swap deployment and verification.
- `8442962`: separate solver-verified numerical scorecard and scheduled reports.

Last recorded relevant checks: **24 passed in 8.17 seconds**:

```bash
uv run --offline --no-sync python -m pytest \
  powercoder/inference/test_domain_scorecard.py \
  powercoder/inference/test_compare_agents.py \
  powercoder/inference/test_evaluate_agent.py \
  training/unsloth/test_evaluation.py \
  tests/integration/test_canonical_mcp_transport.py -q
```

These include a real AC solve on an explicitly named small fixture and actual MCP
stdio transport. They validate implementation, not student improvement. r4 added
`powercoder/inference/test_failure_modes.py`, the ordered-concurrency test in
`test_evaluate_agent.py`, and the parallel-stage test in
`training/test_run_comparison.py`. The full pipeline has not yet completed end to
end.

Next action: let r4 advance and audit each completed stage from `pipeline.jsonl`
and `failure_modes`. FM-16 (student repetition loop) is a measured model result to
report, not something to patch mid-run. Any benchmark change means a new run root.
Follow `AGENTS.md`,
`docs/VISION.md`, and `docs/DEFINITION_OF_DONE.md`; use `uv`, preserve real
evidence, and report exact tool/host environment mismatches without silently
routing around them.
