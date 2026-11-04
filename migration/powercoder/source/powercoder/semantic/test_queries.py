"""Typed, bounded graph queries: they match pandapower and never pass a partial answer as complete."""

from __future__ import annotations

import pandapower as pp
import pytest
from rdflib import Graph

pytest.importorskip("rdflib")

from powercoder.semantic import queries as q  # noqa: E402

# A voltage level snemSA has many buses at, so a small budget must truncate.
VOLTAGE_KV: float = 275.0
SMALL_BUDGET: int = 5


def test_buses_at_voltage_match_pandapower_and_report_truncation(sa_graph: Graph, sa_net: pp.pandapowerNet) -> None:
    truth = sorted(int(b) for b in sa_net.bus.index[sa_net.bus.vn_kv == VOLTAGE_KV])
    full = q.run(sa_graph, "buses_at_voltage", max_rows=q.MAX_ROWS, voltage=VOLTAGE_KV)
    assert [r["index"] for r in full["rows"]] == truth and not full["truncated"]
    cut = q.run(sa_graph, "buses_at_voltage", max_rows=SMALL_BUDGET, voltage=VOLTAGE_KV)
    assert cut["truncated"] and cut["total"] == len(truth) and len(cut["rows"]) == SMALL_BUDGET


def test_only_named_queries_with_typed_parameters_run(sa_graph: Graph) -> None:
    with pytest.raises(KeyError):
        q.run(sa_graph, "anything_else")
    with pytest.raises(ValueError, match="takes exactly"):
        q.run(sa_graph, "equipment_at_bus", bus=985, extra=1)
    with pytest.raises(ValueError, match="must be int"):
        q.run(sa_graph, "equipment_at_bus", bus="985 } DELETE {")
    with pytest.raises(ValueError, match="max_rows"):
        q.run(sa_graph, "equipment_at_bus", max_rows=q.MAX_ROWS + 1, bus=985)
