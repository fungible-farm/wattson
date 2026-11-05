# 0034 — Make the G4 export importable as CGMES (profiles and headers)

- **Status:** **done** 2026-10-23 on `chore/bus-discovery` (below). Found by [0029](0029-cgmes-round-trip-with-pypowsybl.md) on 2026-10-23.
- **Affects:** `powercoder/semantic/export.py`, G4 ([0018](0018-g4-cim-semantic-network-intelligence.md)).

## Gap (measured)
pypowsybl 1.16.1 rejects our export: `Data source does not contain EquipmentCore data`. A CGMES
2.4.15 model is split into profile instances (EQ, TP, SSH, SV), each with a `md:FullModel`
header declaring:
- `Model.profile`, `modelingAuthoritySet`, `scenarioTime` and `created`;
- `DependentOn`, linking each profile to the one it builds on.

Ours writes one merged graph with one header.

## Scope
1. **Split the export into profiles:**
   - **EQ:** equipment, terminals, connectivity nodes, base voltages, limits;
   - **TP:** topological nodes and terminal-to-node links;
   - **SSH:** operating point (P/Q setpoints, regulating targets, tap steps, in-service);
   - **SV:** the solved state (`SvVoltage`, `SvPowerFlow`, …).

   Each profile gets a header and `DependentOn` links. Extension-namespace fields stay out of the
   standard profiles, or are declared as an extension.
2. **Keep the existing tests passing:** our own round trip to within 1.05e-12 pu and 0 SHACL
   violations.
3. **Acceptance:** pypowsybl imports the four files (on an x86-64 peer; no ARM64 wheel exists),
   solves AC, and agrees with pandapower within a stated tolerance.

## Done (2026-10-23)
`powercoder/semantic/cgmes.py` splits the export into EQ/TP/SSH, each with an `md:FullModel`
header (profile, modelling authority, times, `DependentOn`), and puts the `pcx:` extension fields
in a separate EXT file. EQ + TP + SSH + EXT round-trips to the same power flow. pypowsybl 1.16.1
(on sm3lly) **imports every element and solves AC: max |dV| 1.38e-5 pu over all 503 buses, mean
5.1e-7**, against pandapower. Run it with `just powercoder::cgmes-peer-check`
(`scripts/cgmes_peer_check.sh`; 12.6 s; exit 1 above 1e-4 pu).

Getting there took seven fixes, each found from powsybl-core's own queries
(`CIM16.sparql`, `CIM16-update.sparql`) and its import log:
1. **Profiles and headers** (`Data source does not contain EquipmentCore data`).
2. **Containment:** an uncontained node is read as a boundary node. snemSA has none, so
   substations (buses joined by transformers), voltage levels (one per bus) and a region are
   **inferred and labelled as such**, as pypowsybl's MATPOWER importer infers them.
3. **Bus-branch topology:** terminals point at topological nodes, with connectivity nodes in EXT.
   The case has no switches.
4. **Plain literals.** CGMES writes no datatypes. A typed `sequenceNumber` failed the line query's
   `= "1"` (0 of 295 lines imported), and Integer properties must be whole.
5. **Fields derived from the case:**
   - `SynchronousMachine.type`/`operatingMode` (generator);
   - `ShuntCompensator.normalSections`;
   - `GeneratingUnit.initialP` (the dispatch).
6. **Regulation:** `RegulatingControl.targetValueUnitMultiplier` (k) and `discrete` (false), plus
   `RotatingMachine.q` taken from pandapower's solved AC (`solved_reactive_power`). The case holds
   no Q setpoint for a voltage-controlled machine. Without these, no generator regulated and the
   load flow returned `NO_CALCULATION`.
7. **Tap changers:** `lowStep`/`highStep` (the narrowest range holding the recorded and neutral
   steps; snemSA has no `tap_min`/`tap_max`), `normalStep` and `neutralU`. Without them all 403
   were dropped, giving |dV| up to 0.275 pu.

**A latent importer bug, fixed:** `import_._b` read booleans with Python truthiness, so a plain
`"false"` meant connected. `test_cgmes.py` puts an out-of-service line through the split, and it
fails without the fix.

pypowsybl's own CGMES export has two `rdf:ID`s that are not valid NCNames (`+`; 0029). Not
reported upstream yet.
