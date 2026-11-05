# 0011 — Strict-tool-calling arm: redesign as a 2×2 before running it

- **Status:** implemented 2026-10-23 (`run_agent_study --strict-arm`). Queued to run after r6 completes, through `powercoder-agent-study-r6-strict-wait`. The confounded single arm was never run. Review idea 1 (merit 3) and B2 ([review](reviews/2026-10-23-r6-merit-review.md)).

## Problem
As built, the arm changes two things at once: `tool_choice="required"` on turn 0, and `--tool-strict-level parameter` on every turn. `compare` records only the first. Strict schemas also block exactly the `"None"` strings that B1 (0009) trained in, so a gain could not be credited to forcing. Backlog 0007 measured that grammar-constrained decoding can be semantically evasive: a forced call could be well formed and useless.

## Proposal
Run strict {off, on} × first-turn {auto, required} on the 20 smoke tasks with both base models, about 4 × 20 attempts per model. Record both flags in the manifest (0010 item 5). Score useless forced calls separately: well-formed calls to the wrong study.

## Measured basis (2026-10-22)
vLLM 0.31 builds xgrammar's model-native Qwen-XML tool grammar per request. `"required"` and the server flag both produced native XML (`</think><tool_call>…`, without the two newlines Qwen otherwise writes). No grammar is stored in the repo.

## What the running 2×2 can and cannot show (added 2026-10-23, from r6b's journals)
Launched by hand 2026-10-23 as `powercoder-agent-study-r6b-strict`. Its waiter only fires on
`pipeline_complete`, and r6b ended with `driver_error` at the Lab 10 pilot after all its
evaluations. It runs 4 models × 4 cells × 20 smoke tasks; the smoke set is 3 report and 17 fit
tasks.

Measured on r6b's 100-task benchmark (journals, not the smoke set):

| Model | First turn is a tool call | Schema-validation errors (tasks affected) | Error kinds |
|---|---|---|---|
| Qwen base | 99/100 | 32 of 513 calls (28 tasks) | 17 `"None"` strings, 13 `contingency_line` on `run_power_flow`, 2 `'all'` |
| Qwen student | 100/100 | 0 of 487 | — |
| Granite base | 100/100 | 26 of 716 (16 tasks) | 25 `"None"`, 1 missing `scale` |
| Granite student | 100/100 | 0 of 595 | — |

Consequences for a fair reading:
- **Forcing the first turn targets a failure that no longer occurs.** Every model already calls a
  tool first, so `required` can change at most 1 of r6b's 400 first turns. Expect no effect, and
  don't read that as "forcing doesn't work". The failure it was meant for was the r4 student
  answering without tools (FM-19), which training fixed. One open question: does a forced call skip
  the reasoning that, in 81 of Qwen base's 100 attempts, ran into the 8,192-token response cap
  (FM-2)? Read that from the cell journals (`finish_reason`, completion tokens), not from pass
  counts.
- **Schema enforcement can only help the base models.** The students made no schema errors. For
  the bases, every error above is one `parameter` should make impossible. FM-26's bus 999, by
  contrast, is schema-valid, so no grammar prevents it.
- **20 tasks have little statistical power.** A paired 95% interval on 20 tasks is wide (r6b's
  100-task intervals were already about ±10 pp). Only a large pass-rate change will show. The more
  powerful measure is per call: schema errors per call (about 500–700 calls per model on the 100
  tasks), plus turns and completion tokens per attempt. Those come from journals the 2×2 already
  writes, with no extra GPU time.
- **Confirm the grammar was applied in each cell before crediting or blaming it.** Granite is
  served with the same `qwen3_coder` parser, so xgrammar constrains it to Qwen XML. That works
  unforced (716 parsed calls), but under `parameter` it hasn't been checked. Evidence per cell: the
  `--tool-strict-level` flag in the server's `non-default args` line, and zero schema-validation
  errors in that cell.

Follow-ups, in merit order:
1. **Per-call journal metrics** for every cell, against its own `(auto, auto)` cell:
   - schema errors per call;
   - `"None"` strings;
   - completion tokens and `finish_reason: length` per turn;
   - well-formed calls to the wrong study (this file's original "useless forced call" score, not
     implemented).

   CPU only.
2. **If `parameter` cuts the bases' schema errors, rerun it on the 100-task benchmark** for both
   bases (measured r6b wall time on the GPU: Qwen base 2.33 h, Granite base 8.51 h). That is the
   test with enough power to compare with r6b's `auto` result.
3. **Drop the forcing factor** from further runs unless the token analysis in follow-up 1 shows
   that forcing changes reasoning length.

## Qwen half measured (2026-10-23, `strict_arm_metrics`; Granite cells still running)

| Cell | Pass | Calls | Schema errors | `"None"` | Off-task trials | Turns | Truncated | Median reasoning tokens/turn | First-turn median | Server level |
|---|---|---|---|---|---|---|---|---|---|---|
| Qwen base auto/auto | 3 | 116 | 6 | 6 | 7 | 130 | 14 | 700.5 | 572.5 | auto |
| Qwen base auto/required | 1 | 119 | 5 | 4 | 4 | 129 | 18 | 769.0 | 577.5 | auto |
| Qwen base parameter/auto | 2 | 103 | 3 | 3 | 3 | 117 | 14 | 783.0 | 569.5 | parameter |
| Qwen base parameter/required | 2 | 105 | 2 | 2 | 7 | 116 | 17 | 783.0 | 564.0 | parameter |
| Qwen student, all 4 cells | 17 | 102 | 0 | 0 | 0 | 122 | 0 | 1.0 | 1.0 | as set |

What the table shows:
- **The student is unaffected.** All four of its cells are identical. It already calls tools
  correctly and reasons almost not at all (median 1 reasoning token per turn).
- **Forcing doesn't shorten the base's reasoning.** The first-turn median is 564–578 tokens in
  every cell, so `required` does not cut the reasoning before the first call (FM-2's question).
- **`parameter` enforcement looks partial.** Base schema errors by turn:
  - first turn: auto 4 of 25 and 4 of 22 calls, **parameter 0 of 25 and 0 of 22**;
  - later turns: auto 2 of 91 and 1 of 97, parameter 3 of 78 and 2 of 83.

  The server did log `tool_strict_level: 'parameter'`, yet later-turn calls still carried
  `contingency_line: "None"` (e.g. `qwen-base-strict-parameter-first-auto/ex_d92e03950c02a97a`). So
  the argument schema was enforced on the first turn but not on later ones. Why is not yet
  established, and the counts are small (4 → 0), so this is suggestive, not conclusive. The Granite
  cells add evidence. If it holds, the "parameter" cells measure first-turn enforcement only, and
  that should be said wherever they are quoted.
