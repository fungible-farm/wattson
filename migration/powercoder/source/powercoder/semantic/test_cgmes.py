"""The CGMES profile split (backlog 0034): complete, disjoint, and still round-trips."""

from __future__ import annotations

import pandapower as pp
import pytest
from rdflib import RDF, Graph

pytest.importorskip("rdflib")

from powercoder.semantic.cgmes import PROFILES, split_profiles  # noqa: E402
from powercoder.semantic.import_ import import_network  # noqa: E402
from powercoder.semantic.namespaces import MD, PCX  # noqa: E402

# Same bound as test_roundtrip's: the measured pandapower re-solve agreement (1.05e-12 pu).
VOLTAGE_TOLERANCE_PU: float = 1e-9


def non_header(graph: Graph) -> set:
    return {t for t in graph if not str(t[1]).startswith(str(MD)) and t[2] != MD.FullModel}


def same_values(triples: set) -> set:
    """Triples with literals compared by lexical value (CGMES documents use plain literals)."""
    def norm(o):  # noqa: ANN001, ANN202
        if not hasattr(o, "toPython") or not hasattr(o, "datatype"):
            return o
        text = str(o)
        if text in {"true", "false"}:
            return text == "true"
        try:
            return float(text)
        except ValueError:
            return text
    return {(s, p, norm(o)) for s, p, o in triples}


def test_every_triple_lands_in_exactly_one_document(sa_graph: Graph) -> None:
    docs = split_profiles(sa_graph, "snem-sa-2000", infer_containers=False, bus_branch=False)
    derived = {"SynchronousMachine.type", "SynchronousMachine.operatingMode", "ShuntCompensator.normalSections",
               "GeneratingUnit.initialP", "RegulatingControl.targetValueUnitMultiplier", "RegulatingControl.discrete",
               "TapChanger.lowStep", "TapChanger.highStep", "TapChanger.normalStep", "TapChanger.neutralU"}
    for doc in docs.values():
        for triple in [t for t in doc if str(t[1]).split("#")[-1] in derived]:
            doc.remove(triple)
    untyped = {name: {t for t in non_header(doc) if t[1] != RDF.type} for name, doc in docs.items()}
    assert same_values(set().union(*untyped.values())) == same_values({t for t in non_header(sa_graph) if t[1] != RDF.type})
    names = list(untyped)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not same_values(untyped[a]) & same_values(untyped[b]), (a, b)


def test_each_cgmes_document_declares_its_profile_and_has_no_extension_fields(sa_graph: Graph) -> None:
    docs = split_profiles(sa_graph, "snem-sa-2000")
    for name, uri in PROFILES.items():
        assert {str(o) for o in docs[name].objects(None, MD["Model.profile"])} == {uri}
        assert not any(str(p).startswith(str(PCX)) for p in docs[name].predicates())
    assert len(list(docs["TP"].objects(None, MD["Model.DependentOn"]))) == 1


def test_the_profiles_together_round_trip_to_the_same_power_flow(sa_graph: Graph, sa_net: pp.pandapowerNet) -> None:
    union = Graph()
    for doc in split_profiles(sa_graph, "snem-sa-2000").values():
        union += doc
    back = import_network(union)
    assert back.sn_mva == sa_net.sn_mva
    pp.runpp(back, numba=True)
    pp.runpp(sa_net, numba=True)
    assert (back.res_bus.vm_pu - sa_net.res_bus.vm_pu).abs().max() <= VOLTAGE_TOLERANCE_PU


def test_inferred_containers_are_labelled_and_cover_every_node(sa_graph: Graph) -> None:
    """CGMES reads an uncontained node as a boundary node; every container says it was inferred."""
    from powercoder.semantic.cgmes import INFERRED
    from powercoder.semantic.namespaces import CIM

    docs = split_profiles(sa_graph, "snem-sa-2000")
    eq = docs["EQ"]
    nodes = set(sa_graph.subjects(RDF.type, CIM.ConnectivityNode))
    contained = set(docs["EXT"].subjects(CIM["ConnectivityNode.ConnectivityNodeContainer"], None))
    assert contained == nodes  # bus-branch mode: connectivity nodes and their containers are EXT
    for kind in (CIM.Substation, CIM.VoltageLevel, CIM.GeographicalRegion, CIM.SubGeographicalRegion):
        for container in eq.subjects(RDF.type, kind):
            assert str(next(eq.objects(container, CIM["IdentifiedObject.description"]))) == INFERRED
    assert len(set(docs["TP"].subjects(CIM["TopologicalNode.ConnectivityNodeContainer"], None))) == len(nodes)


def test_bus_branch_mode_links_every_terminal_to_its_topological_node(sa_graph: Graph) -> None:
    from powercoder.semantic.namespaces import CIM

    docs = split_profiles(sa_graph, "snem-sa-2000")
    terminals = set(sa_graph.subjects(RDF.type, CIM.Terminal))
    assert set(docs["TP"].subjects(CIM["Terminal.TopologicalNode"], None)) == terminals
    assert not set(docs["EQ"].subjects(RDF.type, CIM.ConnectivityNode))


def test_a_disconnected_terminal_survives_plain_cgmes_literals(sa_net: pp.pandapowerNet) -> None:
    """CGMES writes booleans as plain "false"; bool("false") is True, so this must be parsed."""
    from powercoder.semantic.export import export_network

    import copy

    net = copy.deepcopy(sa_net)
    line = int(net.line.index[0])
    net.line.loc[line, "in_service"] = False
    union = Graph()
    for doc in split_profiles(export_network(net, "snem-sa-2000"), "snem-sa-2000").values():
        union += doc
    assert not bool(import_network(union).line.in_service[line])


def test_initial_reactive_power_comes_from_a_solved_net_or_is_not_written(sa_graph: Graph, sa_net: pp.pandapowerNet) -> None:
    from powercoder.semantic.cgmes import solved_reactive_power
    from powercoder.semantic.namespaces import CIM

    assert not list(split_profiles(sa_graph, "snem-sa-2000")["SSH"].objects(None, CIM["RotatingMachine.q"]))
    pp.runpp(sa_net, numba=True)
    q = solved_reactive_power(sa_net, "snem-sa-2000")
    written = split_profiles(sa_graph, "snem-sa-2000", initial_q=q)["SSH"]
    assert len(list(written.objects(None, CIM["RotatingMachine.q"]))) == len(sa_net.gen)
