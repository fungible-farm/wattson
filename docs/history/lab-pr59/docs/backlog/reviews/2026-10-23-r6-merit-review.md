<!-- Independent review written 2026-10-23 by a separate AI reviewer agent (read-only, not the author of the work), from the repo at commit a7755cd. Kept unchanged as the evidence behind backlog items 0008-0014. Since then: B1 fixed (8314e14), B3 recorded as domain_threshold_met_ci_lower and B7 mitigated (see 0010), the forced arm disarmed pending 0011, Lab 10 cut to a 5-scenario pilot. -->
# Independent merit review: r6 PowerCoder design and proposed ideas

Reviewer stance: skeptical and independent. I did not write this work. Repository at `chore/powercoder-recovery` @ `a7755cd`. No file in the repository was modified.

What I ran (all read-only):
- `uv run python -m pytest -q powercoder training labs/10-poweragentbench-restorebench`: **81 passed, 1 skipped**.
- A scratch probe of `provenance.check`: `scratchpad/prov_probe.py`, with synthetic inputs and no GPU or network.
- Read-only Python over the frozen task files to measure distribution overlap.

I did not start containers, GPU jobs or network calls, and did not touch any systemd unit.

## 0. Measured facts that change the picture

Each of these came from the repo, not from the brief.

1. **All 100 benchmark tasks, and all 273 training rows, use one network (`snem-sa-2000`).** All 75 fit prompts state the same bracket, `s in [0.9, 1.1]`.
   - Of the benchmark's 69 distinct (subfamily, element, contingency) keys, **36 also occur in the training split**, with different targets.
   - So "held-out" here means interpolating the target within one network and one template. It is not generalisation.
2. **Line-rating fits have a closed-form answer.**
   - `studies.run_study_trial` scales `line.max_i_ka` by `s` (studies.py ~L126). The current flow does not change, and pandapower's `loading_percent` is proportional to `i_ka / max_i_ka`, so `loading(s) = loading(1) / s` and `s* = loading(1) / target`.
   - One trial at `s = 1` and one confirming trial at `s*` solve every line-rating task. The scripted bisection spends about 5–7 trials.
   - Load-scale and gen-dispatch fits are smooth and close to linear over ±10%, so a secant method would solve them in about 3 trials.
   - (Derived from code and pandapower's definition. I did not execute it.)
3. **The scripted policy is roughly 30 lines** (`trajectories.fit`, L227–L262), and the student is trained to reproduce it token for token (`generation_check`).
4. **Recovery variants are supervised, injected mistake included** (see finding B1 below).

---

## Ideas: merit, cost, risk, evidence, cheapest decisive experiment, recommendation

### Idea 8 (addressed first, because it frames the rest): does an LLM agent add value over the script?

**Honest answer: on the current task set, no measurable value is possible, and the ≥10pp gate cannot show any.**

- Every task is solvable by a fixed deterministic policy that needs no language understanding beyond slot extraction. The prompts follow a template, and the case, element, target, tolerance and interval can all be read with a regex.
- The student is trained to imitate exactly that policy. The best it can do is match the script, which reaches 238/238 on training rows and 20/20 on the smoke set by the driver's own comment.
- What the study can measure: whether a LoRA can learn a tool-call template, and whether base models can follow the protocol without training. That is a legitimate harness and SFT engineering result. It is not evidence of agent value.

**Is the ≥10pp gate meaningful?** Not as a test of domain capability:
- The r4 bases scored 3.9–5.6% under defective tools. Any student that reproduces the template will clear +10pp by a wide margin, so the gate measures imitation.
- `compare_agents.py:67` sets `domain_threshold_met = delta >= 0.10` from the **point estimate**. With n = 100 paired single greedy attempts, the bootstrap CI half-width is plausibly ±6–10pp. The gate should require the CI lower bound to exceed the threshold, or at least exceed 0.
- Six pairwise comparisons on one sample receive no multiplicity correction.

**Task designs that would make the agent's contribution measurable:**
- **Baselines that bound it:**
  - (a) Script-as-agent, the oracle: pass rate, calls and wall time.
  - (b) Base model with the procedure written into the system prompt or given as one demonstration. If the student is not better than (b), fine-tuning added nothing.
  - (c) Student.
  - Report student − prompted-base, and script − student, with paired CIs.
- **Study selection from free-text intent:** for example, "Is the SA network secure if line 135 trips during peak load?" The agent must choose N-1 plus a load scale and interpret the result. The template cannot be regex-filled.
- **Unbracketed or non-monotone fits:** no interval given, or an interval where the metric is non-monotone or non-convergent at one end. The agent must discover a bracket and handle non-convergence.
- **Interpretation:** "Did this contingency create new voltage violations?" requires comparing against `base_case_voltage_violations` (39). Report the count of *new* violated buses and their IDs, which needs a tool that lists violations.
- **Multi-objective and multi-step:** for example, "the largest load scale such that no line exceeds 100% under any of these 3 contingencies". This composes several studies, and the step count depends on intermediate results.
- **Multiple networks:** the case files `snemSA.m` and others exist. Hold out a whole network.
- **Restoration and diagnosis:** RestoreBench (Lab 10) is exactly this type of task, which is why Lab 10 matters more than its current framing suggests.
- **Efficiency as a scored output:** tool calls, tokens and wall time against the script. An agent that is 100x slower than a 30-line script at equal accuracy is a negative result, and it should be reported as one.

**Merit of the challenge: 5. Recommendation: do now.** Write it as a backlog or issue item and as a caveat in the r6 handoff before any r6 number is quoted. Add baseline (b) to r6 or r7; on the 100-task sample it costs one more base evaluation per model.

### Idea 2: answer-by-reference (the final answer cites tool-call id and field; the harness renders the number)

- **Merit: 4.** It makes PC-REQ-1 hold by construction and removes every provenance heuristic weakness listed in section 2 below: precision gaming, coincidental matches, sign-insensitivity and thousands separators.
- **Cost:** moderate. It needs an answer schema, a renderer, a new scorer version and a new run root. Enforcement can use plain `response_format` JSON schema, which vLLM's default xgrammar already supports. A custom grammar engine is unnecessary.
- **Risks:**
  - (1) It changes what is measured. Reports become pointer selection, so accuracy is then mostly about choosing the right field. Example: `voltage_violations` versus `base_case_voltage_violations`, which are both 39 on many tasks. That is a semantically evasive but valid citation, the 0007 failure in a new form.
  - (2) Fit answers are already references to a tried scale. `relevant_evidence` requires the answer scale to have been tried, so fits gain nothing.
  - (3) It removes the ability to measure fabrication, because fabrication becomes impossible. Keep the free-text arm as the fabrication measurement.
- **Evidence for or against:** the share of r6 report answers whose numbers match more than one source field (ambiguity), and the share where provenance and accuracy disagree.
- **Cheapest decisive experiment (offline, about 1 hour of CPU):** over the r6 journals, map each printed answer number to every source field it matches. If ambiguity and false-trace rates are near zero, the heuristic suffices and answer-by-reference is a nice-to-have. If they are not, adopt it.
- **Recommendation: backlog**, as a written item gated on that offline analysis. Make it an optional arm, never a replacement for the free-text arm.

### Idea 6: failure-mode recovery training

- **Merit in principle: 3. As implemented: negative until finding B1 is fixed.**
  - `agent_sft.py:228` supervises every assistant turn, including the **injected faulty first call**.
  - The model is therefore trained to emit `contingency_line: "None"`, `contingency_line: 0` and `run_power_flow` for N-1 reports (FM-7/18/9) as first turns, in 119 of 357 conversations.
  - Every variant prompt is identical to a clean row's prompt, so for those 119 prompts the first-turn target is a 50/50 split between correct and faulty.
- **Cost of the fix:** small. Mask the injected turn's labels and supervise only from the recovery note onward. Then retrain, which costs about 2 h per student.
- **Risk:**
  - Recovery after real tool errors only teaches recovery from errors this harness surfaces.
  - FM-18 (line 0 out) is schema-valid and only visible through the new `contingency` field. Recovery from it requires the model to read that field, which is plausible.
- **Evidence:** count r6 student first calls that contain `"None"` or `contingency_line: 0`, or that use `run_power_flow` for N-1, against the base and against the v1 (plain-only) student.
- **Cheapest decisive experiment:** run the v1 plain student and the v2 recovery student on the 20-task smoke set, then count injected-pattern calls and recoveries. Better: also count on the 100-task r6 journals once they exist, which costs nothing.
- **Recommendation: do now** (fix the masking, or at minimum measure it before quoting r6 student results).

### Idea 1: forced first-turn tool call (strict arm)

- **Merit: 3** as a diagnostic arm. As designed it is **confounded**, so its result cannot be attributed to the first-turn tool-choice change:
  - The arm changes **two** things, `tool_choice="required"` on turn 0 **and** server `--tool-strict-level parameter` on **every** turn (`run_agent_study.py:75`, the `forced_arm` session call).
  - `compare_agents.compare` admits it as "exactly one difference" (`compare_agents.py:46`). The strict level is recorded in the serving JSON but is not checked or labelled in the comparison output.
  - The confound interacts with B1. Strict schema enforcement rejects the `"None"` strings the recovery data taught (the handoff measured xgrammar rejecting 70/119 injected calls). A forced-arm gain could therefore come from schema enforcement repairing a training defect, not from forcing.
- **Is "semantically evasive" a real risk here?** Yes, in a specific form:
  - The cheapest valid forced call is `run_power_flow(network_id)` or `run_study_trial(operation="none")`. Both are always schema-valid.
  - For N-1 reports, the FM-9 path (`run_power_flow`, no contingency) gives base-case numbers. Those **pass provenance** and fail accuracy. Forcing converts "fabricated" failures into "traced but wrong" failures, which changes the failure taxonomy more than the pass rate.
  - For fits, a forced throwaway call costs one of 24 calls, so the harm is small.
  - Turn 1 onward is `auto`, so the evasion can move to the answer turn.
- **Cost:** about one 100-task evaluation per model (hours of GPU).
- **Evidence:**
  - Per arm: first-call (tool, operation, contingency) distribution.
  - The `relevant_evidence` rate.
  - The fabricated → traced-wrong transition matrix.
- **Cheapest decisive experiment:** a 2×2 grid (strict on/off × required on/off) on the 20-task smoke set for the base models only. Base models are where unforced fabrication occurs. Students already call tools first 8/8 of the time (`generation_check`), so forcing is nearly a no-op for them.
- **Recommendation: backlog, redesigned as a 2×2 on the smoke set.** Run the full 100-task forced arm only if the 2×2 shows a forced-only effect. If it stays queued as is, rename it "strict+forced" in the outputs and record `tool_strict_level` in the evaluation manifest and comparison.

### Idea 3: typed "numerics" MCP server (linear solve, least squares, sensitivities with certificates; Newton instead of bisection; one IDL generating schemas, grammar and validation)

- **Merit:**
  - **3 for the narrow version:** a `sensitivity` tool returning d(metric)/d(scale) from a finite difference or from pandapower's Jacobian (`net._ppc["internal"]["J"]` after `runpp`), plus residual and condition certificates.
  - **2 for the full program:** a numerics co-processor generated from Rust, JSON Schema or LinkML.
- The "calculator co-processor" framing is sound. LLMs should not do arithmetic, and PC-REQ-1 already forbids model arithmetic in answers. But:
  - (a) For these tasks the co-processor solves the whole problem. Line-rating is closed form after one trial. With a Newton tool the agent becomes a router, which sharpens the idea-8 problem instead of solving it.
  - (b) Single-IDL code generation is a sizeable infrastructure project with no measured bottleneck behind it. The schema defects found so far (FM-1/18/23) were schema-*content* and *ordering* problems, which one IDL would not have prevented by itself.
- **Risk:** a model that cannot follow a 5-step bisection also mis-sequences a Newton loop. Certificates help only if the scorer uses them.
- **Evidence:** trials-to-tolerance and pass rate with and without a sensitivity tool, for base models, on fits.
- **Cheapest decisive experiment:** add one `estimate_scale` (secant/Newton step) tool to the study MCP server behind a flag and run base models on the 17 fit tasks of the smoke set. If base pass rates jump to near the student's, then most of what SFT taught was the search procedure. That is itself an important idea-8 finding.
- **Recommendation: backlog**, written as "sensitivity tool + certificates" (the narrow version). Do not build the IDL code generator now.

### Idea 7: Lab 10 RestoreBench via stand-in transport

- **Merit: 3.** It is the only *external* benchmark here and the only task type where an agent's judgment matters (restoring convergence). That makes it the best answer to idea 8 available in the repo.
- **Weaknesses:**
  - The first turn is about 43k characters (roughly 11–13k tokens) in a 32k context. With a 10-maneuver multi-turn episode, overflow mid-episode is likely.
  - Responses are capped at 8,192 tokens (RestoreBench asks for 16,384).
  - Decoding is greedy with one repetition, versus temperature 1.0 and 3 repetitions upstream, so results are not comparable with published numbers.
  - Expected scores for 4B/8B models are around 0. Students fine-tuned on a different tool are likely to regress. That makes it a regression or transfer probe, not a gain measure.
  - S0081's witness fails on aarch64, so the denominator should be 45. Check that summaries say so.
- **Cost:** about 2 h per model × 4 = about 8 h of GPU after r6.
- **Cheapest decisive experiment:** `--limit 5` per model, recording context-overflow counts. Spend the full 46 only if any model gets above 0 or overflow is not dominant.
- **Recommendation: keep, but reduce to a pilot before the full run.** Frame the result as transfer/regression in the write-up.

### Idea 9: other r6 design weaknesses

Merit as critique: 4. Ordered by importance:
- **Smoke gate (`SMOKE_MIN_PASS = 10`, `run_agent_study.py:72`):**
  - Fine as a "not broken" gate.
  - The justification, P ≈ 7e-6 at p = 0.1, rests on r4 base rates from the defective harness, treated as i.i.d. That number is decorative.
  - The smoke set is 17 fits and 3 reports, so it barely tests reports.
  - Bases are never smoke-run, so the gate has no control.
- **Equal allocation:** honestly documented (the manifest says it is not an estimate of the 306-task rate). Acceptable. Make sure no write-up quotes it as the benchmark pass rate.
- **One epoch, LoRA r32, LR 1e-4:** adequate for template imitation (final loss 0.0027). Not a concern given idea 8.
- **Greedy single attempt:**
  - n = 100 with batching nondeterminism (concurrency 8, FM-3 note). Paired deltas under about 10pp are within noise.
  - Use CI-based gating, and repeat attempts on at least the smoke set to estimate run-to-run variance.
- **Provenance:** see section 2 below.

### Idea 5: grammar engine choice (xgrammar vs llguidance vs GBNF)

- **Merit of the pivot ("drop xgrammar, move to llguidance"): 1.**
  - The repo now stores **no grammar** (commit `a7755cd` removed it). The forced arm uses vLLM's built-in model-native Qwen-XML structural tag, which the handoff measured as xgrammar-only in vLLM 0.31 (llguidance accepts only the legacy structural-tag shape). Moving vLLM to the `guidance` backend would remove the one measured, working path.
  - "Keeping xgrammar in sync" costs nothing beyond pinning the inference image, which is already done (xgrammar 0.2.7, llguidance 1.7.6).
  - The claim that xgrammar is "no longer maintained" is **unverified here**. I could not check upstream offline, and it is vLLM's default backend in the measured version. Treat it as a hypothesis, not a reason.
  - llama.cpp (GBNF, optional LLGuidance) serves GGUF through llama-swap, which is **not in the PowerCoder study at all**, so there is no decision to make for this study.
- **Cost of the pivot:** medium. A new grammar path has to be authored and validated, and grammars must be stored again, which brings back the tech debt just removed.
- **Risk:** the 0007 "semantically evasive" finding applies to every engine equally. Engine choice does not address it.
- **Cheapest decisive experiment:** none needed now. If answer-by-reference (idea 2) is adopted, run the same JSON schema on both vLLM backends over the 20 smoke tasks and compare acceptance and pass rate.
- **Recommendation: drop the pivot.** Keep vLLM's default (xgrammar) for vLLM, and record `structured_outputs` backend and version plus `tool_strict_level` in every evaluation manifest. Backlog a one-line note: "llguidance only if llama.cpp-served models enter the study or a needed grammar feature is xgrammar-missing."

### Idea 4: PCA to "destructure decisions"

- **Merit: 1.**
  - Decisions here are low-dimensional and discrete (tool, operation, contingency, scale). PCA on such features gives components without physical meaning.
  - For "which inputs drive outcomes", one-at-a-time sensitivities (idea 3's tool) or simple ablations are interpretable and deterministic.
  - For "compare model choices", a contingency table of first-call choices and the `failure_modes` taxonomy already answer the question better.
  - PCA over pandapower Jacobians or PTDFs is a legitimate power-systems technique (modal analysis), but that is a different idea from destructuring agent decisions.
- **Recommendation: drop**, or fold into idea 3 as "modal/sensitivity analysis tool" if someone wants it later.

---

## 1. Code and measurement-integrity findings

Each finding gives file:line, severity and a concrete failure scenario.

| # | File:line | Severity | Finding / scenario | Verified? |
|---|---|---|---|---|
| B1 | `training/agent_sft.py:228` with `powercoder/datasets/trajectories.py:335` and `:173` | **High** | Every assistant turn is supervised, including the injected FM-7/FM-18/FM-9 call. For 119 prompts the first-turn target is a 50/50 split between the correct call and the faulty one. Scenario: on a benchmark task that shares an element with a variant row (36/69 keys overlap), the student opens with `contingency_line: 0` (line 0 out). That is schema-valid, so even the strict arm does not block it. It wastes calls or produces off-case measurements. `generation_check` (`agent_sft.py:150`) checks the first 2 kept rows per subfamily on 32 tokens. That is too short to reach the `contingency_line` divergence point in Qwen XML, so it cannot detect this. | Verified by reading the code. Behavioural effect not measured. |
| B2 | `training/run_agent_study.py:75` and the `forced_arm` session call; `powercoder/inference/compare_agents.py:46` | **Medium-high** | The forced arm changes tool_choice and server strict level together, but the comparison claims a single difference and checks only `first_turn_tool_choice`. Scenario: the "forced vs auto" delta is reported as the effect of forcing when it is partly schema enforcement, which interacts with B1. | Verified |
| B3 | `powercoder/inference/compare_agents.py:67` | Medium | `domain_threshold_met` uses the point delta, not the bootstrap CI that is computed alongside it. Scenario: a 10pp point delta whose CI includes 0 is marked as met. | Verified |
| B4 | `powercoder/inference/provenance.py:89` (and `domain_scorecard.py:188`) | Medium | Provenance tolerance grows as printed precision drops, and matching is against *any* number in the prompt or in a tool response. Probe results: `"converged in 100 iterations, voltage violations = 2000"` is **traced** (2000 from the case name, 100 from a response limit field). A no-tool report printing `1` / `2000` / `135` is traced. `"1,039"` → `["1","039"]` both traced (039 matches 39). `-2.66` is traced to `2.66` because the sign is ignored. A fit answer equal to the prompt's interval end (`1.1`) or target (`0.312797`) is traced with no tool call. **False negatives:** a truncated value (`0.9069` for 0.90697) is flagged fabricated. Perverse incentive: printing fewer digits makes tracing easier. | Verified with `prov_probe.py` |
| B5 | `powercoder/inference/provenance.py:91` | Low-medium | An answer with no numbers is `traced: True` (vacuous). It fails accuracy anyway, but `failure_modes` then never classifies it as fabricated. That is fine, but the requirement document reads as if provenance were a positive property. | Verified |
| B6 | `powercoder/inference/domain_scorecard.py:188` | Low-medium | The solver-verified v2 pass does not require `relevant_evidence`. For fits, provenance is circular: the answer scale is "traced" because the model's own trial input is echoed in the response, or because the prompt contains it. Scenario: a no-tool fit answer of `1.1` that happens to be within tolerance passes v2. That is rare, but provenance adds nothing on fits. | Verified (logic); frequency not measured |
| B7 | `powercoder/inference/latency_bench.py:78,85` | Low-medium (speculative) | The decode rate uses the timestamps of the first and last *emitted* deltas. With `ignore_eos` and the `qwen3_coder` tool parser, tokens after a completed tool call may be buffered or swallowed instead of streamed. `last` would then come before the 512th token and the tok/s would be overstated, and differently per model (a student emits a tool call within about 60 tokens; a base reasons). Use end-of-stream time, or check the token counts per delta. Rates are per-tokenizer, so Qwen and Granite tok/s are not directly comparable. | Speculative; not run |
| B8 | `training/run_agent_study.py:72` (comment) | Low | The smoke-threshold justification uses r4 defective-harness base rates as an i.i.d. p. The number is decorative, and bases are not smoke-tested under r6 tools. | Verified |
| B9 | `powercoder/datasets/trajectories.py:251` | Low | Bisection termination relies only on the call budget. If `round(mid, 6)` stops moving (interval under 1e-6), it loops until `RuntimeError`. That cannot happen with the current tolerances (relative about 0.4%). Separately, `best` is overwritten by every midpoint, which is harmless because the loop exits on `|f_mid| ≤ tol`. | Verified (reading) |
| B10 | `powercoder/inference/compare_agents.py` (`compare`) | Low | Comparison equality checks do not include serving flags (strict level, structured-output backend, image digest). Two runs that differ only in vLLM image or backend compare silently. | Verified |
| B11 | `labs/10-.../local_provider.py` (`LOCAL_MAX_TOKENS`) | Low | 43k-character first turn plus 8,192 response tokens in a 32,768-token context: mid-episode overflow is likely after a few maneuvers. Make sure overflow is reported as its own outcome, not as an agent failure. | Speculative |
| B12 | Docs: AGENTS.md "Feasibility" and handoff | Low (framing) | "All tasks solvable within budgets" is true. The missing statement is that they are solvable *by a fixed script*. Without it, the r6 deltas will be read as agent-capability gains. | Verified |

The tests pass, but none covers B1 (label masking of injected turns), B2 (strict-level recording), B3 or B4's edge cases.

## 2. Provenance verdict (PC-REQ-1)

The check is a reasonable necessary condition against the specific failure it targets: a student that answered with no tool call. It is not a sufficient guarantee:
- coincidental matches against prompt IDs and response limits;
- precision gaming;
- sign-blindness;
- separator splitting;
- circularity on fits.

Short-term hardening that needs no new arm:
- match only against *metric* fields of responses, not limits or IDs;
- exclude prompt numbers except for an explicit allow-list (case ID, element IDs);
- respect the sign;
- require minimum printed precision per field type.

Long-term: idea 2.

## 3. Final ranking (merit-adjusted priority)

1. **Idea 8: agent-value challenge plus baselines** (script oracle, prompted base) and CI-based gating. **Do now; backlog item.**
2. **Idea 6 / B1: fix recovery-training loss masking**, or measure its effect before quoting r6 student numbers. **Do now; backlog item.**
3. **Idea 9: r6 measurement hygiene** (B3 CI gate, B4 provenance hardening, record serving flags, repeat attempts on the smoke set). **Do now** for B3 and the manifest fields; **backlog item** for provenance hardening.
4. **Idea 1: forced arm**, redesigned as a 2×2 on the smoke set and relabelled strict+forced (B2). **Backlog item**; do not run the 100-task forced arm as currently designed.
5. **Idea 2: answer-by-reference**, gated on the offline ambiguity analysis of the r6 journals. **Backlog item.**
6. **Idea 7: Lab 10**: reduce to a 5-scenario pilot, framed as transfer/regression. **Keep, pilot first.**
7. **Idea 3: sensitivity/Newton tool with certificates** (narrow version). **Backlog item.** Drop the full IDL co-processor for now.
8. **Idea 5: grammar pivot to llguidance.** **Drop.** Keep vLLM's default xgrammar, record the backend and version, and leave a one-line note for llama.cpp/llguidance.
9. **Idea 4: PCA for decisions.** **Drop** (or fold into 3 as modal/sensitivity analysis).

**Items to write into the issue backlog:** 8, 6/B1, 9 (provenance hardening and CI gate), 1 (2×2 redesign), 2, 3 (narrow).
