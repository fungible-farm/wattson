# 0029 — Check the G4 CIM export against a real CGMES implementation (pypowsybl)

- **Status:** **done** 2026-10-23 (results below); the conformance fixes it found are [0034](0034-cgmes-conformant-g4-export.md). **Merit: second** of the 2026-10-23 tool review.
- **Affects:** G4 ([0018](0018-g4-cim-semantic-network-intelligence.md)), `powercoder/semantic/`, and
  [0026](0026-walkable-path-namespace-ls-get.md)/[0027](0027-functional-data-virtualization-layer.md)'s
  naming.

## Why
G4's export is a *CGMES-shaped subset*: one merged graph, no EQ/TP/SSH/SV profile split, with
extension-namespace fields. It round-trips through our own importer, so nothing has yet checked it
against an independent implementation. pypowsybl (MPL-2.0, already used by Labs 2–3) reads and
writes CGMES.

## Scope
1. **Export with pypowsybl.** Export snemSA as CGMES with pypowsybl, from the pandapower or MATPOWER
   case, and compare its class and property usage with ours. Which classes are missing (e.g.
   Substation and VoltageLevel containment), and which properties differ?
2. **Import ours into pypowsybl,** splitting it into profiles if needed. Run its load flow and compare
   bus voltages with pandapower's.
3. **Record every difference** as either a conformance gap in our export or a modelling difference
   between the solvers.

## Acceptance
- **Gap list:** every gap is listed with the class or property and its cause.
- **Voltage agreement:** voltages agree within a stated tolerance, or the cause is named.
- **Containment:** if pypowsybl's export carries containment that ours lacks, 0027's naming plan uses
  that containment.

## Result (2026-10-23)
**Where it ran.** pypowsybl 1.16.1 publishes no Linux ARM64 wheel and no sdist (PyPI, checked).
This host has no x86-64 emulation (`podman --platform linux/amd64` gives `Exec format error`).
So pypowsybl ran on **sm3lly** (x86-64, CPU only) in an ephemeral `uv` environment, per the
peer-placement rule. Inputs came from here: snemSA as MATPOWER `.mat` (Lab 2's `to_mpc` path, base
voltages kept) and our export as RDF/XML.

**Our export does not import into pypowsybl:** `PyPowsyblError: Data source does not contain
EquipmentCore data`. pypowsybl's own export is four profile files (EQ, TP, SSH, SV), each with a
`FullModel` header carrying:
- `Model.profile` (`http://entsoe.eu/CIM/EquipmentCore/3/1`, `…/Topology/4/1`,
  `…/SteadyStateHypothesis/1/1`, `…/StateVariables/4/1`);
- `modelingAuthoritySet`, `scenarioTime`, `created`, `version` and `DependentOn`.

Ours is one merged graph with one header and no profile.

**AC voltages agree.** On the same `.mat`, pypowsybl's OpenLoadFlow converged and agrees with
pandapower to **at most 8.6e-4 pu (mean 3.0e-5)** over the 452 buses whose ids map directly. The
other 51 are grouped by pypowsybl into shared voltage levels (`VL-385_2` …), so they have no
one-to-one id. The residual was not investigated further.

**Class counts (ours / pypowsybl):**
- **Containment.** `Substation` 0 / 327, `VoltageLevel` 0 / 452,
  `GeographicalRegion`/`SubGeographicalRegion` 0 / 1. pypowsybl *infers* this containment in its
  MATPOWER importer, from buses joined by transformers. That containment is **derived, not data**.
  0027 may use it for naming only if it is labelled as inferred.
- **Element classification differs** on the same branches and buses:
  - `ACLineSegment` 295 / 445;
  - `PowerTransformer` 403 / 253;
  - `LinearShuntCompensator` 782 / 410;
  - `EnergyConsumer` 186 / 179, plus 7 `EnergySource` in theirs.

  This is the known line/transformer split difference (Lab 2's gridfit notes), plus per-bus
  aggregation through MATPOWER's bus columns.
- **Only in ours:**
  - `ConnectivityNode` 503;
  - `OperationalLimitSet`/`CurrentLimit` 295 (the 10000 MVA "no limit" ratings, 0023);
  - `RatioTapChanger` 403;
  - `Conductor.length`;
  - 15 extension-namespace fields.
- **Only in theirs:** state as standard classes (`SvVoltage` 503, `SvPowerFlow` 2049,
  `SvShuntCompensatorSections` 410) and the SSH operating point (`EnergySource.activePower`,
  `RotatingMachine.q`, `RegulatingControl.discrete`/`targetDeadband`, …).

**pypowsybl's export is not strictly valid RDF/XML.** Two `rdf:ID` values in its EQ file contain
`+` (`_default+region_GR`), which is not valid in an XML NCName, so rdflib's strict parser rejects
the file. The comparison replaced `+` in ids for this check only.
