# PRDs

Forward-looking capability specs — what to build next, broken into composable units with
acceptance criteria — as distinct from `docs/backlog/`, which records gaps found by auditing
already-committed code. A PRD here may cite a backlog item as motivation, but it is a build plan,
not a gap report.

Numbered in authoring order, never renumbered once merged. Each file states its own dependencies
on other PRDs explicitly (don't assume reading order from the number alone).

| # | Title | Depends on | Status |
|---|-------|------------|--------|
| [0001](0001-composable-generator-detector-platform.md) | Composable generator/detector simulation platform | — (foundation) | implemented |
| [0002](0002-sa-2016-black-system-cascade-scenario.md) | SA 2016 Black System — physically-grounded cascade reproduction | 0001 | implemented |
| [0003](0003-iberian-2025-blackout-scenario.md) | Iberian Peninsula 2025 blackout — new scenario | 0001 | implemented (fast-collapse + precursor oscillation phases + precursor→collapse state handoff, `--phase combined`) |
| [0004](0004-plexos-direct-mcp-and-strangler-fig.md) | PLEXOS direct-access MCP + open-source strangler fig (plexosdb, R2X, Sienna) | — (sibling to PowerMCP) | Phase 0 complete (env verification — see `0004-phase0-findings.md`); Phases 1-7 not started |
| [0005](0005-grid-forming-stabilizer-and-renewable-models.md) | Grid-forming transient stabilizer + open renewable-generation models (Lab 5, SPARTAN's corrective-action testbed) | — (builds on Lab 5 + 0006) | implemented (Phase 4 conditional, not attempted) |
| [0006](0006-sysml-digital-thread-mvp.md) | SysML v2 digital-thread MVP: MBSE tooling evaluation (Lab 6) | — (reuses Labs 1/3/4/5 as seed data) | implemented |
| [0007](0007-lab6-cim-class-uri-traceability.md) | Lab 6 Phase 4a: CIM class-URI traceability annotations for Track B | 0006 | implemented |
| [0008](0008-cim-gridy-mission-engine-prerequisites.md) | cim-gridy Phase 0: mission/calendar-step engine prerequisites (Grid2Op, OperatorFabric, ufo-types) | 0006, 0007 | superseded in practice by 0009 (prerequisites answered via Lab 8) |
| [0009](0009-cim-gridy-incose-v-plan.md) | cim-gridy: INCOSE-V prioritized plan, Rust/Bevy-first | 0006, 0007, 0008 | Phases 0-3 complete, real evidence (Lab 8 spikes, Lab 9 vertical slice — `just check-lab9`, 17 tests); Phases 4-6 not started |
| [0010](0010-powercoder-physics-grounded-engineering-agent.md) | PowerCoder: physics-grounded autonomous power-system engineering (gates G0–G8) | r6 study, backlog 0008–0014, Labs 2/5/6/8/9/10 | proposed; G0 = backlog 0008, G1–G8 = backlog 0015–0022 |

## Why this shape

`docs/PSCADOSSE.md`'s "one waveform state machine generates every view" principle already gives
Lab 5 a single source of truth for a fault's *physics*. What Lab 5 does not yet have is a way to
compose more than one physical event into a causally-linked sequence (a network fault triggering a
protection response triggering a plant disconnection triggering a further protection response —
the actual shape of every real blackout this repo's docs already reference), or anything that
*consumes* the resulting stream to detect the precursor pattern before the collapse. 0001 names
that gap as two small, composable interfaces (`Generator`, `Detector`) and a scoring harness; 0002
and 0003 are the first two scenarios built on top of it, chosen because both are already named
in this repo (SA 2016 is Lab 4's optional Part C, explicitly caveated as *not* a root-cause
reproduction; Iberian 2025 is cited in `docs/VISION.md`/GPSRR-review discussion as the reference
international incident for voltage-control and oscillation risk) and because between them they
cover the two structurally different blackout mechanisms worth having reference implementations
of: a **weather/network-fault-initiated** cascade (SA) and a **control/oscillation-initiated**
cascade with no initiating network fault at all (Iberian).
