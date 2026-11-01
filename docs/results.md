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
| Python shell (Granite 4.2 8B, 1 seed) | 0 | 1 |

## Accuracy (passes per suite, per seed)

| Arm | Held-out (90) | Judgement (40) | Unsolvable (28) | Planning (40) | Derived (40) |
|---|---|---|---|---|---|
| Plain tools, trained, final data (1 seed) | 75 | 38 | 25 | 40 | 31 |
| Plain tools, trained, older data (3 seeds) | 76, 69, 74 | 29, 23, 27 | 28, 28, 28 | - | 23, 20, 23 |
| Python shell, non-thinking (3 seeds) | 89, 88, 90 | 40, 40, 40 | 27, 18, 20 | 40, 40, 40 | 31, 30, 27 |
| Python shell, thinking (3 seeds) | 90, 89, 85 | 40, 40, 40 | 26, 19, 26 | 40, 40, 40 | 39, 29, 38 |
| Untrained Qwen3.5-4B + shell (1 run) | - | - | - | - | 35 |
| Granite 4.2 8B + shell (1 seed) | 90 | 36 | 14 | 40 | 37 |

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

- Granite 4.2 8B with the shell has the best derived score measured (37 of 40, one untraced-number attempt in 40), ahead of
  Qwen thinking (mean 35.3). One seed, so no variance estimate. The three plain-tool controls trained on older data (23, 20, 23)
  score below every Python-shell run; the plain control on the final data (31) sits inside the non-thinking Python range (27-31), so
  the shell's advantage over plain on derived accuracy is not established, while its advantage in untraced numbers
  (0 against 5-18 of 40) and on the held-out suite (88-90 against 69-76) is.

## Runtime guard

With `--enforce` the harness refuses a final answer containing a number no tool produced (see [evaluation](evaluation.md)). One greedy run
each of the untrained Qwen3.5-4B with the shell on the derived suite: 32 of 40 unguarded and 33 of 40 guarded; answers with an untraced number
13 of 37 unguarded and 0 of 38 guarded; 12 attempts sent back, none refused. The guard polices emitted answers only: numbers written anywhere,
reasoning included, went from 23 to 22 attempts. It was not run on the trained students, where the unguarded rate is already 0 to 3 of 40.

## Known limits

- The Python shell is **not a security boundary** (a restricted `python -I` child with a module allowlist, a wall timeout and a
  memory cap). Packaging and containment are separate work and out of scope here.
- `min_voltage_outage` in the derived suite asks for "X pu" without a precision, so a two-decimal answer fails the 1e-3
  tolerance. The suite is frozen (`data/suites/derived-heldout-20261030`); a v2 suite should ask for four decimals.
- Unsolvable-task refusals vary by 9 tasks between seeds; six of the ten failures in one seed were correct refusals that
  stated a maximum line id by computing `count - 1` (an untraced number). The shell should return the highest valid id.
- Granite has one seed; Qwen3.5-4B results are lab evidence about the recipe, and the project's
  preferred base for new work is Granite 4.2 8B (Apache-2.0).
