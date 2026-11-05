# 0019 — G5: observations (SCADA, sparse PMU, data lake) and weather

- **Status:** **blocked** on the access decision for real data. The synthetic path is open.
  [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md) G5, priority B2. Depends on G4.
- **Requirements:** PC-REQ-030–034, PC-CON-001/002.

## Blockers, measured
- **AEMO NEMWeb:** returns 403 by org policy in this sandbox (AGENTS.md capability matrix).
- **No local sources:** this repo has no SCADA, PMU or enterprise data-lake connection.
- **Weather:** fung1's `windyux` agent is a contacts/sessions service, not a forecast API
  (r00t DEC-035). The weather source is still to be chosen.

## Deliverables
- **Interfaces:** read-only SCADA, optional sparse PMU, a data-lake connector, and weather
  observations and forecasts, all mapped to CIM measurements and locations.
- **Snapshot assembler:** time-consistent, keeping valid, availability and issue times.

## Exit criteria
- Read-only access is independently verified.
- Timestamps and availability are preserved.
- Missing PMUs don't block SCADA-based work.
- Forecasts are distinguished from observations, and observability failures are flagged.
- Replay can't see data that wasn't yet available.

## Cheapest first step
A synthetic SCADA stream generated from G2 power flows, with injected stale, missing and mistimed
values, feeding the state estimator.
