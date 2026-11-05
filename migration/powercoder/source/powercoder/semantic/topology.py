"""Physical vs. electrical connectivity derived from the CIM graph (PC-REQ-021, first step).

- *Physical* connectivity: every piece of conducting equipment joins the ConnectivityNodes of its
  terminals, whatever the switching state -- what is built.
- *Electrical* connectivity under a switching state: equipment joins its nodes only when every
  one of its terminals has ``ACDCTerminal.connected = true``, and out-of-service nodes
  (``pcx:inService = false``) drop out -- what is energisable.

Both are computed from the graph alone (no pandapower), so they can be cross-checked against
``pandapower.topology`` on the imported network. The switching state is the SSH
``ACDCTerminal.connected`` flag; :func:`set_equipment_connected` changes it.

Versioned snapshots (model version, validity interval, tap positions, source, quality) are in
:mod:`powercoder.semantic.snapshots`. Not yet built (named, PC-REQ-021 remainder): ``cim:Switch``
objects (the subset refuses switch tables); TopologicalNode re-derivation from closed switches.
"""
from __future__ import annotations

from collections import defaultdict

import networkx as nx
from rdflib import RDF, Graph, Literal, URIRef

from powercoder.semantic.namespaces import CIM, PCX


def _equipment_terminals(graph: Graph) -> dict[URIRef, list[tuple[URIRef, bool]]]:
    """Map equipment IRI -> [(ConnectivityNode, connected)] over all its terminals."""
    out: dict[URIRef, list[tuple[URIRef, bool]]] = defaultdict(list)
    for t in graph.subjects(RDF.type, CIM.Terminal):
        eq = graph.value(t, CIM["Terminal.ConductingEquipment"])
        cn = graph.value(t, CIM["Terminal.ConnectivityNode"])
        connected = graph.value(t, CIM["ACDCTerminal.connected"])
        out[eq].append((cn, bool(connected.toPython()) if connected is not None else True))
    return out


def connectivity_graph(graph: Graph, electrical: bool) -> nx.MultiGraph:
    """Node/edge graph over ConnectivityNodes; edges carry the equipment IRI as ``key``.

    Args:
        graph: the CIM graph.
        electrical: False for physical connectivity, True for electrical connectivity under the
            graph's switching state.
    """
    g = nx.MultiGraph()
    for cn in graph.subjects(RDF.type, CIM.ConnectivityNode):
        in_service = graph.value(cn, PCX.inService)
        if electrical and in_service is not None and not bool(in_service.toPython()):
            continue
        g.add_node(cn)
    for eq, terms in _equipment_terminals(graph).items():
        if len(terms) < 2:
            continue
        if electrical and not all(c for _cn, c in terms):
            continue
        nodes = [cn for cn, _c in terms]
        if all(n in g for n in nodes):
            for other in nodes[1:]:
                g.add_edge(nodes[0], other, key=eq)
    return g


def islands(graph: Graph, electrical: bool = True) -> set[frozenset[URIRef]]:
    """Connected components of the physical or electrical connectivity graph."""
    return {frozenset(c) for c in nx.connected_components(connectivity_graph(graph, electrical))}


def set_equipment_connected(graph: Graph, equipment: URIRef, connected: bool) -> int:
    """Set ``ACDCTerminal.connected`` on every terminal of ``equipment``; returns terminal count.

    This changes the switching state in place, i.e. it opens or closes the equipment.
    """
    terminals = list(graph.subjects(CIM["Terminal.ConductingEquipment"], equipment))
    for t in terminals:
        graph.set((t, CIM["ACDCTerminal.connected"], Literal(bool(connected))))
    return len(terminals)
