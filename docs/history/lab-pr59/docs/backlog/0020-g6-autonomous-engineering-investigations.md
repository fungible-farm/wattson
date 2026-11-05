# 0020 — G6: autonomous engineering investigations

- **Status:** open. [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md) G6, priority B3. Depends on G2, G4 and G5. Its synthetic variant
  needs only G2 and G4.
- **Requirements:** PC-REQ-004, 050–051.

## Reference investigation
A historical voltage disturbance:
1. identify the affected assets;
2. examine the SCADA and PMU evidence;
3. retrieve the weather;
4. evaluate the sensitivities;
5. write a reproducible explanation.

## Deliverables
- **Planner:** a multi-tool investigation planner, with hypothesis generation and testing.
- **Report:** evidence-linked, with uncertainty and data quality stated.
- **Replay** of the whole investigation.

## Exit criteria
- Every quantity traces to data or a tool run.
- Unsupported causal claims are rejected.
- Observation, calculation and hypothesis are kept distinct.
- The investigation reproduces.
- Blind expert review against a predefined rubric finds it defensible.

## Cheapest first step
Synthetic disturbances injected into G5's synthetic SCADA, with a known cause, so explanations can
be scored automatically before any expert review.
