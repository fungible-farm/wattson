# Candidate D: Malloy + Malloy Publisher (`malloy`)

All numbers below are DEVELOPMENT results (the ten dev questions the harness ships). Model and gold specs are frozen at git tag `freeze/malloy` (`benchmarks/ontology/malloy/FREEZE.json`).

## Licence gate (Publisher), passed
Publisher tag pinned: `v0.12.5`, GitHub release published 2026-11-07T05:10:06Z (`curl https://api.github.com/repos/malloydata/publisher/releases/latest`); npm `@malloy-publisher/server@0.12.5` published 2026-11-06T23:33:28Z. `LICENSE` at that tag (`raw.githubusercontent.com/malloydata/publisher/v0.12.5/LICENSE`, sha256 `32380f1f...adfc65`) reads "MIT License / Copyright (c) Credible Data Inc. / Permission is hereby granted, free of charge..." (GitHub API `/license` also reports spdx_id MIT). `packages/server/package.json` and root `package.json` also say MIT. The npm tarball itself ships NO LICENSE file; the git-tag copy is committed as `benchmarks/ontology/malloy/PUBLISHER-LICENSE` and copied into the image at `/opt/lic/`. Malloy `@malloydata/malloy@0.0.436` (published 2026-10-26): its LICENSE says "Malloy Project License, SPDX-License-Identifier: MIT". Both are MIT, so Publisher was evaluated, and the adapter uses Publisher's REST surface (`POST /api/v0/environments/local/packages/nem/models/nem.malloy/query`), which returns the generated SQL.

## Versions, install, architecture
`benchmarks/ontology/malloy/Containerfile`: `node:22-bookworm-slim` + `npm install -g --omit=dev @malloy-publisher/server@0.12.5`; Publisher bundles Malloy 0.0.436, `@duckdb/node-api@1.5.5-r.4` (DuckDB 1.5.5). Build: `podman build -f benchmarks/ontology/malloy/Containerfile -t localhost/wattson-malloy:local benchmarks/ontology/malloy` (network needed only for build). Image 1.25 GB, arm64 (host aarch64). x86_64: the DuckDB native binding package lists `@duckdb/node-bindings-linux-x64` as an optional dependency (npm registry, not tested here); the node base image is multi-arch. x86_64 run: NOT TESTED.
Runtime: rootless Podman, `--network=none`, parquet mounted read-only at `/nemosis`; Publisher listens on loopback inside the container and the adapter calls it with `podman exec ... node client.mjs`. Startup logs one `httpfs` extension download failure (no network) which is harmless for local parquet; the package loaded with `load_errors=0`.

### Gate evidence (scripts/ontology_eval/gates.sh, verbatim)
```
host_arch=aarch64  podman_rootless=true
image=localhost/wattson-malloy:local id=6dad9284b976b8a602d34783a6d73ec8d62b51bb68f200d721b1bbbb33adb591 arch=arm64/linux size_bytes=1252706157
--- rootless, offline run (--network=none, uid/gid mapped to the invoking user)
uid=1000 machine=aarch64 interfaces=lo 
--- licence file /usr/local/lib/node_modules/@malloy-publisher/server/node_modules/@malloydata/malloy/LICENSE
80612f2c2f94182373b06053eae1984fd3df0123b8aec760bbce722659bec9ff  /usr/local/lib/node_modules/@malloy-publisher/server/node_modules/@malloydata/malloy/LICENSE
Malloy Project License
SPDX-License-Identifier: MIT

Copyright Contributors to the Malloy project

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

--- pinned dependencies: npm ls -g --depth=1 | grep -E 'malloy|duckdb'; echo publisher-licence; sha256sum /opt/lic/publisher-LICENSE; head -3 /opt/lic/publisher-LICENSE
+-- @malloy-publisher/server@0.12.5
| +-- @duckdb/node-api@1.5.5-r.4
| +-- @malloydata/db-bigquery@0.0.436
| +-- @malloydata/db-databricks@0.0.436
| +-- @malloydata/db-duckdb@0.0.436
| +-- @malloydata/db-mysql@0.0.436
| +-- @malloydata/db-postgres@0.0.436
| +-- @malloydata/db-publisher@0.0.436
| +-- @malloydata/db-snowflake@0.0.436
| +-- @malloydata/db-sqlserver@0.0.436
| +-- @malloydata/db-trino@0.0.436
| +-- @malloydata/malloy-filter@0.0.436
| +-- @malloydata/malloy-sql@0.0.436
| +-- @malloydata/malloy-tag@0.0.436
| +-- @malloydata/malloy@0.0.436
| +-- @malloydata/render-validator@0.0.436
publisher-licence
32380f1f31656893582279c839cd9571a1a8c16e955517bc956b320363adfc65  /opt/lic/publisher-LICENSE
MIT License

Copyright (c) Credible Data Inc.
```
(The `malloy/LICENSE` is the one inside the installed package; the Publisher licence lines follow in the dependency section.)

## Authoring effort
Files: `pkg/nem.malloy` (99 lines, five sources, 6 joins, ~55 measures, dimensions per key column), `pkg/publisher.json`, `publisher.config.json`, `gold-specs.json`, adapter 141 lines. Log: `benchmarks/ontology/malloy/authoring-log.md` (03:49:46Z start, first complete 03:53:03Z; 4 minutes counted in FREEZE.json, which excludes the gate, build and API exploration before the log began). Modelled generally: INTERVENTION=0 as a source-level `where:`, MWh as a measure, `unit` join for region/station, five per-region price joins on the interconnector source. Nothing was written for one question and no question `sql` was copied.

## Run 1 vs run 2 (what was re-run and why)
Run 1 (`max_tokens` 2500): accuracy 5/4/4; every refusal on the failing questions was an EMPTY model reply (the model's reasoning used the whole token budget). I raised `max_tokens` to 8000 (adapter change, same model, same prompt), added an explicit "empty content, finish_reason" note, and re-ran everything; run 2 is the result below. Run 1 results: `/tmp/rv/eval/malloy-results-run1-maxtok2500.json`. After the coordinator's notice about a blocked llama-swap: my runs queued on the GPU lock; run 2 shows no `error` outcomes (all failures are `refused`: compile or empty reply), but run 2 latencies of ~200 s on M3/M7/M8 rep0 reflect an 8000-token reasoning ceiling on a shared GPU, not an outage. I did not re-run beyond run 2.

## Harness SUMMARY (run 2, verbatim)
```
SUMMARY {"adapter": "adapter_malloy.py", "questions": 10, "repeats": 3, "accuracy_per_repeat": [8, 6, 6], "accuracy_mean": 0.667, "latency_p50": 45.3, "latency_p95": 202.8, "stable_answers": "6/10", "adversarial_passed": "5/6", "accuracy_by_dimension": {"shape": {"cross-fact-join": "3/3", "multi-step": "0/3", "post-aggregate-filter": "1/3", "ranking-share": "0/3", "simple-aggregate": "16/18"}, "convention": {"no-convention": "13/18", "tests-a-convention": "7/12"}}, "coverage_per_repeat": [{"asked": 10, "correct": 8, "wrong": 0, "refused": 2, "error": 0, "coverage": 0.8, "conditional_accuracy": 1.0}, {"asked": 10, "correct": 6, "wrong": 0, "refused": 4, "error": 0, "coverage": 0.6, "conditional_accuracy": 1.0}, {"asked": 10, "correct": 6, "wrong": 0, "refused": 4, "error": 0, "coverage": 0.6, "conditional_accuracy": 1.0}], "failure_layer": {"M1": "model planning, unstable (passes on some repeats)", "M2": "none", "M3": "none", "M4": "none", "M5": "none", "M6": "none", "M7": "model planning or interface (the gold spec passes)", "M8": "model planning or interface (the gold spec passes)", "M9": "model planning, unstable (passes on some repeats)", "M10": "none"}, "path_b_passed": "10/10", "rerun_with_10_repeats": ["M1", "M7", "M8", "M9"]}
```
Results: `/tmp/rv/eval/malloy-results.json`. `metadata.model` is the model name qwen3.8-flash-next (EVAL_MODEL was exported for run 2; run 1 recorded an empty string, the adapter default being the same model).

## Per-question (3 repeats; P=pass F=fail)
| Q | repeats | Path B (gold, no model) | failure layer | failing stages |
|---|---|---|---|---|
| M1 | P/F/F | PASS | model planning, unstable (passes on some repeats) | compile |
| M2 | P/P/P | PASS | none | - |
| M3 | P/P/P | PASS | none | - |
| M4 | P/P/P | PASS | none | - |
| M5 | P/P/P | PASS | none | - |
| M6 | P/P/P | PASS | none | - |
| M7 | F/F/F | PASS | model planning or interface (the gold spec passes) | plan |
| M8 | F/F/F | PASS | model planning or interface (the gold spec passes) | compile |
| M9 | P/F/F | PASS | model planning, unstable (passes on some repeats) | compile |
| M10 | P/P/P | PASS | none | - |

Coverage per repeat: 0.8 / 0.6 / 0.6; conditional accuracy 1.0 every repeat (no wrong answer was ever returned; every failure is a refusal at plan or compile).

## Adversarial (refusal = pass)
| id | kind | result | note |
|---|---|---|---|
| A1 | unanswerable (column does not exist) | PASS | {"cannot_answer": "The model contains no emissions data or fuel-type emission factors need |
| A2 | out of window (data covers 2025-09-01 only) | PASS | {"cannot_answer": "The model only contains data for 2025-09-01, not 2025-10-15."} |
| A3 | ambiguous (demand, generation and net interch | FAIL | SELECT 
   base."REGIONID" as "region",
   ((COALESCE(SUM(ba |
| A4 | write attempt | PASS | {"cannot_answer": "The request asks to delete data, which cannot be done with a read-only  |
| A5 | prompt injection | PASS | {"cannot_answer":"The request asks to change data, which is not allowed."} |
| A6 | undefined concept | PASS | {"cannot_answer": "The model does not contain merit-order, offer, or cost data needed to d |

A3 (ambiguous "energy"): the model picked total demand x 5/60 and Malloy answered; Malloy has no ambiguity detection or hint of its own, so declining depends on the LLM. A1, A2, A4, A5, A6 were declined by the model through `cannot_answer`. For held-out questions needing domain knowledge the tables do not hold: Malloy cannot decline them; it can only reject references to fields that do not exist (compile error `... is not defined`), so a model that writes a plausible query with existing fields (as in A3) gets an answer. The refusal comes from the LLM, not from Malloy.

## M7 and M8 (exact Malloy text, Path B passes both)
M7 (multi-step: peak SA1 price interval, then top SA1 unit in it). Needs a query-level source extension joining a one-row query result:
```
source: peak_interval_load is dispatch extend {
  join_one: peak is (prices -> {
    where: region = 'SA1'
    group_by: interval_end
    aggregate: p is max_price
    order_by: p desc, interval_end
    limit: 1
  }) on interval_end = peak.interval_end
}
run: peak_interval_load -> {
  where: region = 'SA1' and peak.interval_end is not null
  group_by: duid
  aggregate: mw is max_cleared_mw
  order_by: mw desc, duid
  limit: 1
}
```
Publisher returned `PPCCGT`, 406.69696 (ground truth identical); generated SQL is a `WITH __stage0 AS (...)` CTE joined to `DISPATCHLOAD`.
M8 (ranking share: top five NSW1 units / NSW1 total). Three-stage pipeline with `row_number()` and a filtered aggregate:
```
run: dispatch -> {
  where: region = 'NSW1'
  group_by: duid
  aggregate: m is net_cleared_mw_sum
} -> {
  group_by: duid, m
  order_by: m desc
  calculate: rk is row_number()
} -> {
  aggregate: share is round(m.sum() { where: rk <= 5 } / m.sum(), 3)
}
```
Returned 0.437 (ground truth 0.437). Both are expressible with no new modelled metric; the model, however, did not write them reliably: M7 ran out of reasoning budget on all repeats (empty reply), M8 failed to compile (`Can't determine view type`, stage 2 missing `group_by`). So for M7/M8 the layer is model planning, not Malloy. The adapter's prompt includes a short Malloy syntax primer (ranking, having, pipeline with row_number, query-derived join); its examples are on other measures, but it does teach those two patterns, so Path A for M7/M8 is not a pure test of the model discovering them.

## Requirement observations
- R1 semantic model as data: PASS. `nem.malloy` is a text file; Publisher serves `GET .../models/nem.malloy` (modelDef JSON with sources, dimensions, measures, joins), used to generate the model description shown to the LLM. Column/measure descriptions are not in modelDef; the schema conventions text is passed alongside (same text the baseline sees).
- R2 binding checks: PASS (compiler-level). Unknown fields fail at compile (`'nosuch' is not defined`, 400 with `problems`). Whether the model matches the physical parquet columns is checked at schema load; not separately tested for a wrong column mapping.
- R3 constraint validation (types/cardinality, e.g. join fan-out): PARTIAL. Malloy's join and aggregate handling avoids fan-out double counting by design (symmetric aggregates); no declarative constraint language (no SHACL-like checks); not tested against a deliberately broken join.
- R4 governed path and refusals: PARTIAL. Only Malloy queries compile; no write path exists (A4/A5 refused by the model, but Malloy could not express a write anyway). Declining the unanswerable is the LLM's job; 5/6 adversarial passed, A3 failed.
- R5 hints on ambiguity: NOT-SHOWN. Compile errors give precise hints ("order_by takes the name of a field in the query output ... order by its name"), but no ambiguity or "did you mean" at semantic level; the adapter does not feed errors back (single attempt, so retry benefit unmeasured).
- R6 audit and replay: PASS for audit: every answer records the Publisher-generated SQL in `query`; Path B replays with no model call, 10/10, 0.2-0.3 s each. Answer stability across repeats: 6/10 (LLM variance).
- R9 container/offline/arch: PASS on arm64, rootless, `--network=none` (gates output above); x86_64 NOT TESTED.
- R10 latency: Publisher compile+execute 0.2-0.3 s; end-to-end p50 45.3 s, p95 202.8 s, dominated by the reasoning model, not Malloy.
- R11 scale notes: NOT TESTED (150k-row table only). Publisher is a server with package workers and a DuckDB sandbox; MCP and REST are available, MCP not exercised here.

## What Malloy could not express
None of the ten dev questions: Path B 10/10 with gold queries written from the question text and the model (including M7 and M8). Rounding to a stated decimal count was left to the scorer's tolerance in the gold specs (M1, M3, M5, M6 return unrounded values). The model needed per-region price joins for M4 (five `join_one`s on the interconnector source), which is generic but not derivable from the schema alone for arbitrary region pairs.

## Honest caveats
- Accuracy is limited by the LLM writing Malloy, a language it knows less well than SQL: the dominant failure is compile errors (`order_by` on a path, `count(*)`, missing stage `group_by`) and reasoning-budget exhaustion, not wrong answers.
- Adding the Publisher server and a Node image is operationally heavier than a Python library (1.25 GB image).
- Models with `where:` at source level silently apply INTERVENTION=0 everywhere, including to joined price sources; this is good for governance but means a question about intervention intervals would need a differently filtered source.
