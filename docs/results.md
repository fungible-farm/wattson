# Results: Python shell, thinking, and plain tools

Measured on held-out tasks with the `solver-verified-normalised-v4` scorer: report values are compared after normalising to
four decimals, with tolerances of 1e-3 pu (voltages), 0.1 percentage points (loading) and exact counts. "Untraced" means the
model wrote a number that no prompt, tool schema, tool reply or program output gave it, a direct measure of arithmetic done
in the model's head (`wattson.inference.arithmetic_exposure`, a heuristic, not a proof). Seeds are independent training runs
of the same recipe on the same data; with 3 seeds or fewer, differences smaller than the spread between seeds are not claims.

## The result that matters for "never do arithmetic"

| Arm | Held-out 90: attempts with an untraced number | Derived 40: attempts with an untraced number |
|---|---|---|
| Plain tools, trained (4 runs: 3 on older data, 1 on the final data) | 86, 85, 86, 87 | 7, 5, 5, 18 |
| Untrained Qwen3.5-4B + shell (1 run) | not run | 21 |
| Python shell, non-thinking (Qwen3.5-4B, 3 seeds) | 0, 0, 0 | 0, 0, 0 |
| Python shell, thinking (Qwen3.5-4B, 3 seeds) | 0, 0, 0 | 1, 3, 1 |
| Python shell (Granite 4.2 8B, 1 seed) | 0 | 0 (1 flagged, a false positive: see below) |

## Accuracy (passes per suite, per seed)

| Arm | Held-out (90) | Judgement (40) | Unsolvable (28) | Planning (40) | Derived (40) |
|---|---|---|---|---|---|
| Plain tools, trained, final data (1 seed) | 75 | 38 | 25 | 40 | 31 |
| Plain tools, trained, older data (3 seeds) | 76, 69, 74 | 29, 23, 27 | 28, 28, 28 | - | 23, 20, 23 |
| Python shell, non-thinking (3 seeds) | 89, 88, 90 | 40, 40, 40 | 27, 18, 20 | 40, 40, 40 | 31, 30, 27 |
| Python shell, thinking (3 seeds) | 90, 89, 85 | 40, 40, 40 | 26, 19, 26 | 40, 40, 40 | 39, 29, 38 |
| Untrained Qwen3.5-4B + shell (1 run, thinking on) | 13 (see note) | - | - | - | 35 |
| Untrained Qwen3.5-4B + shell (1 run, thinking off) | 5 (see note) | - | - | - | - |
| Untrained Qwen3.5-4B + shell (1 run, thinking on, doubled budget; sensitivity test) | 29 (see note) | - | - | - | - |
| Granite 4.2 8B + shell (1 seed) | 90 | 36 | 14 | 40 | 37 |

Untrained held-out note (#132, one greedy run, temperature 0, max_tokens 8192, 12 turns): 14 tasks ended with an answer (13 correct), 42 hit the
generation limit, 20 the turn limit and 14 the context limit, so 76 answers were empty. The model is failing to finish, not answering wrongly. The
derived-40 untrained run (35) is not comparable until its thinking mode and token budget are confirmed to match. With thinking off (same settings otherwise,
`enable_thinking=false` as the server default, no reasoning in any response) it scored 5/90: 17 answered, 32 hit the turn limit, 31 the context limit,
10 the generation limit. So runaway reasoning is not the whole cause: without it the model still loops in the shell without converging. A larger
turn or token budget has not been tried. An earlier 10/90 and 12/90 are void (harness image without the case-directory fix).

Budget sensitivity (#132): the same untrained model, thinking on, greedy, one run, with 24 turns, 48 tool calls and max_tokens 16384 (double the protocol; the
limits were patched into a read-only copy of `tool_probe.py`, the repo is unchanged) scored 29/90 (32.2%), complete, 60 answers empty. This is not comparable
to the students' 88-90/90, which ran under the fixed protocol (12 turns, 24 calls, 8192 tokens). It shows the 13/90 baseline was partly budget-limited, and
that the model still fails to finish most tasks at twice the budget. Served by vLLM with context 98304 and concurrency 8, 12,424 s.

Smoke (20 tasks) is at ceiling for every Python arm (20 of 20) and 19 for plain tools.

## What the numbers support

- The trained Python-shell students meet the goal: no untraced numbers on 90 held-out fits and reports. Plain-tool students and
  the untrained model do not.
- The shell beats plain tools on the held-out suite (lowest Python seed 88 against plain 75 and 76 in the two plain runs
  measured). The derived suite does not show the same gap: plain (31) sits inside the non-thinking Python range (27-31).
- Thinking vs non-thinking: on held-out, judgement, unsolvable and planning the arms are inside seed noise. On the derived suite
  thinking is higher on average (35.3 against 29.3), driven by `loading_difference` (29 of 30 against 8 of 30, where
  non-thinking gets the sign or value wrong); one thinking seed (29) sits inside the non-thinking range. Thinking wrote 5 untraced-number
  attempts across three seeds; non-thinking wrote none.
- The untrained model with the shell scores 35 on the derived suite, equal to the thinking mean: on that suite the non-thinking
  fine-tune costs accuracy, and thinking recovers it. One untrained run has no variance estimate.

- Granite 4.2 8B with the shell has the best derived score measured (37 of 40, no untraced number: the one attempt the exposure measure flagged computed a mean in Python and printed `mean88.69`, which the measure's tokeniser does not read), ahead of
  Qwen thinking (mean 35.3). One seed, so no variance estimate. The three plain-tool controls trained on older data (23, 20, 23)
  score below every Python-shell run; the plain control on the final data (31) sits inside the non-thinking Python range (27-31), so
  the shell's advantage over plain on derived accuracy is not established, while its advantage in untraced numbers
  (0 against 5-18 of 40) and on the held-out suite (88-90 against 69-76) is.

## Reading the exposure counts

`arithmetic_exposure` is a heuristic and can over-count: a program that prints a number glued to a word (`mean88.69`) makes the measure
report the copied number as untraced. One Granite attempt was checked by hand and is exactly this. The counts for the other arms were not
all audited, so treat them as upper bounds. For emitted answers the enforcement rule (`provenance.check`, the scorer's rule) is the one that
decides, and on it: non-thinking shell students 0, 0, 0 of 120 answers; Granite 0 of 40; thinking students 1, 1, 2; the untrained model 13
of 39; plain-tool students 23 of 37 and 9 of 29.

## Runtime guard

With `--enforce` the harness refuses a final answer containing a number no tool produced (see [evaluation](evaluation.md)). Derived suite,
one greedy run each, guarded against unguarded: untrained Qwen3.5-4B + shell 32 to 33 of 40 passes, emitted answers with an untraced number
13 of 37 to 0 of 38 (12 sent back, none refused); Granite 4.2 8B student 37 to 37, 0 to 0 (never fired); Qwen3.5-4B thinking student, seed 42,
37 to 37, 1 of 40 to 0 of 39 (1 sent back and refused). The guard polices emitted answers only: for the untrained model 63 of 70 untraced
numbers were in private reasoning. The thinking student scored 37 here against 39 in the original lab run of the same weights; one greedy
run on different hardware, so the difference is sampling and batching noise, not an effect.

## Known limits

- The Python shell is **not a security boundary** (a restricted `python -I` child with a module allowlist, a wall timeout and a
  memory cap). Packaging and containment are separate work and out of scope here.
- `min_voltage_outage` in the derived suite asks for "X pu" without a precision, so a two-decimal answer fails the 1e-3
  tolerance. The suite is frozen (`data/suites/derived-heldout-20261030`); a v2 suite should ask for four decimals.
- Unsolvable-task refusals vary by 9 tasks between seeds; six of the ten failures in one seed were correct refusals that
  stated a maximum line id by computing `count - 1` (an untraced number). The shell should return the highest valid id.
- Granite has one seed; Qwen3.5-4B results are lab evidence about the recipe, and the project's
  preferred base for new work is Granite 4.2 8B (Apache-2.0).

## Runbook notes, with and without (#130, 2026-11-14)

Does adding a verified runbook's notes to the prompt change neo-coder's answers on the ten timeseries dev questions? One run per question per arm,
temperature 0, prompt as it stood before PR #141 (the schema line that states the end-of-interval window removed, so the Q4 trap is open).
The selector is deterministic (token overlap with the runbook's family, parameter names and descriptions); its threshold was set to 0.10 after seeing
the scores, because the pre-set 0.15 selected only Q6. Evidence: `benchmarks/runbooks/region-window-average/notes-eval-evidence.json`.

| Arm | Passed (of 10) | Notes |
|---|---|---|
| Base prompt | 9 | Q4 fails (window ends one interval early) |
| Base prompt plus selected runbook notes | 9 | Q4 passes, Q9 fails |

The notes selected the runbook for Q4, Q6, Q7 and Q9. Q4 was fixed (the model used `> start AND <= end`), and Q9 broke: the model shifted the window back a whole
day (6114.8 against 6480.8), a wrong-day error that appeared only with the notes. Net effect: none. Limits: dev questions only and in-sample (the notes were
written after seeing the Q4 failure), one run each, ten questions, one runbook. The scorer also hides small errors: Q5 passes at 64.7063 against 64.7051 and Q7
checks only the region. This does not show that runbook notes help; it shows the mechanism runs and can both fix and cause errors.
