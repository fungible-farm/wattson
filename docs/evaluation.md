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

## Runtime guard: refusing an unverified number

Training makes a model compute with the shell; it does not make it certain. `--enforce` (`just eval derived --enforce`,
`just ask "..." --enforce`) adds a runtime check to the harness. Before a final answer is accepted, every number in it is traced with the
scorer's own provenance rule. If any number came from no tool and not from the prompt, the model is sent back with a message naming the
numbers and telling it to compute them with `run_python`, at most twice. If it still gives an untraced number, the attempt ends as
`guard_refused` and **no answer is emitted**. The run manifest records `enforce_provenance`, so a guarded run is never mistaken for an
unguarded one, and the default is off, so every number in [results](results.md) describes the unguarded harness.

**What it guarantees, and what it does not.** It guarantees that no emitted answer contains a number nothing verified. It does not stop a
model computing in its private reasoning or inside the code it writes, and it cannot make a wrong answer right. Measured on the derived suite,
one greedy run each, through the pod:

| Model | Passes (of 40), unguarded to guarded | Emitted answers with an untraced number | Sent back | Refused |
|---|---|---|---|---|
| Untrained Qwen3.5-4B + shell | 32 to 33 | 13 of 37 to 0 of 38 | 12 | 0 |
| Granite 4.2 8B student | 37 to 37 | 0 of 40 to 0 of 40 | 0 | 0 |
| Qwen3.5-4B thinking student (seed 42) | 37 to 37 | 1 of 40 to 0 of 39 | 1 | 1 |

For the untrained model the guard turned in-head sums into shell computations at no cost in accuracy. The Granite student never needed it. The
thinking student needed it once: it kept giving an untraced number after being sent back, so the attempt failed closed instead of emitting
the number. The numbers a model writes in its private reasoning are not policed: for the untrained model 63 of its 70 untraced numbers were
in reasoning, which no answer-side guard can touch.

## Reading the results

[results.md](results.md) has the tables. Read the seeds, not just the means: the unsolvable suite moves by up to 9 tasks between seeds of the
same recipe. A difference smaller than that is not a finding.
