"""Typed, bounded graph queries over the CIM graph (G4: "normal operation needs no unrestricted SPARQL").

Each query is a fixed SPARQL template with typed parameters, so a caller (or a model) can only
ask what is listed here. Every result carries the true total and is cut to a row budget, with
``truncated`` set, so a partial answer can never pass for a complete one. Parameters are bound
as typed literals through rdflib's ``initBindings``, never pasted into the query text.
"""

from __future__ import annotations

from typing import Any, TypedDict

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import XSD

from powercoder.semantic.namespaces import CIM, PCX

# Default and hard row budgets per query: enough to name a substation's equipment, small
# enough that one answer fits in a tool response (namespace.ls pages 50 ids the same way).
DEFAULT_ROWS: int = 50
MAX_ROWS: int = 500

QUERIES: dict[str, str] = {
    # Topological nodes at a nominal voltage (kV), by pandapower bus index.
    "buses_at_voltage": """
        SELECT ?node ?index WHERE {
            ?node a cim:TopologicalNode ; cim:TopologicalNode.BaseVoltage ?base .
            ?base cim:BaseVoltage.nominalVoltage ?kv .
            ?cn cim:ConnectivityNode.TopologicalNode ?node ; pcx:pandapowerIndex ?index .
            FILTER (?kv = ?voltage)
        } ORDER BY ?index""",
    # Equipment with a terminal at the bus with pandapower index ?bus.
    "equipment_at_bus": """
        SELECT DISTINCT ?equipment ?type WHERE {
            ?cn pcx:pandapowerIndex ?bus ; a cim:ConnectivityNode .
            ?terminal cim:Terminal.ConnectivityNode ?cn ; cim:Terminal.ConductingEquipment ?equipment .
            ?equipment a ?type .
        } ORDER BY ?type ?equipment""",
}
PARAMETERS: dict[str, dict[str, type]] = {"buses_at_voltage": {"voltage": float}, "equipment_at_bus": {"bus": int}}


class QueryResult(TypedDict):
    """Rows within the budget, the true total, and whether the rows were cut."""

    query: str
    rows: list[dict[str, Any]]
    total: int
    truncated: bool


def run(graph: Graph, name: str, max_rows: int = DEFAULT_ROWS, **params: Any) -> QueryResult:
    """Run one named query with typed parameters under a row budget.

    Raises:
        KeyError: for an unknown query.
        ValueError: for missing, extra or wrongly typed parameters, or a budget above MAX_ROWS.
    """
    template, expected = QUERIES[name], PARAMETERS[name]
    if set(params) != set(expected):
        raise ValueError(f"{name} takes exactly {sorted(expected)}")
    if not 1 <= max_rows <= MAX_ROWS:
        raise ValueError(f"max_rows must be in [1, {MAX_ROWS}]")
    bindings = {}
    for key, kind in expected.items():
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, (int, float) if kind is float else kind):
            raise ValueError(f"{key} must be {kind.__name__}")
        bindings[key] = Literal(float(value), datatype=XSD.double) if kind is float else Literal(int(value), datatype=XSD.integer)
    result = list(graph.query(template, initNs={"cim": CIM, "pcx": PCX}, initBindings=bindings))
    rows = [{str(var): (str(v) if isinstance(v, URIRef) else v.toPython()) for var, v in zip(result_vars(template), row)}
            for row in result[:max_rows]]
    return QueryResult(query=name, rows=rows, total=len(result), truncated=len(result) > max_rows)


def result_vars(template: str) -> list[str]:
    """The SELECT variables of a template, in order."""
    head = template.split("WHERE")[0]
    return [token[1:] for token in head.split() if token.startswith("?")]
