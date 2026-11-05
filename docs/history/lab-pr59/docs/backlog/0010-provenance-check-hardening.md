# 0010 — Provenance check (PC-REQ-1) is weaker than its requirement

- **Status:** **done** 2026-10-23 on `chore/bus-discovery` (v3 below; v2 unchanged). Review findings B4–B6 ([review](reviews/2026-10-23-r6-merit-review.md)), all reproduced with the reviewer's probe.
- **Context:** `grade()` also requires numerical correctness and matching tool evidence, so most of the cases below still fail the strict score. The weakness is in provenance as a stand-alone guarantee, and in the solver-verified v2 score, which does not require tool evidence.

## Reproduced cases
- **Accepted:**
  - "converged in 100 iterations, voltage violations = 2000" traces to a schema limit and to the case name.
  - A no-tool report with coarse values (1, 1, 1, 2000, 135).
  - "1,039" is read as 1 and 039.
  - A negative invented value traces because the sign is ignored.
  - A fit answer equal to the prompt's interval end (1.1) or to its target.
  - An empty answer (vacuously traced).
- **Rejected:** a truncated value, 0.9069 for 0.90697.
- **Perverse incentive:** printing fewer digits makes an answer easier to trace.

## Proposal
1. **Trace each number to a field.** A report value must match the field it claims to be: min voltage to `min_bus_voltage_pu`, not any number.
2. **Narrow the sources.** Exclude schema limits, ids and the case name, and use the prompt only for ids. A fit scale must equal a tried scale, as `relevant_evidence` already requires.
3. **Keep signs. Parse thousands separators. Accept truncation as well as rounding.**
4. **Require tool evidence** in the solver-verified score.
5. **Record serving flags** (for example `--tool-strict-level`) in each run manifest, and have `compare_agents` refuse runs that differ in them. Today they sit only in the serving JSON.
6. **Done in this pass:** `latency_bench` measures decode to the end of the stream, not to the last streamed delta (B7, speculative).

## Done (2026-10-23): field-level provenance v3, alongside v2
`provenance.check_fields` and `domain_scorecard`'s `solver-verified-field-traced-v3` cover
items 1–4 of the proposal:
- **Per-field matching:** each labelled report value matches its own output field, at no fewer
  decimals than the reference report prints (voltages 4, loading 2).
- **Narrow sources:** context numbers trace to solver outputs, the tool's declared limits or
  prompt ids. The echoed request is excluded.
- **Fits:** an answer must include a scale that was actually tried.
- **Parsing:** signs kept, thousands separators parsed, truncation accepted as well as rounding.
- **No numbers fails;** v3 also requires the original tool evidence.

Item 5 was already met: `server_tool_strict_level` and the other conditions are recorded and
checked by `compare_agents.CONDITIONS`. `check` (v2) is unchanged and remains the headline score,
so r6 comparisons are not affected.

Each reproduced case has a test (`test_provenance.py`, 14 tests). The first measurement found two
errors in my v3, both fixed:
- **Quoting the tool's declared voltage band as context is legitimate.** What the review objected
  to was a limit *standing in for* a field.
- **Markdown emphasis** (`**5**`) broke field parsing.

**Measured on r6b (v2 → v3 pass counts):**

| Run | v2 | v3 |
|---|---|---|
| Qwen base | 12 | 12 |
| Qwen student | 76 | 76 |
| Granite base | 30 | 23 |
| Granite student | 59 | 59 |

All 7 Granite-base losses are report tasks whose numbers were right **without matching tool
evidence** (item 4). v3 deltas: Qwen +64 pp [53, 75], Granite +36 pp [23, 49].
