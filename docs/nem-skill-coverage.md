# NEM market analysis: question-family coverage (#157 slice B)

Which question families the `nem-market-analysis` skill and the runbook catalog cover, and which have no gold SQL or runbook yet. Surveyed 2026-11-16 on main `ad79b96` by reading files; nothing was run, so the last column reports results already recorded in the repo, not new runs.

## How each column was produced

| Column | Command or file |
|---|---|
| Recipe | `grep -n -E "^(nem-[a-z-]+\|nemosis-[a-z-]+)" justfile` (lines 402 to 444) |
| Gold SQL | questions with an `sql` field in `configs/nem-hard-questions.proposed.json` (H1 to H10), `configs/nem-multitable-questions.v2.proposed.json` (M1 to M10) and `benchmarks/runbooks/region-window-average/{questions,heldout-questions}.json` (3 and 8 questions); tables read from each `sql` |
| Runbook | `benchmarks/runbooks/catalog.json`: one accepted (`region-window-average`), none rejected |
| Replay result | `catalog.json` evidence: `replay_passed 3/3`, `siblings_passed 2/2` |

## Table

| Family | Tables behind it | Recipe | Gold SQL questions | Runbook | Replay result |
|---|---|---|---|---|---|
| Price and demand, windows and averages | DISPATCHPRICE, DISPATCHREGIONSUM | `nemosis-fetch`, `nemosis-reconcile` | H1 to H10 (price_demand); 3 + 8 in the runbook question files | `region-window-average` (accepted) | 3/3 cases, siblings 2/2 |
| Station and unit lookup, net cleared energy | DISPATCHLOAD, DUDETAILSUMMARY | `nem-unit-lookup` | M1, M2, M3, M7, M8, M10 (M1 and M3 share a shape; M11 added in `benchmarks/runbooks/net-cleared-energy/`) | `net-cleared-energy-extreme` drafted and REJECTED (see Correction) | 0 cases run: leakage failed first |
| Interconnector flow and losses | DISPATCHINTERCONNECTORRES (+ DISPATCHPRICE) | `nem-interconnector` | M4, M6 | none | none |
| Regional net interchange | DISPATCHREGIONSUM | none | M5, M9 | none | none |
| FCAS price | DISPATCHPRICE (FCAS columns) | `nem-fcas` | F1 to F5 (`configs/nem-fcas-questions.proposed.json`, lint PASS, 0 failures) | none | none |
| Bid stack and supply curve | BIDPEROFFER_D, BIDDAYOFFER_D (+ DISPATCHPRICE, DUDETAILSUMMARY, DISPATCHREGIONSUM) | `nem-bidstack` | none | none | none |
| Binding constraints | DISPATCHCONSTRAINT, GENCONDATA, SPD* | none | none | none | none |
| Fuel mix, capacity factor | DISPATCHLOAD, DISPATCH_UNIT_SCADA, Generators and Scheduled Loads | none | none | none | none |

## Reading the table

- **One family has a runbook.** Price and demand is the only family with an accepted runbook; every other row has none.
- **Admission needs three questions in a family** (the source and two siblings, `docs/runbook-replay-design.md`). Unit lookup has six gold questions, but they are not one family (see Correction); only M1 and M3 share a query shape. Interconnector has two (M4, M6) and needs at least one more question before a runbook can be admitted. Regional net interchange has two (M5, M9) and the same gap.
- **FCAS and bid stack have a recipe but no gold question.** Their scripts run one deterministic statement each and print PASS or FAIL against checks (`scripts/nem_fcas.py`: eight services present, rows for every region, no negative price), which is a data check, not a scored question. Writing gold questions for them is the step that would make them runbook-eligible.
- **Blocked, not missing by choice:** fuel mix and capacity factor need AEMO's registration workbook, which a person must save (the skill's "Not done" section). FCAS causer-pays (FCAS_4_SECOND) returned no data from NEMOSIS for four windows. Binding constraints are planned with no recipe.

## Proposed next steps, smallest first

1. Draft a unit-lookup runbook from M1 with M2 and M3 as siblings, and replay it through `scripts/runbook_replay.py` (pi runs it; needs the NEM container and data).
2. Write one more interconnector question and one more net-interchange question with gold SQL, checked by `scripts/nem_question_lint.py`, so each family reaches three.
3. Write gold questions for FCAS and bid stack from their recipes' tables.

None of these is started.

## Correction (2026-11-16, PR for #157 slice B step 1)

- **The six unit-lookup questions are not one family.** Reading their SQL: M1 and M3 are the same query ranked by station or by region; M2 is a distinct count, M7 is multi-step, M8 is a share, M10 is a station count. The first version of this document called unit lookup "the best next candidate" on the strength of the count; that overstated it.
- **A runbook was drafted and the admission check rejected it.** `net-cleared-energy-extreme` has one SQL template with two slots (`group_col`, `direction`) covering M1, M3 and a new M11 (lowest station). `scripts/runbook_admit.py`, run by pi, returned `REJECTED ... leakage: FAIL sql: M1 quotes 165 chars of the gold sql`. No replay ran. The decision is recorded under `rejected` in `benchmarks/runbooks/catalog.json`.
- **Why that is the right result, not a lint to get around.** The family's questions are the same query with a different grouping, so the template is the gold SQL with slots. The lint exists to stop a runbook that merely carries the answer's query. Reworking whitespace or aliases to pass would defeat its purpose and was not done.
- **What this says for #130.** A family whose whole content is one parameterised SQL statement cannot be admitted by a template runbook under the current leakage rule; `region-window-average` passed because its template adds a window the source question did not have. Whether such families need a different runbook shape (procedure notes with no full statement) is an open design question for the owner, not decided here.
- **Other limits.** M11's winner margin and its independent `sql_alt` were not checked with `scripts/nem_question_lint.py`. The runbook was drafted knowing all three questions, so M3 and M11 are not independent siblings in the way the admission design intends. Gold values were computed in the container from one day of data (2025-09-01).

## Update: FCAS gold questions (2026-11-16)

`configs/nem-fcas-questions.proposed.json` holds F1 to F5 over `dispatchprice` (2025-09-01, one day): highest average raise 6-second price by region, count above a threshold, highest of the eight services, a strict-greater count, and a cross-region equality count. Each has an independently written `sql_alt`, two wrong-reading variants that must give a different answer, and (F1, F3) a winner margin. `scripts/nem_question_lint.py`, run by pi against `configs/nem-fcas-claims.json`, ended `PASS nem-question-lint: 0 failure(s)`. The first run failed F4: the `>` versus `>=` mutant gave the same count (275) for TAS1, so the question did not test the tie it claimed to; it was moved to NSW1 (263 against 264) and passed on the rerun. No schema file was passed to the lint, because its schema check is hard-coded to the multi-table keywords.

Limits: one day of data; the five questions are one table and mostly one family shape, so they are not yet enough to admit a runbook (that needs the template-versus-leakage question above settled). Bid stack still has no gold question: it needs BIDPEROFFER_D and BIDDAYOFFER_D, which are not fetched, and a fetch of their size has not been agreed.
