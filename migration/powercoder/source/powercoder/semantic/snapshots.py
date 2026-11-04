"""Versioned topology snapshots over the CIM graph (PC-REQ-021 remainder; G4 "historical snapshots reproduce").

A snapshot records the *state* of a model at a time: every terminal's SSH
``ACDCTerminal.connected`` flag (the switching state) and every tap changer's
``TapChanger.step``. It also records the model version it belongs to, its validity
interval, its source and its quality. The model version is the sha256 of the graph's
non-state triples, so a snapshot can only be applied to the model it was taken from.
Its id is the sha256 of its content, so identical states share an id.

:class:`SnapshotStore` is append-only (JSON lines). Validity intervals may not overlap,
so "the topology at time t" has at most one answer. Applying a stored snapshot to its
model reproduces exactly the islands (``topology.islands``) seen when it was captured; the
tests check this.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from rdflib import Graph, Literal, URIRef

from powercoder.semantic.namespaces import CIM

STATE_PREDICATES: frozenset[URIRef] = frozenset({CIM["ACDCTerminal.connected"], CIM["TapChanger.step"]})
QUALITIES: frozenset[str] = frozenset({"measured", "planned", "estimated", "study"})


class Snapshot(TypedDict):
    """The switching and tap state of one model version over one validity interval."""

    snapshot_id: str
    model_version: str
    valid_from: str  # ISO 8601, inclusive
    valid_to: str | None  # ISO 8601, exclusive; None = still valid
    source: str
    quality: str
    connected: dict[str, bool]  # terminal IRI -> ACDCTerminal.connected
    tap_steps: dict[str, float]  # tap changer IRI -> TapChanger.step


def model_version(graph: Graph) -> str:
    """sha256 of the graph's triples other than the state ones, in a canonical order."""
    lines = sorted(f"{s.n3()} {p.n3()} {o.n3()}" for s, p, o in graph if p not in STATE_PREDICATES)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def capture(graph: Graph, valid_from: str, source: str, quality: str, valid_to: str | None = None) -> Snapshot:
    """The graph's current switching and tap state as a snapshot.

    Raises:
        ValueError: for an unknown quality or an interval that ends before it starts.
    """
    if quality not in QUALITIES:
        raise ValueError(f"quality must be one of {sorted(QUALITIES)}")
    if valid_to is not None and datetime.fromisoformat(valid_to) <= datetime.fromisoformat(valid_from):
        raise ValueError("valid_to must be after valid_from")
    connected = {str(s): bool(o.toPython()) for s, o in graph.subject_objects(CIM["ACDCTerminal.connected"])}
    steps = {str(s): float(o.toPython()) for s, o in graph.subject_objects(CIM["TapChanger.step"])}
    body = {"model_version": model_version(graph), "valid_from": valid_from, "valid_to": valid_to, "source": source,
            "quality": quality, "connected": dict(sorted(connected.items())), "tap_steps": dict(sorted(steps.items()))}
    snapshot_id = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return Snapshot(snapshot_id=snapshot_id, **body)  # type: ignore[typeddict-item]


def apply(graph: Graph, snapshot: Snapshot) -> Graph:
    """A copy of ``graph`` in the snapshot's state.

    Raises:
        ValueError: if the snapshot belongs to another model version.
    """
    if model_version(graph) != snapshot["model_version"]:
        raise ValueError("snapshot was taken on another model version")
    out = copy.deepcopy(graph)
    for terminal, connected in snapshot["connected"].items():
        out.set((URIRef(terminal), CIM["ACDCTerminal.connected"], Literal(connected)))
    for changer, step in snapshot["tap_steps"].items():
        out.set((URIRef(changer), CIM["TapChanger.step"], Literal(step)))
    return out


def diff(before: Snapshot, after: Snapshot) -> dict[str, dict[str, tuple]]:
    """Terminals whose connection changed and tap changers whose step changed, as (before, after)."""
    return {"connected": {t: (before["connected"].get(t), v) for t, v in after["connected"].items()
                          if before["connected"].get(t) != v},
            "tap_steps": {c: (before["tap_steps"].get(c), v) for c, v in after["tap_steps"].items()
                          if before["tap_steps"].get(c) != v}}


class SnapshotStore:
    """Append-only JSON-lines store of snapshots for one model version."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def all(self) -> list[Snapshot]:
        """Every stored snapshot, in append order."""
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append(self, snapshot: Snapshot) -> None:
        """Store a snapshot; refuses another model version or an overlapping validity interval.

        Raises:
            ValueError: on a model-version mismatch or an overlap.
        """
        start = datetime.fromisoformat(snapshot["valid_from"])
        end = datetime.fromisoformat(snapshot["valid_to"]) if snapshot["valid_to"] else datetime.max
        for other in self.all():
            if other["model_version"] != snapshot["model_version"]:
                raise ValueError("a store holds one model version")
            o_start = datetime.fromisoformat(other["valid_from"])
            o_end = datetime.fromisoformat(other["valid_to"]) if other["valid_to"] else datetime.max
            if start < o_end and o_start < end:
                raise ValueError(f"validity overlaps snapshot {other['snapshot_id'][:12]}")
        with self.path.open("a") as stream:
            stream.write(json.dumps(snapshot, sort_keys=True) + "\n")

    def at(self, when: str) -> Snapshot | None:
        """The snapshot valid at ``when``, or None."""
        t = datetime.fromisoformat(when)
        for snapshot in self.all():
            start = datetime.fromisoformat(snapshot["valid_from"])
            end = datetime.fromisoformat(snapshot["valid_to"]) if snapshot["valid_to"] else datetime.max
            if start <= t < end:
                return snapshot
        return None
