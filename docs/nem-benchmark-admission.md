# Admitting a benchmark question (NEM multi-table set, #120)

Adversarial probes of the first multi-table set (`configs/nem-multitable-questions.proposed.json`) found six defects. Each is an anti-pattern with a mechanical defence, run by `just nem-question-lint` (chart: `docs/diagrams/nem-question-lint.d2`).

| Probe finding | Anti-pattern | Why it happened | Defence in the lint |
|---|---|---|---|
| `dudetailsummary` has one row per DUID, yet schema, M10 and every ground-truth query handled "several dated rows" | Folklore in the schema: a trap described, never observed | The rule came from the bid-table work (versioned rows) and was carried over without checking this table | Every schema sentence about the data has an executable claim that must return true; the false sentence is removed |
| M4: all 151 forward intervals already had NSW1 dearer, so the price condition filtered nothing | Vacuous condition: the question cannot tell right from wrong | The truth was computed and accepted without asking what a wrong query would return | Mutation check: each plausible wrong reading is run and must give a different answer, else the question fails (declared exceptions carry a reason and a claim) |
| M1/M3/M8: 34 units have negative TOTALCLEARED; M8 is 0.437 net and 0.434 positive-only | Ambiguous wording: two defensible readings, one is marked right | "Total cleared energy" was assumed to be obvious | Schema states the net convention; a mutant for the other reading must differ; the lint checks the schema text |
| Scorer passed if any expected value appeared in any returned cell | Lenient oracle: a table dump can pass by coincidence | Scoring was written for single-value questions and reused | `scripts/nem_scoring.py`: at most 3 rows, one row must hold every expected value; lint proves a 200-row dump containing the truth fails |
| Scorer tolerance 0.005 failed an unrounded correct answer (50501.556 vs 50501.6) | Tolerance unrelated to the question's declared rounding | A fixed constant, not derived from "(1 decimal)" | Tolerance is half the declared rounding unit; lint proves an unrounded truth passes and a wrong value fails |
| Truth, schema and questions had one author and one SQL dialect; margins unchecked (M3 leads by 0.6%) | Self-grading: no independent check on the ground truth | One pass, one person | Each question carries an independently written `sql_alt` that must agree; the winner must beat the runner-up by more than 10 rounding units so rounding cannot flip it |

Declared limits (the lint reports them rather than hiding them):
- M1 cannot detect the sign convention (the leading station has no negative rows). M3 and M8 do.
- M9 cannot detect a missing absolute value (the five-region sum is never below -50 on this day).
- A one-day window cannot test dated-row resolution. Testing that needs a window where DUIDs have several `dudetailsummary` rows.
- The questions are single-day; results are one run per arm.
