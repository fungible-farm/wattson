"""Versioned topology snapshots: a stored state reproduces its islands, and time lookup is unambiguous."""

from __future__ import annotations

from pathlib import Path

import pytest
pytest.importorskip("rdflib")

from rdflib import RDF, Graph  # noqa: E402

from wattson.semantic import snapshots as sn  # noqa: E402
from wattson.semantic.namespaces import CIM  # noqa: E402
from wattson.semantic.topology import islands, set_equipment_connected  # noqa: E402

T0, T1, T2 = "2026-10-01T00:00:00", "2026-10-02T00:00:00", "2026-10-03T00:00:00"


def isolating_line(graph: Graph) -> object:
    """A line whose opening splits the network (FM-25's line 60 on snemSA is one)."""
    base = len(islands(graph))
    for line in graph.subjects(RDF.type, CIM.ACLineSegment):
        trial = sn.apply(graph, sn.capture(graph, T0, "test", "study"))
        set_equipment_connected(trial, line, False)
        if len(islands(trial)) > base:
            return line
    raise AssertionError("no isolating line")


def test_a_stored_snapshot_reproduces_the_islands_it_was_taken_with(sa_graph: Graph, tmp_path: Path) -> None:
    intact = sn.capture(sa_graph, T0, "EMS", "measured", valid_to=T1)
    outage = sn.apply(sa_graph, intact)
    line = isolating_line(sa_graph)
    set_equipment_connected(outage, line, False)
    after = sn.capture(outage, T1, "outage plan", "planned")
    store = sn.SnapshotStore(tmp_path / "snapshots.jsonl")
    store.append(intact)
    store.append(after)
    assert store.at("2026-10-01T12:00:00")["snapshot_id"] == intact["snapshot_id"]
    assert store.at(T2)["snapshot_id"] == after["snapshot_id"]
    assert islands(sn.apply(sa_graph, store.at(T2))) == islands(outage)
    assert islands(sn.apply(outage, store.at(T0))) == islands(sa_graph)
    changed = sn.diff(intact, after)["connected"]
    assert changed and all(v == (True, False) for v in changed.values())


def test_overlapping_validity_and_another_model_are_refused(sa_graph: Graph, tmp_path: Path) -> None:
    store = sn.SnapshotStore(tmp_path / "snapshots.jsonl")
    store.append(sn.capture(sa_graph, T0, "EMS", "measured", valid_to=T2))
    with pytest.raises(ValueError, match="overlaps"):
        store.append(sn.capture(sa_graph, T1, "EMS", "measured"))
    other = Graph()
    other += sa_graph
    other.remove((next(other.subjects(RDF.type, CIM.ACLineSegment)), None, None))
    with pytest.raises(ValueError, match="another model version"):
        sn.apply(other, store.all()[0])


def test_identical_states_share_an_id_and_quality_is_checked(sa_graph: Graph) -> None:
    assert sn.capture(sa_graph, T0, "a", "study")["snapshot_id"] == sn.capture(sa_graph, T0, "a", "study")["snapshot_id"]
    with pytest.raises(ValueError, match="quality"):
        sn.capture(sa_graph, T0, "a", "guessed")
