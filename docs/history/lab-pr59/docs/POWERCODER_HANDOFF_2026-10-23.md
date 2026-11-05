# PowerCoder handoff — 23 October 2026

Snapshot: **2026-10-23 00:40 AEDT (UTC+11)**. Branch `chore/powercoder-recovery`, head `9154cc4`, tree
clean, **nothing pushed**. This document supersedes
[`POWERCODER_HANDOFF_2026-10-21.md`](POWERCODER_HANDOFF_2026-10-21.md), which is kept for the r1–r6
history.

## 1. What this study is, and the caveat to read first

The study compares Qwen3.5-4B and Granite 4.2 8B, each as a base model and as a LoRA-trained
"student", acting as tool-using agents on a pandapower power-flow benchmark. A task is either a report
(run one study and report its results) or a fit (search a scale factor until a measured value hits a
target). The original goal was a gain of at least 10 pp in domain pass@1 for each student over its
base.

**Before quoting any r6 number, read [backlog 0008](backlog/0008-powercoder-benchmark-cannot-measure-agent-value.md).**
- **One network, one fit interval:** every benchmark and training task uses `snem-sa-2000`, and every
  fit uses the interval [0.9, 1.1].
- **Overlap with training:** 36 of the 69 benchmark task keys (subfamily plus element) also appear in
  training, with different targets.
- **Scripted solutions:** every task is solved by a ~30-line procedure, and the students are trained
  to imitate that procedure.

An r6 gain therefore shows that a LoRA learned the tool protocol, not that an agent adds value over a
script. The gate is recorded both on the point estimate (`domain_threshold_met`) and on the paired
95% interval's lower bound (`domain_threshold_met_ci_lower`).

## 2. What is running now

> **Update 07:45.**
> - **Training finished:** both students passed `generation_check` 8/8. Qwen took 2.7 h (loss
>   0.0023); Granite took 3.4 h (loss 0.0025).
> - **The r6 waiter never launched (FM-24):** no `uv` on the systemd unit's PATH, fixed in `d73370b`.
> - **The manual r6 smoke stage exposed FM-25:** 8 of the 25 benchmark reports and 2 of the 3 smoke
>   reports were unsolvable through the tool. First smoke results: Qwen student 15/20, Granite
>   student 9/20, both including those 2 impossible reports.
> - **The first r6 (`agent-study-r6/`) was stopped and isn't a result.** It's re-running as **r6b**,
>   unit `powercoder-agent-study-r6b`, root `training/unsloth/out/agent-study-r6b/`, from `1259ae1`.
>   The strict 2×2 follows via `powercoder-agent-study-r6b-strict-wait`.
> - **The table below names the old units.** Read `r6` as `r6b` throughout.

| Unit | State | Does |
|---|---|---|
| `powercoder-agent-sft` | running | Trains the Qwen student (46 steps, about 4 min per step, started 00:22), then Granite. Each merge must pass `generation_check`, or the unit fails |
| `powercoder-agent-study-r6-wait` | waiting | Launches **r6** (`./scripts/launch_agent_study.sh training/unsloth/out/agent-study-r6`) only if the training unit's result is `success` **and** `agent-sft-granite42-8b-20261022-v2/hash_manifest.json` exists |
| `powercoder-agent-study-r6-strict-wait` | waiting | When r6's `pipeline.jsonl` ends with `pipeline_complete`, launches the **strict-tool-calling 2×2** (`… agent-study-r6-strict --strict-arm`). If r6 ends with `driver_error` or `driver_stopped`, it exits instead |

**Expected timing** (estimates, not measurements): the Qwen merge around 03:30, Granite around 07:00,
then r6 for about 15–25 h. Most of that time is the base models' slow fit tasks: about 40 min per fit
at 8 concurrent attempts. The strict 2×2 follows r6.

**Watch:**
```bash
podman logs --tail 3 powercoder-agent-sft-qwen35-4b 2>&1 | tr '\r' '\n' | grep -oE "'loss'.*'epoch': '[^']+'" | tail -1
cat training/unsloth/out/agent-sft-*-20261022-v2/generation_check.json   # 8 checks, each "matches": true
systemctl --user status powercoder-agent-study-r6-wait powercoder-agent-study-r6 powercoder-agent-study-r6-strict-wait
tail -3 training/unsloth/out/agent-study-r6/pipeline.jsonl
free -g   # GB10 unified memory: ~80 GB available during Qwen training
```

**Stop safely, in this order.** Stop the waiters first. A training unit stopped by hand can report
`success`, and the r6 waiter would then launch against students that don't exist:
```bash
systemctl --user stop powercoder-agent-study-r6-wait powercoder-agent-study-r6-strict-wait
systemctl --user stop powercoder-agent-sft        # ExecStop stops its containers first
```

## 3. What to do at each trigger

1. **Qwen's `generation_check.json` appears.**
   - **8/8:** nothing to do. Granite trains next.
   - **Fewer than 8:** the unit fails, and neither r6 nor the strict arm launches. Read the
     `expected`/`generated` pairs and stop there.
2. **Granite's check appears.** Same rule. On success, r6 starts automatically.
3. **r6 smoke gate.** Each student needs ≥10/20 on the validation smoke set (`smoke_gate` events in
   `pipeline.jsonl`). A student that fails isn't evaluated further, and the comparisons that need it
   are recorded as skipped.
4. **r6 completes.** Before quoting results:
   - **Re-check 0009's fix:** count calls in the student journals that use `contingency_line: "None"`
     or `0`, or `run_power_flow` with a contingency. A non-zero rate on tasks that don't need them
     means the fix didn't hold.
   - **Read the comparisons** `*-vs-*.json` and `*-numerical.json`, together with
     `domain_threshold_met_ci_lower`.
   - **Report latency and memory** from `<label>-latency.json` and the `memory_*` events.
     Tokens/s is per tokenizer, so compare wall time across models.
5. **The strict 2×2 completes.** Read `*-strict-*-vs-baseline.json`. It's a 20-task pilot for
   direction, not a gate.

## 4. Inputs (all verified by hash before use)

| Input | Path | Identity |
|---|---|---|
| 100-task benchmark sample: 25 per subfamily, interleaved, seed 20261022 | `benchmarks/power-agent-bench-lite/results/domain-stratified-100-20261022/` | `e0ae550f…`; independence audit clean |
| 20-task smoke set from the validation split, minus every task shared with training (FM-21) | `training/unsloth/out/smoke-validation-20-20261022/` | `566885d9…`; 7 load / 7 line / 3 dispatch / 3 report |
| Training trajectories: 238 scripted solver runs plus 119 failure-mode recovery variants, all passing `grade()` with provenance, stored in served key order with schema-ordered arguments | `training/unsloth/out/trajectories-20261022-v2/` | `f89074d4…`, built at `a9fe73d`, `git_dirty: false` |
| Training image (pinned training image + flash-linear-attention 0.5.2) | `training/agent/Containerfile` → `localhost/powercoder-agent-sft:local` | `c890642db172` |
| Inference image (vLLM 0.31, xgrammar 0.2.7, llguidance 1.7.6) | `run_comparison.INFERENCE_IMAGE` | `d53cb735dd18` |

Students go to `training/unsloth/out/agent-sft-{qwen35-4b,granite42-8b}-20261022-v2/`. Both use the
same recipe:
- 1 epoch at LR 1e-4, LoRA r32 on the decoder linear layers;
- 8 conversations per step: 46 steps over 2,207 supervised turns;
- each turn trained as the served prompt and the target tokenised separately;
- injected mistakes are context only, never targets.

Two earlier students are kept as evidence and must not be evaluated as r6 students:
- `…-20261022-defect-seam-token/`: FM-22.
- `…-20261022/`: seam-fixed, trained before FM-23 was found. It passed `generation_check` 8/8, and 3
  live validation fits passed.

## 5. What changed since r5 (commits `3d4996d`…`9154cc4`)

All of these are committed and tested: 102 tests pass across `powercoder`, `training`, Lab 10 and the
study-tool integration test.
- **Study tools:** responses carry `metric_valid` and `base_case_voltage_violations` (FM-17).
  `contingency_line` is optional, and each response names the line taken out (FM-18).
- **Provenance (PC-REQ-1):** every number in an answer must trace to a tool response or the prompt.
  Otherwise the attempt is `fabricated` and fails, in both the strict score and the
  solver-verified-traced-v2 score. It's weaker than the requirement; see backlog 0010.
- **Training pipeline:** `powercoder/datasets/trajectories.py` (scripted, graded trajectories and
  recovery variants), `training/agent_sft.py` (one recipe for both models, `generation_check`), and
  `scripts/launch_agent_sft.sh`.
- **Study driver:** `training/run_agent_study.py` and `scripts/launch_agent_study.sh`. It runs:
  - the smoke gate;
  - an idle-server latency benchmark (`powercoder/inference/latency_bench.py`);
  - host memory sampling;
  - the 100-task evaluation of all four models, one server at a time;
  - every pairwise comparison;
  - the Lab 10 RestoreBench pilot (5 IEEE 118 scenarios per model).
- **Strict tool calling (PC-REQ-2):** `--strict-arm`, as a 2×2 of server `--tool-strict-level` ×
  first-turn `tool_choice`. Runs record both settings, and `compare_agents.CONDITIONS` refuses a
  comparison that doesn't name every setting that differs.
  - **No grammar is stored here:** vLLM builds xgrammar's model-native Qwen-XML tool grammar per
    request from the request's own tools.
  - **Measured live:** forced replies were native XML. vLLM drops the two newlines Qwen otherwise
    writes after `</think>`.
- **Lab 10** (`labs/10-poweragentbench-restorebench/`): PowerAgentBench's RestoreBench, the only part
  of the repo with a licence, fetched at a pinned commit and run in its own locked environment through
  a named stand-in transport.
  - **Witness check:** IEEE 118 verifies 45/46 on aarch64 (S0081) and PEGASE 89 46/46.

## 6. Defects found and fixed on 2026-10-22/23

All are recorded in AGENTS.md (FM-17–FM-23) or the backlog. These are the ones that would have
invalidated r6:

| ID | Defect | How it was caught |
|---|---|---|
| FM-19 | r4 students were trained on prompt → answer text with no tools | Code read after r4 scored 0/306 |
| FM-20 | Reference fit answers fail the benchmark's own scorer | A scripted trajectory failed `grade()` |
| FM-21 | The validation split shares report tasks with training | `audit_rows` refused the smoke set |
| FM-22 | Prompt and target tokenised together merged `\n\n` at the seam, so the student answered without tools | Live probe; teacher-forced loss ~1e-5 hid it |
| FM-23 | Training prompts rendered tool schemas with sorted keys | Byte comparison with the served prompt |
| — | Arguments not in schema order (rejected under a grammar) | xgrammar acceptance check over all trajectories |
| 0009 | Recovery training supervised the injected mistake | Independent merit review (B1) |
| — | A stale grammar raised `ValueError` inside an attempt, which would be scored as a model failure | User review of the structural-tag test (later removed with the grammar file) |

## 7. Decisions on record

- **Grammar engines ([0014](backlog/0014-grammar-engines-xgrammar-llguidance-gbnf.md)):**
  - **xgrammar is kept, in sync with vLLM**, because it measurably works there.
  - **llguidance or GBNF is the llama.cpp path.** vLLM's llguidance backend can't express the Qwen-XML
    tool format, and llama-swap's `llama-server` is built without LLGuidance.
  - **Recorded in b00t:** `b00t learn xgrammar`.
- **The forced arm runs only as the 2×2**, never as a single confounded arm.
- **Lab 10 runs as a 5-scenario pilot per model.** All 46 would add about 7 h for a signal the study
  doesn't gate on.
- **Upstream reports:** PowerAgentBench [#11](https://github.com/Power-Agent/PowerAgentBench/issues/11)
  (no root licence), [#13](https://github.com/Power-Agent/PowerAgentBench/issues/13) (S0081 on
  aarch64), [#14](https://github.com/Power-Agent/PowerAgentBench/issues/14) (the batch scorer has no
  data directory).

## 8. Open work, by merit

From the [independent merit review](backlog/reviews/2026-10-23-r6-merit-review.md), indexed in
[`backlog/README.md`](backlog/README.md):
1. **[0008](backlog/0008-powercoder-benchmark-cannot-measure-agent-value.md)**, agent-value baselines
   and judgement tasks. Cheapest decisive test: each base model with the procedure in its prompt, on
   the 20 smoke tasks.
2. **[0009](backlog/0009-recovery-training-supervised-the-injected-mistake.md)**, journal check after
   r6.
3. **[0010](backlog/0010-provenance-check-hardening.md)**, provenance hardening, and recording serving
   flags in every manifest.
4. **[0012](backlog/0012-answer-by-reference.md)**, answer by reference. Start with a GPU-free
   ambiguity count.
5. **[0013](backlog/0013-sensitivity-newton-tool.md)**, sensitivity/Newton tool. PCA and the
   one-interface generator are dropped.

## 9. Rules that bit this session

- **Don't run `pytest tests/` as a whole during a GPU stage.** `tests/integration/test_teacher_gateway.py`
  makes llama-swap load a ~96 GB model, and the OOM killer fired on 2026-10-22. Name test paths
  explicitly.
- **Don't run `pkill -f <pattern>` or `pgrep -f` from a shell whose command line contains the
  pattern.** It kills that shell. Kill by PID.
- **`serve()` stops containers but doesn't remove them.** Reuse a container name only after
  `podman rm`.
- **The kr0ki at `sm3lly.lan:8787` (v0.1.1) has no Mermaid companion,** so it refuses Mermaid. The
  user notes that real kr0ki outputs carry a signature. Don't present a rendered SVG as a kr0ki output
  unless it has one.
- **Don't touch llama-swap or the GGUF services (OPEN-001).** The user runs sudo; never run
  `b00t soul reset`.
