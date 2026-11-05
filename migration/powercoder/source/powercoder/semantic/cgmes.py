"""Split the G4 graph into CGMES 2.4.15 profile documents (backlog 0034).

``export.export_network`` writes one merged graph with one ``md:FullModel``. pypowsybl
1.16.1 refuses that ("Data source does not contain EquipmentCore data", 0029): a CGMES
model is a set of profile instances, each with its own header declaring the profile, and
later profiles declaring which ones they depend on. This splits the same triples into:

- **EQ** (Equipment): equipment, terminals, connectivity nodes, base voltages, limits;
- **TP** (Topology): topological nodes and the connectivity-node links into them;
- **SSH** (Steady State Hypothesis): the operating point (connected, P/Q setpoints,
  regulation targets, shunt sections, tap steps);
- containers (substations, voltage levels, a region) *inferred* by ``infer_containment``,
  because CGMES importers read uncontained nodes as boundary nodes and the case has none;
- **EXT**: this repo's ``pcx:`` pandapower-only fields, kept out of the CGMES documents
  (they are not CIM) but written beside them, so EQ + TP + SSH + EXT is the whole graph.

Property-to-profile assignment follows the CGMES 2.4.15 profile definitions. For every
property that pypowsybl's own CGMES export of snemSA also uses, it matches the profile
pypowsybl put it in (checked 2026-10-23, 0029). No SV (state variables) profile is written:
the graph holds no solved state (``export.py``'s module docstring), and an importer solves.

Named stand-in: the headers' ``modelingAuthoritySet`` is this repo's URN, not a registered
modelling authority, and ``scenarioTime``/``created`` are parameters with a fixed default so
the same network always writes the same bytes.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from typing import Final

from rdflib import RDF, XSD, Graph, Literal, URIRef

from powercoder.semantic.namespaces import CIM, MD, PCX

PROFILES: Final[dict[str, str]] = {
    "EQ": "http://entsoe.eu/CIM/EquipmentCore/3/1",
    "TP": "http://entsoe.eu/CIM/Topology/4/1",
    "SSH": "http://entsoe.eu/CIM/SteadyStateHypothesis/1/1",
}
# Which profile each later one builds on (CGMES 2.4.15 dependency chain, without SV).
DEPENDS_ON: Final[dict[str, tuple[str, ...]]] = {"EQ": (), "TP": ("EQ",), "SSH": ("EQ",)}
# CGMES 2.4.15: these properties live in SSH or TP; every other cim: property is EQ.
SSH_PROPERTIES: Final[frozenset[str]] = frozenset({
    "ACDCTerminal.connected", "EnergyConsumer.p", "EnergyConsumer.q", "EnergySource.activePower",
    "EnergySource.reactivePower", "RegulatingCondEq.controlEnabled", "RegulatingControl.enabled",
    "RegulatingControl.targetValue", "RegulatingControl.discrete", "RegulatingControl.targetDeadband",
    "RegulatingControl.targetValueUnitMultiplier", "RotatingMachine.p", "RotatingMachine.q",
    "ShuntCompensator.sections", "SynchronousMachine.referencePriority", "SynchronousMachine.operatingMode",
    "TapChanger.step", "TapChanger.controlEnabled", "ExternalNetworkInjection.p", "ExternalNetworkInjection.q",
    "ExternalNetworkInjection.referencePriority",
})
TP_PROPERTIES: Final[frozenset[str]] = frozenset({"ConnectivityNode.TopologicalNode", "Terminal.TopologicalNode"})
TP_CLASSES: Final[frozenset[URIRef]] = frozenset({CIM.TopologicalNode})
# Header defaults: fixed, so an export is byte-reproducible; pass real times for a real exchange.
DEFAULT_TIME: Final[str] = "2026-01-01T00:00:00Z"
MODELING_AUTHORITY: Final[str] = "urn:powercoder:semantic"
HEADER_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6f1d2a3e-0b4c-4e5f-9a8b-7c6d5e4f3a2b")


def property_profile(predicate: URIRef, subject_classes: set[URIRef]) -> str | None:
    """The profile a non-type triple belongs to; None for header triples."""
    text = str(predicate)
    if text.startswith(str(PCX)):
        return "EXT"
    if text.startswith(str(MD)):
        return None
    name = text.split("#")[-1]
    if name.startswith("TopologicalNode.") or name in TP_PROPERTIES:
        return "TP"
    if name.startswith("IdentifiedObject."):
        return "TP" if subject_classes & TP_CLASSES else "EQ"
    return "SSH" if name in SSH_PROPERTIES else "EQ"


def header(model_id: str, profile: str, scenario_time: str, created: str) -> tuple[URIRef, list[tuple]]:
    """The md:FullModel triples for one profile document."""
    about = lambda name: URIRef(f"urn:uuid:{uuid.uuid5(HEADER_NAMESPACE, f'{model_id}/{name}')}")
    model = about(profile)
    triples = [(model, RDF.type, MD.FullModel), (model, MD["Model.profile"], Literal(PROFILES[profile])),
               (model, MD["Model.modelingAuthoritySet"], Literal(MODELING_AUTHORITY)),
               (model, MD["Model.scenarioTime"], Literal(scenario_time)), (model, MD["Model.created"], Literal(created)),
               (model, MD["Model.version"], Literal("1")),
               (model, MD["Model.description"], Literal(f"{model_id} {profile}, powercoder.semantic"))]
    triples += [(model, MD["Model.DependentOn"], about(dep)) for dep in DEPENDS_ON[profile]]
    return model, triples


# One-terminal equipment that sits in its node's voltage level, as pypowsybl's own export does.
VOLTAGE_LEVEL_EQUIPMENT: Final[frozenset[URIRef]] = frozenset({
    CIM.EnergyConsumer, CIM.SynchronousMachine, CIM.LinearShuntCompensator, CIM.EnergySource,
    CIM.ExternalNetworkInjection})
INFERRED: Final[str] = ("inferred by powercoder.semantic.cgmes (not network data): "
                        "substations group buses joined by transformers; one voltage level per bus")


def infer_containment(graph: Graph, model_id: str) -> tuple[list[tuple], list[tuple]]:
    """Containers CGMES needs but the case has none of: (EQ triples, TP triples).

    Without containers a CGMES importer reads every node as a boundary node (pypowsybl:
    "Unexpected boundarySide and modelSide at boundaryNode", 2026-10-23). snemSA carries
    no substations or voltage levels, so they are *inferred*, the way pypowsybl's MATPOWER
    importer infers them: buses joined by a transformer share a substation, and each bus
    gets its own voltage level. Every inferred object says so in its description.
    """
    node = lambda name: URIRef(f"urn:uuid:{uuid.uuid5(HEADER_NAMESPACE, f'{model_id}/{name}')}")
    equipment_nodes: dict[URIRef, list[URIRef]] = {}
    for terminal, equipment in graph.subject_objects(CIM["Terminal.ConductingEquipment"]):
        for cn in graph.objects(terminal, CIM["Terminal.ConnectivityNode"]):
            equipment_nodes.setdefault(equipment, []).append(cn)
    parent = {cn: cn for cn in graph.subjects(RDF.type, CIM.ConnectivityNode)}

    def root(cn: URIRef) -> URIRef:
        while parent[cn] != cn:
            parent[cn] = parent[parent[cn]]
            cn = parent[cn]
        return cn

    for transformer in graph.subjects(RDF.type, CIM.PowerTransformer):
        nodes = equipment_nodes.get(transformer, [])
        for other in nodes[1:]:
            parent[root(other)] = root(nodes[0])
    region, sub_region = node("region"), node("subregion")
    eq: list[tuple] = [(region, RDF.type, CIM.GeographicalRegion), (region, CIM["IdentifiedObject.name"], Literal(model_id)),
                       (region, CIM["IdentifiedObject.description"], Literal(INFERRED)),
                       (sub_region, RDF.type, CIM.SubGeographicalRegion),
                       (sub_region, CIM["IdentifiedObject.name"], Literal(model_id)),
                       (sub_region, CIM["SubGeographicalRegion.Region"], region),
                       (sub_region, CIM["IdentifiedObject.description"], Literal(INFERRED))]
    tp: list[tuple] = []
    substation_of, level_of = {}, {}
    for cn in parent:
        group = root(cn)
        if group not in substation_of:
            sub = substation_of[group] = node(f"substation/{group}")
            eq += [(sub, RDF.type, CIM.Substation), (sub, CIM["IdentifiedObject.name"], Literal(f"S {len(substation_of)}")),
                   (sub, CIM["Substation.Region"], sub_region), (sub, CIM["IdentifiedObject.description"], Literal(INFERRED))]
        level = level_of[cn] = node(f"voltagelevel/{cn}")
        name = next(graph.objects(cn, CIM["IdentifiedObject.name"]), Literal(str(cn)))
        eq += [(level, RDF.type, CIM.VoltageLevel), (level, CIM["IdentifiedObject.name"], name),
               (level, CIM["VoltageLevel.Substation"], substation_of[group]),
               (level, CIM["IdentifiedObject.description"], Literal(INFERRED)),
               (cn, CIM["ConnectivityNode.ConnectivityNodeContainer"], level)]
        for tn in graph.objects(cn, CIM["ConnectivityNode.TopologicalNode"]):
            for base in graph.objects(tn, CIM["TopologicalNode.BaseVoltage"]):
                eq.append((level, CIM["VoltageLevel.BaseVoltage"], base))
            tp.append((tn, CIM["TopologicalNode.ConnectivityNodeContainer"], level))
    for equipment, nodes in equipment_nodes.items():
        kinds = set(graph.objects(equipment, RDF.type))
        if kinds & VOLTAGE_LEVEL_EQUIPMENT and nodes:
            eq.append((equipment, CIM["Equipment.EquipmentContainer"], level_of[nodes[0]]))
        elif CIM.PowerTransformer in kinds and nodes:
            eq.append((equipment, CIM["Equipment.EquipmentContainer"], substation_of[root(nodes[0])]))
    for machine, unit in graph.subject_objects(CIM["RotatingMachine.GeneratingUnit"]):
        nodes = equipment_nodes.get(machine, [])
        if nodes:
            eq.append((unit, CIM["Equipment.EquipmentContainer"], substation_of[root(nodes[0])]))
    return eq, tp


# Node-breaker detail moved to EXT in bus-branch mode: the case has no switches, so
# connectivity nodes add nothing a CGMES importer can use (see split_profiles).
CONNECTIVITY_PROPERTIES: Final[frozenset[str]] = frozenset({
    "Terminal.ConnectivityNode", "ConnectivityNode.TopologicalNode", "ConnectivityNode.ConnectivityNodeContainer"})


# CIM16 properties typed Integer. The merged export writes numbers as xsd:double; CGMES
# importers parse these as integers ("For input string: \"1.0\"", pypowsybl, 2026-10-23).
INTEGER_PROPERTIES: Final[frozenset[str]] = frozenset({
    "ShuntCompensator.sections", "ShuntCompensator.normalSections", "ShuntCompensator.maximumSections",
    "ACDCTerminal.sequenceNumber", "TransformerEnd.endNumber", "PowerTransformerEnd.phaseAngleClock",
    "TapChanger.neutralStep", "TapChanger.lowStep", "TapChanger.highStep", "TapChanger.normalStep",
    "SynchronousMachine.referencePriority", "ExternalNetworkInjection.referencePriority"})


def as_cgmes_literal(predicate: URIRef, obj: object) -> object:
    """A value as CGMES RDF/XML writes it: a plain literal, integers without a fraction.

    CGMES instance files carry no ``rdf:datatype``, and importers match on the lexical
    form: powsybl-core's ``acLineSegments`` query requires ``sequenceNumber = "1"``, which a
    typed ``"1"^^xsd:integer`` does not equal in SPARQL (every line was dropped,
    2026-10-23), and it parses Integer properties strictly ("1.0" failed).

    Raises:
        ValueError: if an Integer-typed property holds a value that is not whole.
    """
    if not isinstance(obj, Literal):
        return obj
    value = obj.toPython()
    if str(predicate).split("#")[-1] in INTEGER_PROPERTIES:
        if float(value) != int(float(value)):
            raise ValueError(f"{predicate} must be an integer, got {obj}")
        return Literal(str(int(float(value))))
    if isinstance(value, bool):
        return Literal("true" if value else "false")
    return Literal(str(obj))


def derived_fields(graph: Graph) -> tuple[list[tuple], list[tuple]]:
    """CGMES fields pypowsybl expects that follow from the case itself: (EQ, SSH) triples.

    - ``SynchronousMachine.type`` = generator and SSH ``operatingMode`` = generator: every
      ``cim:SynchronousMachine`` here is a pandapower ``gen`` (``export.py``);
    - ``ShuntCompensator.normalSections`` = its ``sections`` (pandapower ``step``): the
      case records one state, so its normal state is that state;
    - ``GeneratingUnit.initialP`` = the generator's dispatch (powsybl-core's
      ``synchronousMachinesGenerators`` query requires it);
    - on voltage ``RegulatingControl`` s, SSH ``targetValueUnitMultiplier`` = k (targets
      are in kV) and ``discrete`` = false (continuous voltage control);
    - on ``RatioTapChanger`` s without a recorded range, ``lowStep``/``highStep`` = the
      narrowest range holding the recorded and neutral steps, ``normalStep`` = the recorded
      step, ``neutralU`` = the tap-side end's rated voltage.
    """
    eq, ssh = [], []
    # GeneratingUnit.initialP: the unit's initial active power, i.e. the generator's
    # dispatch. RotatingMachine.p uses the load sign convention (negative for
    # generation, export.py), so initialP is its negation.
    for machine, unit in graph.subject_objects(CIM["RotatingMachine.GeneratingUnit"]):
        for p in graph.objects(machine, CIM["RotatingMachine.p"]):
            eq.append((unit, CIM["GeneratingUnit.initialP"], Literal(-float(p))))
    for machine in graph.subjects(RDF.type, CIM.SynchronousMachine):
        eq.append((machine, CIM["SynchronousMachine.type"], CIM["SynchronousMachineKind.generator"]))
        ssh.append((machine, CIM["SynchronousMachine.operatingMode"], CIM["SynchronousMachineOperatingMode.generator"]))
    for shunt, sections in graph.subject_objects(CIM["ShuntCompensator.sections"]):
        eq.append((shunt, CIM["ShuntCompensator.normalSections"], sections))
    # Ratio tap changers. powsybl-core's ratioTapChangers query requires lowStep, highStep,
    # normalStep and neutralU; without them all 403 were dropped and every transformer ran
    # at its nominal ratio (|dV| up to 0.275 pu against pandapower, 2026-10-23). snemSA has
    # no tap_min/tap_max, so the range is the narrowest one holding the recorded position
    # and the neutral step; the normal step is the recorded one; neutralU is the rated
    # voltage of the end the changer is on.
    for changer in graph.subjects(RDF.type, CIM.RatioTapChanger):
        step = next(graph.objects(changer, CIM["TapChanger.step"]), None)
        neutral = next(graph.objects(changer, CIM["TapChanger.neutralStep"]), None)
        end = next(graph.objects(changer, CIM["RatioTapChanger.TransformerEnd"]), None)
        if step is None or neutral is None:
            continue
        positions = (float(step), float(neutral))
        if next(graph.objects(changer, CIM["TapChanger.lowStep"]), None) is None:
            eq.append((changer, CIM["TapChanger.lowStep"], Literal(min(positions))))
        if next(graph.objects(changer, CIM["TapChanger.highStep"]), None) is None:
            eq.append((changer, CIM["TapChanger.highStep"], Literal(max(positions))))
        eq.append((changer, CIM["TapChanger.normalStep"], Literal(float(step))))
        for rated in graph.objects(end, CIM["PowerTransformerEnd.ratedU"]):
            eq.append((changer, CIM["TapChanger.neutralU"], rated))
    for injection in graph.subjects(RDF.type, CIM.ExternalNetworkInjection):
        for bound, value in (("ExternalNetworkInjection.minP", -UNBOUNDED_P_MW), ("ExternalNetworkInjection.maxP", UNBOUNDED_P_MW)):
            if next(graph.objects(injection, CIM[bound]), None) is None:
                eq.append((injection, CIM[bound], Literal(value)))
    # Voltage regulating controls: the export writes targetValue in kV (multiplier k),
    # and a generator's voltage control is continuous, not stepped (discrete = false).
    # powsybl-core reads enabled/targetValue only together with both (regulatingControls).
    for control, mode in graph.subject_objects(CIM["RegulatingControl.mode"]):
        if str(mode).endswith("RegulatingControlModeKind.voltage"):
            ssh.append((control, CIM["RegulatingControl.targetValueUnitMultiplier"], CIM["UnitMultiplier.k"]))
            ssh.append((control, CIM["RegulatingControl.discrete"], Literal(False)))
    return eq, ssh


def solved_reactive_power(net: object, model_id: str) -> dict[URIRef, float]:
    """Each generator's reactive power from a solved pandapower net, keyed by its machine IRI.

    For ``split_profiles``' ``initial_q``. powsybl-core's ``synchronousMachinesForUpdate``
    reads ``controlEnabled`` only together with ``RotatingMachine.q``; without it every
    machine loses voltage control and the load flow has no voltage-controlled bus
    (``NO_CALCULATION``, 2026-10-23). A voltage-controlled machine's Q is an output, so the
    honest initial value is a real solution: pandapower's, in CGMES's load sign convention
    (negative for generation), as ``RotatingMachine.p`` already is. Pass nothing and no
    ``q`` is written.

    Raises:
        ValueError: if the net has no generator results (run ``pp.runpp`` first).
    """
    from powercoder.semantic.export import _iri

    if not len(getattr(net, "res_gen", [])):
        raise ValueError("solved_reactive_power needs a solved net (pp.runpp)")
    return {_iri(model_id, "SynchronousMachine", idx)[0]: -float(net.res_gen.q_mvar[idx]) for idx in net.gen.index}


# CGMES requires ExternalNetworkInjection.minP/maxP; a pandapower ext_grid is an unbounded
# slack with none. This sentinel means "no limit", in the spirit of MATPOWER's 9999/10000
# "no limit" values, and is written only where the case gives no limit. Without minP/maxP
# powsybl-core's externalNetworkInjections query drops the injection (Lab 5, 2026-10-23).
UNBOUNDED_P_MW: Final[float] = 1e6


def solved_injections(net: object, model_id: str) -> dict[URIRef, dict[str, float]]:
    """Each ext_grid's P and Q from a solved pandapower net, in CGMES's load sign convention.

    powsybl-core's ``externalNetworkInjectionsForUpdate`` reads ``controlEnabled`` and the
    reference priority only together with ``ExternalNetworkInjection.p`` and ``q``.

    Raises:
        ValueError: if the net has no ext_grid results (run ``pp.runpp`` first).
    """
    from powercoder.semantic.export import _iri

    if len(net.ext_grid) and not len(getattr(net, "res_ext_grid", [])):
        raise ValueError("solved_injections needs a solved net (pp.runpp)")
    return {_iri(model_id, "ExternalNetworkInjection", idx)[0]: {
        "ExternalNetworkInjection.p": -float(net.res_ext_grid.p_mw[idx]),
        "ExternalNetworkInjection.q": -float(net.res_ext_grid.q_mvar[idx])} for idx in net.ext_grid.index}


def split_profiles(graph: Graph, model_id: str, scenario_time: str = DEFAULT_TIME,
                   created: str = DEFAULT_TIME, infer_containers: bool = True,
                   bus_branch: bool = True, initial_q: dict[URIRef, float] | None = None,
                   initial_injection: dict[URIRef, dict[str, float]] | None = None) -> dict[str, Graph]:
    """EQ, TP, SSH (each with its header) and EXT graphs from one merged export.

    Every non-header triple lands in exactly one document. A subject's ``rdf:type`` is
    repeated in each document that describes it, as CGMES RDF/XML does.

    ``bus_branch`` (default) writes the topology as pypowsybl's own CGMES export does,
    and as it imports: each terminal points at its topological node (TP), and the
    connectivity nodes go to EXT. With them in EQ, pypowsybl 1.16.1 imported the
    transformers and loads but dropped every line, generator and shunt, with no
    message in its import report (measured 2026-10-23).
    """
    out = {name: Graph() for name in (*PROFILES, "EXT")}
    for g in out.values():
        g.bind("cim", CIM)
        g.bind("md", MD)
        g.bind("pcx", PCX)
    classes: dict[URIRef, set[URIRef]] = {}
    for subject, cls in graph.subject_objects(RDF.type):
        classes.setdefault(subject, set()).add(cls)
    described: dict[URIRef, set[str]] = {}
    for subject, predicate, obj in graph:
        if predicate == RDF.type:
            continue
        profile = property_profile(predicate, classes.get(subject, set()))
        if bus_branch and (str(predicate).split("#")[-1] in CONNECTIVITY_PROPERTIES
                           or CIM.ConnectivityNode in classes.get(subject, set())):
            profile = "EXT"
        if MD.FullModel in classes.get(subject, set()):
            # The merged export's own header carries network-level fields (name, base
            # MVA, frequency) that import_ needs; they are not CGMES, so they go to EXT.
            profile = None if profile is None else "EXT"
        if profile is None:
            continue
        out[profile].add((subject, predicate, obj if profile == "EXT" else as_cgmes_literal(predicate, obj)))
        described.setdefault(subject, set()).add(profile)
    for subject, cls in graph.subject_objects(RDF.type):
        if cls == MD.FullModel:
            if "EXT" in described.get(subject, set()):
                out["EXT"].add((subject, RDF.type, cls))
            continue
        for profile in described.get(subject) or {"TP" if cls in TP_CLASSES else "EQ"}:
            if profile != "EXT" or (bus_branch and cls == CIM.ConnectivityNode):
                out[profile].add((subject, RDF.type, cls))
    if infer_containers:
        eq, tp = infer_containment(graph, model_id)
        for triple in eq:
            # In bus-branch mode the connectivity nodes are EXT, so their containers are too.
            out["EXT" if bus_branch and triple[1] == CIM["ConnectivityNode.ConnectivityNodeContainer"] else "EQ"].add(triple)
        for triple in tp:
            out["TP"].add(triple)
    if bus_branch:
        for terminal, cn in graph.subject_objects(CIM["Terminal.ConnectivityNode"]):
            for tn in graph.objects(cn, CIM["ConnectivityNode.TopologicalNode"]):
                out["TP"].add((terminal, CIM["Terminal.TopologicalNode"], tn))
                out["TP"].add((terminal, RDF.type, CIM.Terminal))
    eq_extra, ssh_extra = derived_fields(graph)
    for machine, q in (initial_q or {}).items():
        ssh_extra.append((machine, CIM["RotatingMachine.q"], Literal(q)))
    for injection, values in (initial_injection or {}).items():
        for name, value in values.items():
            ssh_extra.append((injection, CIM[name], Literal(value)))
    for subject, predicate, obj in eq_extra:
        out["EQ"].add((subject, predicate, as_cgmes_literal(predicate, obj)))
    for triple in ssh_extra:
        out["SSH"].add(triple)
        for cls in classes.get(triple[0], set()):
            out["SSH"].add((triple[0], RDF.type, cls))
    for profile in PROFILES:
        _, triples = header(model_id, profile, scenario_time, created)
        for triple in triples:
            out[profile].add(triple)
        typed = [t for t in out[profile] if isinstance(t[2], Literal) and (t[2].datatype or t[2].language)]
        for subject, predicate, obj in typed:
            out[profile].remove((subject, predicate, obj))
            out[profile].add((subject, predicate, as_cgmes_literal(predicate, obj)))
    return out


def write_cgmes_zip(graph: Graph, model_id: str, path: Path, scenario_time: str = DEFAULT_TIME,
                    created: str = DEFAULT_TIME) -> list[str]:
    """Write EQ/TP/SSH as RDF/XML (the CGMES exchange format) and EXT as Turtle into one zip."""
    docs = split_profiles(graph, model_id, scenario_time, created)
    names = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for profile, doc in docs.items():
            name = f"{model_id}_{profile}." + ("ttl" if profile == "EXT" else "xml")
            buffer = io.BytesIO()
            doc.serialize(buffer, format="turtle" if profile == "EXT" else "xml")
            archive.writestr(name, buffer.getvalue())
            names.append(name)
    return names
