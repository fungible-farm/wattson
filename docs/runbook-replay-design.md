# Runbook replay: design for #130 step 2 (proposal, for review before code)

Status: proposal. Nothing here is built except the leakage lint (PR #142). Written 2026-11-14.

## Goal

Turn a verified success into a reusable runbook, and accept it into a catalog only after a fresh, model-free replay passes the same strict scorer.
Terminal-RSI's loop (verified success, runbook, leakage review, fresh verified execution) mapped onto the existing harness (`scripts/ontology_eval.py`).

## What already exists

- Adapter interface: `answer(question) -> {rows, query, calls, note, stage}` and an optional `run_spec(spec)` that executes a gold specification through the candidate's own path with no model call (Path B).
- Strict scorer `nem_scoring.score`, ground truth by running each question's `sql` in the no-network DuckDB container.
- Leakage lint `scripts/runbook_leakage_lint.py`: question text, SQL and gold values may not appear in a runbook.

## Proposal

1. **Runbook file** (JSON, one per verified success): `id`, `family` (shape/convention tags from the question set), `source` (adapter, run id, sha256 of the journal row), `parameters` (named slots, e.g. region, threshold, period), `sql_template` (SQL with slots), `steps` (ordered, each with `cmd:` and `pass:` so it can be drawn as a D2 chart by the existing generator), `notes` (conventions the success relied on, such as end-of-interval timestamps).
2. **`--replay-runbook FILE`** in the harness: no model call. Fills the slots from a question's declared parameters, runs the SQL in the DuckDB container, scores with the strict scorer. It is `run_spec` with a runbook as the spec.
3. **Admission** to the catalog needs all of: leakage lint PASS; replay PASS on the question it was drafted from; replay PASS on at least two *sibling* questions of the same family that were not used to draft it (different parameters). Otherwise the runbook is stored as `rejected` with the reason.
4. **Gold values file**: a small script that runs each gold SQL and writes `{id, question, sql, values}`, so the lint's `values` check has something to check. Deterministic, no model.
5. **Catalog**: `benchmarks/runbooks/catalog.json` with `accepted` and `rejected` lists, each entry pointing to its runbook file and the verdict evidence (command, exit code, scorer output). Selection by family membership, not by a model (REQ-012).
6. **Golden-path drafts** (#130 item 3) come from accepted runbooks: the `steps` become a D2 chart that `just diagrams-check` validates.

## Main risk, and how this design answers it

A runbook that stores the SQL for one question and replays it will pass that question. That is a cache, not learning. The sibling-question rule in step 3 is what separates a procedure from a stored answer: it must work on questions it was not drafted from. Even so, with ten questions and few siblings per family the evidence is thin; results should be reported per family with counts, never as one accuracy figure.

A second risk: drafting a runbook from a held-out question leaks the held-out set. Drafting uses dev questions only; held-out questions may only be used as replay siblings after the runbook is frozen (hash recorded before the replay).

## Open decisions for the owner

1. Is a sibling-replay rule (two or more unseen questions per family) the right admission bar, or should a human review each runbook?
2. Where do runbooks live: in this repo (`benchmarks/runbooks/`) or in a per-client registry, given the direction in the named-skill memory (client-supplied registered mapping citing operations manuals)?
3. Do accepted runbooks feed the model's prompt (as notes) or only a deterministic selector, which would be the stricter reading of REQ-012? The first is easier to test; the second keeps inference out of the selection path.

## Build order if approved

(a) gold-values script and lint wiring, (b) runbook schema plus validator, (c) `--replay-runbook`, (d) catalog and admission command, (e) D2 draft generator. Each is a small PR with tests run by pi.
