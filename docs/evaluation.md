# Evaluation

## Labs 1-3: deterministic benchmarks

The labs are small, fully deterministic power-system tasks with committed expected results. They need no model and no GPU; they check
that the solver stack reproduces on your machine.

| Lab | Task | Check |
|---|---|---|
| `benchmarks/lab01-simple-loadflow-fit` | fit a load-scale factor so one bus voltage matches a field reading, by bisection around a real `pandapower.runpp()` | `wattson lab 1` |
| `benchmarks/lab02-medium-interconnection-screening` | N-1 screening of a candidate generator on a 1803-bus case, with a human-approval gate on the memo | `wattson lab 2` |
| `benchmarks/lab03-advanced-provider-bakeoff` | compare several proposers (deterministic and model-driven) on the same fitting tasks | `wattson lab 3` |

`just labs` runs all three in the container. Each prints `MATCH` against its `expected_*.json`. The same fixtures match on aarch64 and
x86_64. The `pypowsybl` second-engine cross-checks in labs 2 and 3 run only on x86_64 (no aarch64 wheel exists) and are skipped elsewhere.

The labs show the point of this project in miniature: something *proposes* a trial value and the real physics *evaluates* it. The
physics is never in the proposer.

## Frozen suites: scoring a model

```bash
just eval smoke          # also: heldout | judgement | unsolvable | planning | derived
```

| Suite | Tasks | Measures |
|---|---|---|
| `heldout` | 90 | fits on networks no training row used: the primary endpoint |
| `smoke` | 20 | a quick gate |
| `judgement` | 40 | reports and choosing the worst outage |
| `unsolvable` | 28 | refusing a task that names an element that does not exist |
| `planning` | 40 | worst-of-three-outages on unseen networks, one unseen wording |
| `derived` | 40 | arithmetic over several solver results: nothing in any tool reply is the answer |

Each task is graded by **re-solving the network** (a fit passes if the answered value reaches the target within tolerance), not by
comparing text. A report passes if each solver value appears in the answer within tolerance after every number is normalised to four
decimals (voltages within 1e-3 pu are the same voltage). Every number in an answer must also trace to the prompt, a tool reply or a
printed result. `wattson.inference.arithmetic_exposure` separately counts numbers the model wrote that nothing gave it.

Output goes to `out/eval-<suite>-<time>/` in the directory you started from: a `summary.json` and one journal per task recording every
model message and tool reply. A journal is enough to re-score an attempt on another machine without a model; the smoke check does this
for 20 recorded attempts.

## Reading the results

[results.md](results.md) has the tables. Read the seeds, not just the means: the unsolvable suite moves by up to 9 tasks between seeds of the
same recipe. A difference smaller than that is not a finding.
