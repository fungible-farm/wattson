# Candidate B: MetricFlow (dbt-labs/metricflow) evaluation (#128)

All numbers below are DEVELOPMENT results (the ten development questions plus six adversarial ones). The model was authored from the schema file only.
Evidence files: `/tmp/rv/eval/metricflow-results-run1.json` (copy of `out/eval/metricflow/results.json`, not committed), `/tmp/rv/eval/metricflow-run1.log`,
`benchmarks/ontology/metricflow/gold-spec-attempts.txt` (verbatim `mf` output for every inexpressibility attempt), gate output in `/tmp/mf-gates.txt` (pasted below).

## Versions, licence, install
- Resolved by pip into `docker.io/library/python:3.12-slim` (arm64): metricflow 0.213.0, dbt-metricflow 0.15.0, dbt-core 1.12.6, dbt-duckdb 1.11.1, duckdb 2.0.0 (full pin list in the gate output). Pins are in `benchmarks/ontology/metricflow/Containerfile`.
- Licence: Apache-2.0. The text I read is the `LICENSE` file shipped inside the installed `metricflow==0.213.0` wheel (copied to `/licenses/metricflow-0.213.0-LICENSE`, sha256 below). I did NOT read the file at git tag v0.213.0 (no network access to GitHub was used), so "licence at the tag" is not shown.
- Install effort: `podman build` of the image 40 s wall (pip layer, warm base image; one dependency, `dbt-core-experimental-parser`, was compiled from sdist/wheel build step), image 490 MB (490,390,837 bytes). Base image: the local cache held an amd64 `python:3.12-slim` under that tag which failed with `Exec format error` on this aarch64 host; I ran `podman pull --platform linux/arm64 docker.io/library/python:3.12-slim`, which re-tagged it in the shared store (side effect for other worktrees).
- Offline: image built with network; every `dbt`/`mf` run is in a `--network=none` container (`interfaces=lo` in the gate output). A long-lived container (`mf-eval`, started by `setup()`) is used because `mf` needs the `target/semantic_manifest.json` that `dbt parse` writes at run time.

## Gate evidence (verbatim `scripts/ontology_eval/gates.sh localhost/wattson-metricflow:local /licenses/metricflow-0.213.0-LICENSE "pip freeze | grep -iE '^(metricflow|dbt-|duckdb|dbt_)'"`)
```
host_arch=aarch64  podman_rootless=true
image=localhost/wattson-metricflow:local id=6793391b1afb5c9d6e04a60638319767ead7e74576e106e996d264ebb80f6cb1 arch=arm64/linux size_bytes=490390837
--- rootless, offline run (--network=none, uid/gid mapped to the invoking user)
uid=1000 machine=aarch64 interfaces=lo 
--- licence file /licenses/metricflow-0.213.0-LICENSE
438b69717fc3020f549d12e262421c57c5b21d38e97a36f8d9b9d68082c09974  /licenses/metricflow-0.213.0-LICENSE
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
--- pinned dependencies: pip freeze | grep -iE '^(metricflow|dbt-|duckdb|dbt_)'
dbt-adapters==1.24.5
dbt-common==1.39.0
dbt-core==1.12.6
dbt-core-experimental-parser==2.0.11
dbt-duckdb==1.11.1
dbt-extractor==0.6.0
dbt-metricflow==0.15.0
dbt-protos==1.0.615
duckdb==2.0.0
metricflow==0.213.0
```

## Authoring effort
- Files under `benchmarks/ontology/metricflow/`: 5 one-line dbt view models + 3 key-dimension views (`dim_region`, `dim_station`, `dim_interconnector`) + 2 time-spine models, `time_spines.yml` (13 lines), `dbt_project.yml` (8), `profiles.yml` (7), `Containerfile` (23), `gen_semantic_models.py` (65 lines, stdlib), generated `models/semantic_models.yml` (1,616 lines: 8 semantic models, 142 simple metrics, one per measure), adapter `scripts/ontology_eval/adapter_metricflow.py` (about 150 lines).
- The YAML is generated from a column catalogue by the 65-line script: that is how 1,600 lines were produced in minutes. A hand author would write the same volume or build such a generator. Measures: sum/avg/max/min on core MW columns, avg/max on the remaining numeric columns, price measures avg/max/min, count_distinct on keys, row counts, and one derived-expression measure (`dispatchload_cleared_energy_mwh` = `TOTALCLEARED * 5.0 / 60.0`, from the schema note).
- Time: about 10 minutes wall clock for dbt project + semantic model including three validation-fix iterations (primary-entity warning, duplicate dimension/entity pairing error, YAML colon in a description, `mf` needing key tables for entity group-bys). `authoring-log.md` is reconstructed from file mtimes and commit times, not kept contemporaneously (see its header). Adapter plus diagnosis took roughly 30 minutes more, dominated by waiting on the shared model.
- Modelling decisions that mattered: INTERVENTION = 0 is filtered inside the four staging views (the schema says always use it) rather than per metric; entities `duid` (primary on `dudetailsummary`), `station`, `region`, `interconnector` (primary on key tables).

## Run configuration
- Model `qwen3.8-flash-next`, temperature 0, `seed` = `EVAL_SEED` (default 0) is sent with every call; the llama-swap serving command has `--temp 1.0 ... --top-k 20`, recorded in the METADATA line of the log. Sampling is requested at temperature 0 per call; whether the server honours the seed with speculative decoding (MTP draft) is not shown.
- Prompt: module-level `PROMPT` (head + generated semantic-model text); sha256 12 = `16450d7336c8`; adapter sha256 12 = `73fb88b89a22` (run 1 only; the adapter was changed afterwards for max_tokens, see below).
- Semantic-model text shown to the model comes from `mf list metrics --show-all-dimensions` plus descriptions in the generated `semantic_manifest.json` (about 13,600 characters, about 4,200 prompt tokens).
- The first attempt used `mf list metrics` without `--show-all-dimensions`; it truncates group-by lists ("and 13 more"), which made valid names look invalid (adapter validation rejected `duid__regionid`). Fixed before run 1.
- Run 1 `max_tokens` = 3000: M1 returned an empty reply (reasoning consumed the budget, 83 s) on repeats 1 and 2. The adapter was then changed to `max_tokens` 8000 (committed before the freeze); the 10-repeat rerun uses it.
- Run 1 happened while other agents shared llama-swap (the coordinator reported a 15-minute blockage by another agent's model probe; whether run 1 overlapped it is not shown: run 1 started after I acquired the GPU lock, and its first call was a 83 s model load). No call in run 1 recorded an `error` outcome (0 errors per `coverage_per_repeat`), so I did not re-run for that reason; I re-ran M1 etc. only because of the empty replies.

## Harness SUMMARY (run 1, development results, verbatim)
```
SUMMARY {"adapter": "adapter_metricflow.py", "questions": 10, "repeats": 3, "accuracy_per_repeat": [5, 3, 3], "accuracy_mean": 0.367, "latency_p50": 26.3, "latency_p95": 83.3, "stable_answers": "3/10", "adversarial_passed": "4/6", "accuracy_by_dimension": {"shape": {"cross-fact-join": "0/3", "multi-step": "0/3", "post-aggregate-filter": "0/3", "ranking-share": "0/3", "simple-aggregate": "11/18"}, "convention": {"no-convention": "6/18", "tests-a-convention": "5/12"}}, "coverage_per_repeat": [{"asked": 10, "correct": 5, "wrong": 0, "refused": 5, "error": 0, "coverage": 0.5, "conditional_accuracy": 1.0}, {"asked": 10, "correct": 3, "wrong": 1, "refused": 6, "error": 0, "coverage": 0.4, "conditional_accuracy": 0.75}, {"asked": 10, "correct": 3, "wrong": 1, "refused": 6, "error": 0, "coverage": 0.4, "conditional_accuracy": 0.75}], "failure_layer": {"M1": "model planning, unstable (passes on some repeats)", "M2": "semantic representation (not expressible in the candidate's model)", "M3": "scoring (the answer holds every expected value within 1%; the strict scorer rejected it)", "M4": "semantic representation (not expressible in the candidate's model)", "M5": "none", "M6": "none", "M7": "semantic representation (not expressible in the candidate's model)", "M8": "semantic representation (not expressible in the candidate's model)", "M9": "semantic representation (not expressible in the candidate's model)", "M10": "none"}, "path_b_passed": "5/10", "rerun_with_10_repeats": ["M1", "M2", "M3", "M4", "M7", "M8", "M9"]}
```
Capability is reported as coverage with conditional accuracy: coverage 0.5 / 0.4 / 0.4, conditional accuracy 1.0 / 0.75 / 0.75 per repeat. The model refused (`cannot_answer`) rather than guessing on the inexpressible questions, which is the behaviour a governed layer should show; the single wrong answer is M3 (below).

## Per-question results (3 repeats)
| Q | rep0 | rep1 | rep2 | Path B (gold spec) | failure layer / note |
| --- | --- | --- | --- | --- | --- |
| M1 | PASS | FAIL (refused: empty model reply) | FAIL (refused: empty model reply) | PASS | model/interface: empty reply at `max_tokens` 3000 |
| M2 | FAIL refused | FAIL refused | FAIL refused | inexpressible | semantic representation (attempt M2-a) |
| M3 | PASS | FAIL wrong | FAIL wrong | PASS | the model added `metric_time__day` to the group-by; SETTLEMENTDATE is the END of the interval, so the interval ending 2025-09-02 00:00 falls into day 09-02 and the day-1 total is 169,841.2 instead of 170,416.5 (0.34% off). The harness labels this "scoring"; I record it as a WRONG answer caused by an unmodelled convention (interval end vs day bucket), not a scorer artefact |
| M4 | FAIL refused | FAIL refused | FAIL refused | inexpressible | semantic representation (M4-a) |
| M5 | PASS | PASS | PASS | PASS | |
| M6 | PASS | PASS | PASS | PASS | |
| M7 | FAIL refused | FAIL refused | FAIL refused | inexpressible | semantic representation, single spec (attempts below) |
| M8 | FAIL refused | FAIL refused | FAIL refused | inexpressible | semantic representation, single spec (attempts below) |
| M9 | FAIL refused | FAIL refused | FAIL refused | inexpressible | semantic representation (attempts below) |
| M10 | PASS | PASS | PASS | PASS | |

Path B (no model): 5 of 10 pass (M1, M3, M5, M6, M10 with the gold specs in `gold-specs.json`); the five others are recorded as inexpressible with the attempts below. Rows hash stable across repeats: 3/10 (M5, M6, M10).

### Adversarial (6)
| id | kind | result | detail |
| --- | --- | --- | --- |
| A1 | column does not exist (carbon intensity) | PASS | refused, no such metric |
| A2 | out of window (2025-10-15) | FAIL | answered: the query ran and returned one row containing NULL (an empty AVG), no refusal; the semantic model has no knowledge of the data window |
| A3 | ambiguous ("used the most energy") | FAIL | silently chose total demand and returned NSW1; no hint or clarification |
| A4 | write attempt | PASS | refused |
| A5 | prompt injection | PASS | refused |
| A6 | undefined concept (merit order) | PASS | refused |

## Inexpressible questions: tested, not assumed (verbatim output in `benchmarks/ontology/metricflow/gold-spec-attempts.txt`)
Formulations tried: plain group-by/order/limit, multi-metric specs, entity group-bys (`duid`), multi-hop entities (`duid__region__regionid`), where on dimensions and entities, `Metric()` filters (metric-as-filter), time-dimension filters. Saved queries, derived/ratio metrics and conversion metrics were NOT executed: a saved query is a fixed pre-authored spec (it would be a question-specific artefact, disallowed), and a ratio metric whose numerator is a ranked subset cannot be defined without a literal list (question-specific). That is a reasoning, not a test, and is labelled so.
- M2 (distinct units with TOTALCLEARED > 0 in some interval): `Dimension('dispatchload_record__totalcleared')` does not exist; values are modelled as measures. Failed attempt M2-a. Would need a dimension on every numeric column or a bespoke filtered measure.
- M4 (NSW1 price exceeds VIC1 price by 10 while the interconnector flows forward): M4-a returns each region as separate rows; no per-interval comparison across regions or tables exists. Not expressible.
- M7 (unit with the highest MW at the interval of the highest SA1 price): step 1 alone works (M7-a: 2025-09-01 11:20, 760.19), step 2 alone works with the literal (M7-e: PPCCGT 406.697, equal to the ground truth), but a single spec fails: M7-b (price and load metrics together: "does not match to a common item", region paths differ: `region__regionid` vs `duid__region__regionid`), M7-d and M7-f (`Metric()` filter: unsupported group-by). So it is expressible only as two dependent calls with a value handed over, which is orchestration outside MetricFlow. Failure layer: semantic representation under the single-spec contract.
- M8 (share of the five largest NSW1 units): top-5 list works (M8-a), the numerator with that literal list works (M8-d: 73,955.3) and the denominator works (M8-e: 169,402.6; 73,955.3 / 169,402.6 = 0.4366), but it takes three calls plus client arithmetic; a `Metric()` filter takes only value thresholds (M8-b), and an `IN (SELECT ...)` form returned an empty result without error (M8-c, meaningless). Failure layer: semantic representation under the single-spec contract.
- M9 (intervals where the all-region NETINTERCHANGE sum has absolute value above 50): `Metric(..., group_by=['metric_time'])` and `['metric_time__minute']` are refused (only `region` offered); the region-grouped variant runs but answers another question (M9-b, 864 rows). Not expressible.
- MetricFlow cannot do: sub-queries, argmax-then-filter, per-row cross-table comparison, filter on an aggregate over a derived grouping, rank-defined subsets. It is built for governed aggregate-by-dimension queries, which M1, M3, M5, M6, M10 are.

## Requirement observations
| Req | Verdict | Evidence |
| --- | --- | --- |
| R1 semantic model as data | PASS | `models/semantic_models.yml` (YAML) -> `target/semantic_manifest.json`; `mf list metrics --show-all-dimensions` produces the text shown to the model |
| R2 binding checks | PASS | unknown metric, unknown group-by and unknown where field all fail at resolution with an error and suggestions (probe in attempts file and run log; M2-a prints the available names); the adapter additionally validates names before calling `mf` |
| R3 constraint validation | PASS | `mf validate-configs`: 12 validation groups report `ERRORS: 0` (parse, semantics, then data-warehouse checks of semantic models, dimensions, entities, measures, metrics); a deliberately broken copy (metric pointing to a missing measure) fails with `A semantic model having a measure 'no_such_measure' does not exist but was referenced` |
| R4 governed path and refusals | PARTIAL | 4/6 adversarial pass; A2 (out of window) and A3 (ambiguous) answered. Safety finding: the `where` argument is raw text into the SQL: `--where "... = 'SA1'; DROP TABLE dispatchprice" --explain` printed generated SQL ending `WHERE region__regionid = 'SA1'; DROP TABLE dispatchprice`, i.e. a stacked statement is passed through (only `--explain` was run, nothing executed). My adapter rejects `;`, `--`, `/*` and write keywords in the where string (not part of MetricFlow); the DuckDB file is mounted read-write at `/work`. A governed deployment needs a read-only connection |
| R5 hints on ambiguity | NOT-SHOWN | no hint mechanism observed; A3 was answered silently; resolution errors list "Suggestions" for names (see M7-d output) which are name hints, not ambiguity hints |
| R6 audit and replay | PASS | every answer records the `mf query` args and the compiled SQL (`query` field); the compiled SQL hash was identical in 3 consecutive `--explain` runs of the same spec; the rows hash was stable 3/3 on M5, M6, M10; the Path B gold specs reproduce the model-chosen specs for those questions. Whole-run stability is limited by the model: 3/10 stable |
| R9 container/offline/arch | PASS | gate output: arm64, rootless, `--network=none`, `interfaces=lo`, image 490 MB; base-image arch trap described above |
| R10 latency | PARTIAL | `mf query` about 1.6 s per call (measured with `date`, including process start and a `--decimals` print); Path B 2.8 to 2.9 s per question including `--explain`; Path A p50 26.3 s, p95 83.3 s, dominated by the model (reasoning tokens) and contention on the shared server, not by MetricFlow |
| R11 scale notes | NOT-SHOWN | only 150k rows tested; the semantic model is verbose: 142 metrics and about 13,600 characters of model text, about 4,200 prompt tokens for five tables; the generator, not hand authoring, is what makes that tractable. Larger models would need retrieval over metrics |

## Honest assessment
Strengths: deterministic governed compilation, errors at resolution, one-line audit, offline arm64 image, Apache-2.0, easy to generate from a schema catalogue, refuses by itself (rather than guessing) on the questions it cannot express and on 4 of 6 adversarial prompts.
Weaknesses: only 5 of the 10 development questions are expressible at all (Path B 5/10); the five that are not are exactly the multi-step, cross-table and rank-based ones; unmodelled conventions bite (M3 day bucket by interval end); ambiguity and out-of-window requests are answered silently; `where` is unsanitised SQL text; deploying needs dbt plus a dbt-compatible warehouse project (a larger footprint than a bare semantic layer) and a runtime `dbt parse` before `mf` works.

## 10-repeat rerun of the questions flagged by the harness (M1, M2, M3, M4, M7, M8, M9)
Started with `--repeats 10 --only M1,M2,M3,M4,M7,M8,M9 --out out/eval/metricflow-rerun` (adapter with `max_tokens` 8000). At the time of writing it was still queued behind other agents' runs on the shared GPU lock; its results are written to `out/eval/metricflow-rerun/results.json` (log `/tmp/mf-rerun.log`) and are NOT included here.
