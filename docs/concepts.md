# Concepts: procedure, not luck

## The claim

A model that is good at an engineering task is not one that guesses well. It is one that follows a procedure whose every step can be
checked. WattsOn is built around that: **planning, study and test protocols are enforced by code, not left to chance or to good
intentions.** If a rule matters, a program checks it, and the check can be re-run by anyone.

## Where each rule lives

| Rule | Enforced by |
|---|---|
| The model never does arithmetic | a restricted Python shell with a solver; `arithmetic_exposure` counts any number the model wrote that no prompt, tool reply or program output gave it |
| Every number in an answer is traceable | the provenance gate in the scorer rejects an answer containing an untraced number |
| An answer is right because the physics says so | the scorer re-solves the network at the answered value; it never compares text |
| A task that cannot be solved is refused, not guessed | `solver.exists()` is called first; the unsolvable suite scores refusals |
| What the model sees in training is what it sees in use | `parity_check` replays trajectories through the real harness and compares the prompts token for token |
| Training data is the data the manifest says | the trainer hashes it before it trains; `data/MANIFEST.json` covers every file |
| Held-out means held out | frozen suites with recorded hashes; the harness audits tasks against the training rows |
| A result is not claimed after the fact | the rule for "the shell beats plain tools" was written down before any seed finished (below) |
| The model is not trusted to round | reports are copied from `report_text`, printed by the tool at the required decimals |

None of these depends on a model's judgement, and none uses a model to measure a model. A step is "complete" only if its outcome is an
observation a program can produce again.

## Study and practice

The analogy is a student preparing for an exam, and it maps closely:

| Student | Here |
|---|---|
| worked examples, done correctly | oracle trajectories: a deterministic script solves each task with the real solver and the model is trained on the exact procedure |
| practising on your own mistakes | the DAgger relabeller: a student's own failed attempt becomes a training example with the correct next step |
| a fixed study method, not whim | the oracle is a procedure (check existence, search in code, copy the report sentence), the same for every task of a kind |
| sitting an exam you have not seen | frozen suites on networks no training row used |
| the exam marked by a key, not by opinion | re-solving, not text matching |

## What the evidence says, including where it disagrees

The pre-registered rule, written before any seed finished: *the Python shell beats the matched plain-tools control on the held-out 90
only if the lowest Python seed's pass count exceeds the highest plain seed's.* It held: the six Python-shell seeds scored 85 to 90, the plain-tools controls 69 to 76.

Practice moves two things we can measure:

- **How much the model computes itself.** Plain-tool students put a number nothing had provided in 85 to 87 of 90 held-out attempts. Shell
  students: 0 of 90. See [results](results.md).
- **Accuracy on tasks like the ones practised.** The shell students pass the practised kinds of task at or near ceiling.

Practice is not free, and the same data says so. On the derived-arithmetic suite, which asks for things nobody practised (a difference of two
outages' loadings, a mean, a ranking by minimum voltage), the **untrained** Qwen3.5-4B with the shell scored 35 of 40, above the
non-thinking trained students (27 to 31). Training narrowed what the model does by default; scripted reasoning in the thinking variant gave
some of it back (29 to 39, three seeds). A student who has drilled one question type can do worse on a neighbouring one. That is why the
frozen, unseen suites exist.

## PowerAgent

WattsOn is the first part of a larger idea, **PowerAgent**: a package of tools, skills and encoded knowledge, shipped as bundles that
an agent loads. In this repository the three are:

| | Here |
|---|---|
| tools | the MCP tool server (`run_study_trial`, `run_python`, `load_network`, `run_power_flow`) |
| skills | the procedures: the oracle scripts and the system prompt the students were trained to follow |
| encoded knowledge | `data/`: the tasks, suites and trajectories, each with a manifest |

How these are bundled and distributed as a separate package is not defined here and is not implemented. This repository is the
evidence and the working parts.
