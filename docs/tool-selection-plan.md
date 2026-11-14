# Tool selection with a typed local decision model: considerations, plan, evaluation

Status: proposal for review, written 2026-11-14 on branch `feat/tool-selection-eval`. No model has been run. Every number below about a third-party model is
self-reported by its authors and was read from their pages that day; none was reproduced here.

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
| Liquid AI d1-3B (Ollama tag `d1:3b`, built on LFM2.5-VL-3B) | "Decision model": a state plus named typed questions (`noul` yes/no, `choice`, `score`) answered with probabilities in one forward pass, no generated text. Claims Decision Index 0.2.1 score 48.57, Tools sub-score 74.5. The pages do not say what the Tools category tests. | Python `transformers>=5.14` with `trust_remote_code=True`; Ollama (excluded by the owner). A newer `d1-2B` exists, not read. | LFM Open License v1.0 | Best candidate on claimed tool score; see licence flag. |
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
