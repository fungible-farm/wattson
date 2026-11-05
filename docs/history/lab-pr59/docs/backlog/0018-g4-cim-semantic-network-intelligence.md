# 0018 — G4: CIM semantic network intelligence

- **Status:** open. [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md) G4, priority B1. Depends on G1 for typed graph queries. It can
  start in parallel on synthetic models.
- **Requirements:** PC-REQ-020–023.

## Starting point in this repo
- **Lab 6:** a LinkML → SysML pipeline, with CIM class-URI traceability for Track B's real
  `snemSA.m` cluster (PRD-0007).
- **Labs 8 and 9:** the cim-gridy spikes, and the Grid2Op dataset built from `data/snemSA.m`.
- **r00t register:** the RDF/SHACL direction is DEC-020/028 (`~/promptexecution/AGENTS.md`). No
  RDF store exists yet.

## Deliverables
- **Ingestion and validation:** CIM/RDF ingestion of CGMES profiles (ENTSO-E), with OWL/RDFS where
  it applies and SHACL validation.
- **Bounded graph queries:** typed graph-query tools. Normal operation needs no unrestricted SPARQL.
- **Topology snapshots:** versioned, keeping physical and electrical connectivity under a switching
  state distinct.
- **Model mapping:** CIM ↔ pandapower, so G2 tools run on graph-derived models.

## Exit criteria
- Every reference identifier resolves.
- SHACL constraints are checked, and known-invalid graphs are rejected or quarantined.
- Graph-derived connectivity matches the reference model, and historical snapshots reproduce.
- Query budgets are enforced.

## Cheapest first step
Export `snemSA.m` to a CGMES-shaped RDF graph. Validate it with SHACL, and round-trip it to
pandapower with identical power-flow results.

## Versioned topology snapshots done (2026-10-23): `powercoder/semantic/snapshots.py`
A snapshot records the SSH switching state (`ACDCTerminal.connected`) and the tap steps, plus the
model version (sha256 of the non-state triples), its validity interval, source and quality. The
store is append-only, refuses overlapping intervals and foreign model versions, and answers
"the state at time t". **Historical snapshots reproduce:** re-applying a stored snapshot gives
exactly the islands it was captured with (`test_snapshots.py`, on snemSA with an isolating line
opened).

**Also done for G4:**
- the CGMES export, checked against pypowsybl to 1.38e-5 pu (0029, 0034);
- the walkable `ls`/`get` namespace, the typed query surface (0026).

**Query budgets done the same day** (`powercoder/semantic/queries.py`). There are named SPARQL
templates only, with typed parameters bound through `initBindings` (never pasted into the text), a
row budget (default 50, maximum 500), and the true total with `truncated` on every answer.
`buses_at_voltage(275)` matches pandapower's 52 buses exactly. An injection-shaped string parameter
is refused by type.

**Left:** `cim:Switch` objects and switch-derived TopologicalNodes; OWL/RDFS schema ingestion.
