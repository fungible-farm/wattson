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
| Station and unit lookup, net cleared energy | DISPATCHLOAD, DUDETAILSUMMARY | `nem-unit-lookup` | M1, M2, M3, M7, M8, M10 | none | none |
| Interconnector flow and losses | DISPATCHINTERCONNECTORRES (+ DISPATCHPRICE) | `nem-interconnector` | M4, M6 | none | none |
| Regional net interchange | DISPATCHREGIONSUM | none | M5, M9 | none | none |
| FCAS price | DISPATCHPRICE (FCAS columns) | `nem-fcas` | none | none | none |
| Bid stack and supply curve | BIDPEROFFER_D, BIDDAYOFFER_D (+ DISPATCHPRICE, DUDETAILSUMMARY, DISPATCHREGIONSUM) | `nem-bidstack` | none | none | none |
| Binding constraints | DISPATCHCONSTRAINT, GENCONDATA, SPD* | none | none | none | none |
| Fuel mix, capacity factor | DISPATCHLOAD, DISPATCH_UNIT_SCADA, Generators and Scheduled Loads | none | none | none | none |

## Reading the table

- **One family has a runbook.** Price and demand is the only family with an accepted runbook; every other row has none.
- **Admission needs three questions in a family** (the source and two siblings, `docs/runbook-replay-design.md`). Unit lookup has six gold questions and is the best next candidate. Interconnector has two (M4, M6) and needs at least one more question before a runbook can be admitted. Regional net interchange has two (M5, M9) and the same gap.
- **FCAS and bid stack have a recipe but no gold question.** Their scripts run one deterministic statement each and print PASS or FAIL against checks (`scripts/nem_fcas.py`: eight services present, rows for every region, no negative price), which is a data check, not a scored question. Writing gold questions for them is the step that would make them runbook-eligible.
- **Blocked, not missing by choice:** fuel mix and capacity factor need AEMO's registration workbook, which a person must save (the skill's "Not done" section). FCAS causer-pays (FCAS_4_SECOND) returned no data from NEMOSIS for four windows. Binding constraints are planned with no recipe.

## Proposed next steps, smallest first

1. Draft a unit-lookup runbook from M1 with M2 and M3 as siblings, and replay it through `scripts/runbook_replay.py` (pi runs it; needs the NEM container and data).
2. Write one more interconnector question and one more net-interchange question with gold SQL, checked by `scripts/nem_question_lint.py`, so each family reaches three.
3. Write gold questions for FCAS and bid stack from their recipes' tables.

None of these is started.
