"""Switching state changes electrical, not physical, connectivity (PC-REQ-021)."""
from __future__ import annotations

import copy

import networkx as nx
import pandapower as pp
import pandapower.topology as top
import pytest

pytest.importorskip("rdflib")

from rdflib import URIRef  # noqa: E402

from wattson.semantic._cases import SA_NETWORK_ID, SMALL_CASES  # noqa: E402
from wattson.semantic.export import export_network, from_turtle, mrid, to_turtle  # noqa: E402
from wattson.semantic.import_ import import_network  # noqa: E402
from wattson.semantic.topology import islands, set_equipment_connected  # noqa: E402


def pandapower_islands(net: pp.pandapowerNet, model_id: str) -> set[frozenset[URIRef]]:
    """pandapower's own energised islands, mapped to the exporter's ConnectivityNode IRIs."""
    graph = top.create_nxgraph(net, respect_switches=True, include_out_of_service=False)
    return {frozenset(URIRef(f"urn:uuid:{mrid(model_id, 'ConnectivityNode', b)}") for b in comp)
            for comp in top.connected_components(graph)}


def bridge_line(net: pp.pandapowerNet) -> int:
    """Index of a line whose removal splits the network (found, not hard-coded)."""
    g = nx.Graph()
    g.add_edges_from(zip(net.line.from_bus, net.line.to_bus))
    g.add_edges_from(zip(net.trafo.hv_bus, net.trafo.lv_bus))
    bridges = {frozenset(e) for e in nx.bridges(g)}
    for idx, row in net.line.iterrows():
        if frozenset((row.from_bus, row.to_bus)) in bridges:
            return int(idx)
    raise AssertionError("no bridge line in this case")


def test_opening_a_line_splits_electrical_but_not_physical_connectivity() -> None:
    """case30: opening a bridge line adds one electrical island; physical stays one island."""
    model = "case30"
    net = SMALL_CASES[model]()
    graph = export_network(net, model_id=model)
    assert len(islands(graph, electrical=True)) == len(islands(graph, electrical=False)) == 1

    line = bridge_line(net)
    assert set_equipment_connected(graph, URIRef(f"urn:uuid:{mrid(model, 'ACLineSegment', line)}"),
                                   connected=False) == 2
    assert len(islands(graph, electrical=False)) == 1
    assert len(islands(graph, electrical=True)) == 2

    # The switching state survives Turtle and the import, and pandapower agrees on the islands.
    rebuilt = import_network(from_turtle(to_turtle(graph)))
    assert not bool(rebuilt.line.at[line, "in_service"])
    assert pandapower_islands(rebuilt, model) == islands(graph, electrical=True)


def test_out_of_service_line_in_pandapower_exports_as_open_terminals() -> None:
    """pandapower in_service=False -> ACDCTerminal.connected=false -> the same islands."""
    model = "case30"
    net = SMALL_CASES[model]()
    net.line.loc[bridge_line(net), "in_service"] = False
    graph = export_network(net, model_id=model)
    assert islands(graph, electrical=True) == pandapower_islands(net, model)
    assert len(islands(graph, electrical=True)) == 2


def test_snem_sa_graph_connectivity_matches_pandapower(sa_net, sa_graph) -> None:  # noqa: ANN001
    """G4 exit criterion on the real case: graph-derived islands equal pandapower's."""
    assert islands(sa_graph, electrical=True) == pandapower_islands(sa_net, SA_NETWORK_ID)
    assert islands(sa_graph, electrical=False) == islands(sa_graph, electrical=True)


def test_reclosing_restores_connectivity() -> None:
    """Open then close the same line: electrical connectivity returns to the intact state."""
    model = "case30"
    net = SMALL_CASES[model]()
    graph = export_network(net, model_id=model)
    before = islands(graph)
    eq = URIRef(f"urn:uuid:{mrid(model, 'ACLineSegment', bridge_line(net))}")
    set_equipment_connected(graph, eq, False)
    set_equipment_connected(copy.deepcopy(graph), eq, True)  # a copy: original stays open
    assert islands(graph) != before
    set_equipment_connected(graph, eq, True)
    assert islands(graph) == before
