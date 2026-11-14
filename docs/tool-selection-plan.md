# Tool selection with a typed local decision model: considerations, plan, evaluation

Status: evaluated 2026-11-15 on branch `feat/tool-selection-eval`; the result is at the end (d1-3B does not meet the decision rule). The candidate-table numbers below
are self-reported by the authors, read on 2026-11-14; the measured numbers are in the final section.

## What would "helpful" mean

Today the harness hands a model an allow-listed tool set (`study`, `study+python2`) and the model both picks the tool and writes its arguments as generated
text; the gateway journals each call. A typed decision step would pick the tool from the allow-list (or abstain) and return probabilities, with no free text:
auditable, bounded, and in line with "the model plans, deterministic tools compute, every step is logged".

Evidence that tool *selection* is the bottleneck: none yet. The trained students score 88-90 of 90 held-out tasks with the shell. The untrained 4B model fails by
not finishing (60 of 90 answers empty at twice the budget; loops, turn limits), not by choosing a visibly wrong tool. A selector may help audit and guardrails
more than accuracy. The first step therefore costs no model: count wrong first tool calls in the journals we already have.

## Candidates (facts as read; all benchmarks self-reported)

| Candidate | What it is | Runs where | Licence | Fit |
|---|---|---|---|---|
| typesafe-sdk-python | Client for the hosted TypeSafe "System One" API. README names no mechanism and no local model. | Hosted; needs `TYPESAFE_API_KEY` and network | MIT (client) | Excluded: not local, and it would send task text to a third party. |
| Liquid AI d1-3B (Ollama tag `d1:3b`, built on LFM2.5-VL-3B) | "Decision model": a state plus named typed questions (`noul` yes/no, `choice`, `score`) answered with probabilities in one forward pass, no generated text. Claims Decision Index 0.2.1 score 48.57, Tools sub-score 74.5. The pages do not say what the Tools category tests. | Python `transformers>=5.14` with `trust_remote_code=True`; Ollama (excluded by the owner). Correction 2026-11-15: `LiquidAI/d1-3B-GGUF` also exists (not used: the typed probability readout comes from the Python API). A newer `d1-2B` exists, not evaluated. | LFM Open License v1.0 | Best candidate on claimed tool score; see licence flag. |
| Liquid AI d1-omni-600M (587M, F32, 2.35 GB) | Same decision API, text/JSON, images, audio; "early research release". Claims Decision Index 15.95, Tools sub-score 15.1. | `transformers>=5.15`, `trust_remote_code=True`, no GGUF in the repo | LFM Open License v1.0 | Weak claimed tool score; probably not worth running first. |
| laya (NandhaKishorM) | Encoder classifier (ModernBERT-family) with typed choice, score and yes/no, calibration and an abstain flag. Authors report 0.766 accuracy for a fine-tuned checkpoint against 0.362 for the base checkpoint on their own 2,000-decision set: it needs fitting per domain. | PyTorch and HF checkpoints; its HTTP protocol is not OpenAI-compatible | Apache-2.0 | Plausible and cheap to try; maturity claims (stars, commits) not verified, benchmarks by the same authors, the preprint is not peer reviewed. |
| Control: constrained choice on an existing llama.cpp model | A JSON-schema or grammar-constrained answer from neo-coder or arete-27b through the existing llama-swap and gateway. No new runtime. | Existing pattern | Existing | The honest comparison for "is a decision model better than what we already run". |

## Flags that need an owner decision

1. **Licence.** The LFM Open License v1.0 conditions commercial use on annual revenue under 10 million USD (section 5(a), 5(b)); section 5(c) exempts a qualified non-profit's non-commercial or research use. wattson is a non-commercial CSIRO and AEMO joint effort, but whether that fits 5(c) is a question for the partners' legal contacts, not for this repo. I read the d1-omni-600M licence text; the d1-3B licence text was not read.
2. **`trust_remote_code=True`.** Both d1 models ship Python that `transformers` executes on load. Acceptable only pinned to a reviewed revision and run in a container with `--network=none`, read-only root and a memory cap (sm3lly's OCI hook rejects uncapped containers).
3. **Runtime pattern.** Neither d1 nor laya speaks the OpenAI chat API that llama-swap and the gateway front. Fitting them in means a small container we own that wraps the library behind one HTTP route, registered beside the llama.cpp models. That is new code to maintain; the owner's guidance is to extend existing local patterns, so this must earn its place against the control above.
4. **Placement.** Not GGUF, so DEC-013 does not forbid shinyDGX, but shinyDGX's job-placement rule (DEC-015) points heavy work at sm3lly, whose GPU is shared with neo-coder and kr0ki. CPU speed for a 600M to 3B model is unmeasured.

## Plan

1. **Measure whether selection errors exist.** From the existing harness journals (held-out 90: trained students, untrained runs), compute how often the first tool call differs from the tool a passing run used for the same task. No model, no GPU.
2. **Labelled selection cases.** Build a case set where the correct tool is known deterministically from the task family (SQL over parquet, workbook cell read, grid solver, shell). Dev and held-out split, hashes frozen before any model sees held-out cases.
3. **Baselines first.** A deterministic keyword or BM25 router, and the constrained-choice control on neo-coder through the gateway.
4. **Candidates.** d1-3B in a pinned, network-less container; laya second if cheap. Report per task family with counts, top-1 accuracy, coverage with abstain, calibration error (using the model's own confidence), latency and memory.
5. **Decision rule fixed in advance.** Adopt a candidate only if it beats both baselines on held-out cases at equal or higher coverage, runs inside the memory cap, and the licence question is answered. Otherwise record the result and stop.

## Expected outcome, stated now

With a handful of allow-listed tools, the deterministic router may already be near perfect, and if step 1 finds no selection errors the case for a decision model rests on audit value only. That is a legitimate result.

## Step 1 result (2026-11-14): what the journals of the untrained 4B runs show

Read-only count over the per-task journals of three untrained Qwen3.5-4B runs on the held-out 90 (thinking on, thinking off, and the doubled-budget run; sm3lly
`/c0de/out-pc-baseline/`). The tool set is `load_network`, `run_power_flow`, `run_study_trial`, `run_python`. No trained-student journals were on that machine, so
this says nothing about the students.

| Run | First tool (python / study_trial / load_network / none) | Calls by tool (python / study_trial / load_network / power_flow) | `run_python` calls with status error | `run_study_trial` calls with `execution_failure` |
|---|---|---|---|---|
| thinking on, 8192 tokens | 42 / 26 / 20 / 2 | 521 / 62 / 20 / 3 | 113 of 521 | 41 of 62 |
| thinking off | 44 / 42 / 4 / 0 | 701 / 54 / 5 / 2 | 177 of 701 | 48 of 54 |
| thinking on, doubled budget | 41 / 26 / 21 / 2 | 625 / 60 / 21 / 3 | 168 of 625 | 36 of 60 |

What this shows: the model mostly works in `run_python` (85 to 90 percent of all tool calls) and retries there; a large share of its `run_study_trial` calls fail at
execution; it picks a first tool that varies across tasks. What it does not show: whether any first choice was wrong. No label says which tool a task needs, so a
selection error rate cannot be computed from these journals. The one journal read by hand shows a correctly chosen tool called with bad arguments
(`operation=load_scale requires observation, observation_id`). My attempt to count argument errors by matching error text found none and is not trustworthy, because
the error text may sit in a field I did not read.

Consequence for the plan: the evidence points at argument construction and not finishing, neither of which a tool-choice model addresses. Step 2 (labelled
selection cases) is worth doing only if the owner wants the audit and guardrail value regardless; otherwise the result of step 1 is to stop here.

## Result (2026-11-15): d1-3B does not meet the decision rule

Cases: 64 labelled, 32 dev and 32 held-out, one family per tool (`load_network`, `run_power_flow`, `run_study_trial`, `run_python`), 8 cases per family per split, generated by `generate_cases.py` from disjoint phrasing templates (hashes in `FREEZE.json`, frozen before any arm saw held-out). There is no SQL tool in the allow-list, so the NEM tables are not covered. The tasks are short and easy; the router and the cases were written by the same process.

| Arm | Dev top-1 | Held-out top-1 | Held-out coverage | Held-out wrong answers | Latency p50 |
|---|---|---|---|---|---|
| Keyword router (`router_baseline.py`, written from dev only) | 32/32 | 28/32 | 28/32 | 0 (4 abstentions, all `load_network`) | not measured (no model) |
| neo-coder, JSON-schema constrained choice via the gateway | 32/32 | 28/32 | 32/32 | 4 (all picked `run_study_trial`) | 0.84 s |
| d1-3B, `choice` question, abstain below confidence 0.5, CPU fp32 | 21/32 | 18/32 | 29/32 | 11 of 29 answered | 23.7 s |

d1-3B held-out by family: `load_network` 8/8, `run_python` 8/8, `run_power_flow` 2/8, `run_study_trial` 0/8 (3 abstained). Its errors are 12 picks of `load_network` for a task that needed `run_power_flow` or `run_study_trial`, and 2 picks of `run_python` for `run_study_trial`. The d1-3B dev result with the first question wording was 16/32 (see below).

Decision rule from step 5: adopt a candidate only if it beats both baselines on held-out at equal or higher coverage, stays inside the memory cap, and the licence is answered. d1-3B fails the first condition (18/32 against 28/32), runs 28 times slower than the control on this hardware, and peaked at 16,313 MiB of a 16,384 MiB cap. Stop here: do not adopt d1-3B for tool selection.

What went wrong in the run, so it is not repeated: the first frozen question asked "which tool should be called first", and the model answered `load_network` for every analysis task (16/32 on dev, all 16 misses at confidence 0.70-0.85), because loading is first and the labels mean the tool that does the task. The wording was changed once, on dev only ("Which one tool performs this task?"); held-out was never run with the first wording. A model that answers a differently-worded question this differently is also evidence that the result depends on prompt wording that the README does not specify. One dev run crashed with SIGILL after the weights loaded; an unchanged retry completed. The cause was not found.

Limits: one question wording per arm, one run each at temperature 0 (d1 is deterministic apart from kernel differences), 32 held-out cases, easy cases, a CPU-only d1 run (the README's 8-30 ms figures are for GPUs and were not tested; the GPU beside neo-coder had about 4 GB free), the d1-2B and d1-3B-GGUF variants not tried, no calibration of confidence beyond the fixed 0.5 threshold, laya not tried. The result says d1-3B was worse than a keyword router on this narrow case set; it does not say d1-3B is a poor decision model. The licence question (section 5(c)) is still open with the partners' legal contacts and now has no urgency.

What the result means for the wider question: on a four-tool allow-list with clear task wording, tool selection is solved by a deterministic router at no cost and with abstain-on-miss behaviour, and step 1 of this plan already found the real failures in argument construction and not finishing. A typed decision model is not needed for selection here. Its remaining value would be as an audit signal (a probability per tool, logged) and that is better tested later on a task set with real ambiguity.

Files: `benchmarks/tool-selection/` (`cases.json`, `router_baseline.py`, `constrained_choice.py`, `d1/` harness and `stage.sh`, `results/`, `FREEZE.json`).
