"""SHACL accepts the exported graphs and rejects deliberately broken ones (PC-REQ-020)."""
from __future__ import annotations

import copy

import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

from rdflib import RDF, Graph, Literal, URIRef  # noqa: E402

from powercoder.semantic._cases import SMALL_CASES  # noqa: E402
from powercoder.semantic.export import _dbl, export_network, mrid  # noqa: E402
from powercoder.semantic.namespaces import CIM  # noqa: E402
from powercoder.semantic.validate import ValidationReport, validate_graph  # noqa: E402

MODEL: str = "case14"


def iri(kind: str, index: object) -> URIRef:
    """The exporter's IRI for ``kind``/``index`` of the case14 model."""
    return URIRef(f"urn:uuid:{mrid(MODEL, kind, index)}")


@pytest.fixture(scope="module")
def good() -> Graph:
    """The exported case14 graph (copied by each test before breaking it)."""
    return export_network(SMALL_CASES[MODEL](), model_id=MODEL)


def keys(report: ValidationReport) -> set[tuple[str, str, str]]:
    """``(focus, path, component local name)`` for every violation."""
    return {(v.focus, v.path, v.component.rsplit("#", 1)[-1]) for v in report.violations}


@pytest.mark.parametrize("case", sorted(SMALL_CASES))
def test_shacl_accepts_exported_small_cases(case: str) -> None:
    """Every built-in case exports to a conforming graph."""
    report = validate_graph(export_network(SMALL_CASES[case](), model_id=case))
    assert report.conforms, report.text


def test_shacl_accepts_exported_snem_sa(sa_graph) -> None:  # noqa: ANN001
    """The real CSIRO SA case conforms, including its 2 negative-x series-compensated lines."""
    report = validate_graph(sa_graph)
    assert report.conforms, report.text
    assert report.violations == []


def test_line_missing_a_terminal_is_rejected(good: Graph) -> None:
    """Dropping line 0's second terminal violates "every line has two terminals"."""
    g = copy.deepcopy(good)
    t2 = iri("Terminal", "line/0/2")
    g.remove((t2, None, None))
    report = validate_graph(g)
    assert not report.conforms
    line = str(iri("ACLineSegment", 0))
    assert keys(report) == {(line, f"^{CIM['Terminal.ConductingEquipment']}",
                             "MinCountConstraintComponent")}


def test_negative_impedance_is_rejected(good: Graph) -> None:
    """A negative series resistance violates "r >= 0"."""
    g = copy.deepcopy(good)
    line = iri("ACLineSegment", 3)
    g.set((line, CIM["ACLineSegment.r"], _dbl(-0.01)))
    report = validate_graph(g)
    assert keys(report) == {(str(line), str(CIM["ACLineSegment.r"]),
                             "MinInclusiveConstraintComponent")}
    assert "must be present and >= 0 ohm" in report.violations[0].message


def test_non_positive_transformer_rating_is_rejected(good: Graph) -> None:
    """A zero transformer rating violates "ratedS > 0"."""
    g = copy.deepcopy(good)
    end = iri("PowerTransformerEnd", "0/1")
    g.set((end, CIM["PowerTransformerEnd.ratedS"], _dbl(0.0)))
    assert keys(validate_graph(g)) == {(str(end), str(CIM["PowerTransformerEnd.ratedS"]),
                                        "MinExclusiveConstraintComponent")}


def test_dangling_node_is_rejected(good: Graph) -> None:
    """A ConnectivityNode no terminal reaches violates "no dangling node"."""
    g = copy.deepcopy(good)
    cn, tn = URIRef("urn:uuid:dangling-cn"), iri("TopologicalNode", 0)
    g.add((cn, RDF.type, CIM.ConnectivityNode))
    g.add((cn, CIM["IdentifiedObject.mRID"], Literal("dangling-cn")))
    g.add((cn, CIM["ConnectivityNode.TopologicalNode"], tn))
    assert keys(validate_graph(g)) == {(str(cn), f"^{CIM['Terminal.ConnectivityNode']}",
                                        "MinCountConstraintComponent")}


def test_terminal_on_a_missing_node_is_rejected(good: Graph) -> None:
    """A terminal pointing at a node that does not exist violates "terminal on a node"."""
    g = copy.deepcopy(good)
    term = iri("Terminal", "load/0/1")
    g.set((term, CIM["Terminal.ConnectivityNode"], URIRef("urn:uuid:no-such-node")))
    assert keys(validate_graph(g)) == {(str(term), str(CIM["Terminal.ConnectivityNode"]),
                                        "ClassConstraintComponent")}


def test_missing_base_voltage_is_rejected(good: Graph) -> None:
    """A BaseVoltage without nominalVoltage violates "base voltages present"."""
    g = copy.deepcopy(good)
    bv = next(g.subjects(RDF.type, CIM.BaseVoltage))
    g.remove((bv, CIM["BaseVoltage.nominalVoltage"], None))
    assert keys(validate_graph(g)) == {(str(bv), str(CIM["BaseVoltage.nominalVoltage"]),
                                        "MinCountConstraintComponent")}
