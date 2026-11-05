# 0012 — Idea: answer by reference (made-up numbers impossible by construction)

- **Status:** idea, not started. Review idea 2, merit 4 ([review](reviews/2026-10-23-r6-merit-review.md)).

## Idea
The final answer cites a tool-call id and a field, for example `{"report_from_call": "call_0"}`. A grammar enforces that shape, and the harness renders the numbers from that call's real output. No number in an answer could then come from the model.

## Risks
- **Evasive citing.** The model could cite the wrong field. `base_case_voltage_violations` and `voltage_violations` are both 39 on snem-sa-2000.
- **Retraining.** It needs retraining on the new answer format. Otherwise grammar and training conflict.

## Cheapest decisive experiment
Offline, with no GPU: over all r6 journals, count answer numbers that match more than one tool-response field. That measures how ambiguous citations would be before anything is built.
