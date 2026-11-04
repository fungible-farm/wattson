"""pandapower -> CIM Turtle -> pandapower gives the same AC power flow (G4 cheapest first step)."""
from __future__ import annotations

import copy
from typing import Final

import pandapower as pp
import pytest

pytest.importorskip("rdflib")

from rdflib import RDF, Literal  # noqa: E402

from powercoder.semantic._cases import SA_NETWORK_ID, SMALL_CASES  # noqa: E402
from powercoder.semantic.export import (  # noqa: E402
    UnsupportedNetworkFeature, export_network, from_turtle, graph_stats, to_turtle,
)
from powercoder.semantic.import_ import import_network  # noqa: E402
from powercoder.semantic.namespaces import CIM  # noqa: E402

VM_TOL_PU: Final[float] = 1e-9
"""Bus voltage magnitude tolerance. Measured max |dV| on snem-sa-2000: 1.05e-12 pu (2026-10-23,
float rounding of the r/x <-> vk/vkr and kV <-> pu conversions); 0.0 on case9/14/30. ~1000x margin,
and far below the 1e-4 pu scale any study in this repo reports."""

VA_TOL_DEG: Final[float] = 1e-6
"""Bus voltage angle tolerance. Measured max on snem-sa-2000: 1.06e-9 deg."""

LOADING_TOL_PERCENT: Final[float] = 1e-7
"""Line/transformer loading tolerance. Measured max on snem-sa-2000: 4.4e-11 %."""


def roundtrip(net: pp.pandapowerNet, model_id: str) -> pp.pandapowerNet:
    """Export to Turtle text, parse it back, and import -- the full serialised round trip."""
    return import_network(from_turtle(to_turtle(export_network(net, model_id=model_id))))


def assert_same_power_flow(original: pp.pandapowerNet, rebuilt: pp.pandapowerNet) -> dict[str, float]:
    """Run AC power flow on both nets and assert identical results; returns the max differences."""
    pp.runpp(original)
    pp.runpp(rebuilt)
    assert original.converged and rebuilt.converged
    assert original._ppc["iterations"] == rebuilt._ppc["iterations"]
    assert list(rebuilt.res_bus.index) == list(original.res_bus.index)
    diffs = {
        "vm_pu": float((original.res_bus.vm_pu - rebuilt.res_bus.vm_pu).abs().max()),
        "va_degree": float((original.res_bus.va_degree - rebuilt.res_bus.va_degree).abs().max()),
        "line_loading_percent": float(
            (original.res_line.loading_percent - rebuilt.res_line.loading_percent).abs().max()),
        "trafo_loading_percent": float(
            (original.res_trafo.loading_percent - rebuilt.res_trafo.loading_percent).abs().max())
        if len(original.trafo) else 0.0,
    }
    assert diffs["vm_pu"] <= VM_TOL_PU, diffs
    assert diffs["va_degree"] <= VA_TOL_DEG, diffs
    assert diffs["line_loading_percent"] <= LOADING_TOL_PERCENT, diffs
    assert diffs["trafo_loading_percent"] <= LOADING_TOL_PERCENT, diffs
    return diffs


@pytest.mark.parametrize("case", sorted(SMALL_CASES))
def test_small_case_round_trip_gives_identical_power_flow(case: str) -> None:
    """Built-in cases survive the Turtle round trip with identical runpp results."""
    net = SMALL_CASES[case]()
    rebuilt = roundtrip(copy.deepcopy(net), case)
    for table in ("bus", "line", "trafo", "gen", "ext_grid", "sgen", "load", "shunt"):
        assert len(getattr(rebuilt, table)) == len(getattr(net, table)), table
    assert_same_power_flow(net, rebuilt)


def test_snem_sa_round_trip_gives_identical_power_flow(sa_net, sa_graph) -> None:  # noqa: ANN001
    """The real CSIRO SA case: export -> Turtle -> import, then identical AC power flow."""
    rebuilt = import_network(from_turtle(to_turtle(sa_graph)))
    diffs = assert_same_power_flow(copy.deepcopy(sa_net), rebuilt)
    print(f"snem-sa-2000 round-trip max differences: {diffs}")


def test_snem_sa_graph_counts_match_the_case(sa_net, sa_graph) -> None:  # noqa: ANN001
    """One CIM object per pandapower element; terminals = 2 per branch + 1 per one-port."""
    classes = graph_stats(sa_graph)["classes"]
    n = sa_net
    assert classes["cim:ConnectivityNode"] == classes["cim:TopologicalNode"] == len(n.bus)
    assert classes["cim:ACLineSegment"] == len(n.line)
    assert classes["cim:PowerTransformer"] == len(n.trafo)
    assert classes["cim:PowerTransformerEnd"] == 2 * len(n.trafo)
    assert classes["cim:SynchronousMachine"] == len(n.gen)
    assert classes["cim:EnergyConsumer"] == len(n.load)
    assert classes["cim:LinearShuntCompensator"] == len(n.shunt)
    assert classes["cim:Terminal"] == (2 * (len(n.line) + len(n.trafo))
                                       + len(n.gen) + len(n.load) + len(n.shunt)
                                       + len(n.sgen) + len(n.ext_grid))
    assert classes["cim:BaseVoltage"] == n.bus.vn_kv.nunique()


def test_every_reference_resolves(sa_graph) -> None:  # noqa: ANN001
    """G4 exit criterion: every IRI object of a cim: association is a typed subject in the graph."""
    typed = set(sa_graph.subjects(RDF.type, None))
    for s, p, o in sa_graph:
        if p == RDF.type or isinstance(o, Literal) or not str(p).startswith(str(CIM)):
            continue
        if str(o).startswith(str(CIM)):  # enumeration value, e.g. RegulatingControlModeKind.voltage
            continue
        assert o in typed, (s, p, o)


def test_export_is_deterministic() -> None:
    """Same network, same model id -> byte-identical Turtle (stable uuid5 mRIDs)."""
    net = SMALL_CASES["case14"]()
    assert to_turtle(export_network(net, "case14")) == to_turtle(export_network(net, "case14"))


def test_turtle_keeps_full_double_precision() -> None:
    """rdflib's stock Turtle writer keeps 7 significant digits for doubles; ours keeps all 17."""
    net = SMALL_CASES["case9+sgen"]()
    net.line.loc[0, "r_ohm_per_km"] = 0.1234567890123456789
    graph = export_network(net, "precision")
    line0 = [s for s in graph.subjects(RDF.type, CIM.ACLineSegment)
             if graph.value(s, CIM["IdentifiedObject.name"]) == Literal("ACLineSegment_0")][0]
    exact = from_turtle(to_turtle(graph)).value(line0, CIM["ACLineSegment.r"]).toPython()
    stock = from_turtle(graph.serialize(format="turtle")).value(line0, CIM["ACLineSegment.r"])
    assert exact == net.line.at[0, "r_ohm_per_km"] * net.line.at[0, "length_km"]
    assert stock.toPython() != exact  # documents the stock-serializer loss this module avoids


def test_unsupported_tables_are_refused_not_dropped() -> None:
    """A switch table is outside the subset: export raises instead of silently dropping it."""
    net = SMALL_CASES["case9+sgen"]()
    pp.create_switch(net, bus=0, element=1, et="b")
    with pytest.raises(UnsupportedNetworkFeature, match="switch"):
        export_network(net, "with-switch")


def test_sa_model_id_is_the_powermcp_network_id(sa_graph) -> None:  # noqa: ANN001
    """The header names the PowerMCP network id the graph was exported from."""
    from powercoder.semantic.namespaces import MD

    header = next(sa_graph.subjects(RDF.type, MD.FullModel))
    assert str(sa_graph.value(header, CIM["IdentifiedObject.name"])) == SA_NETWORK_ID
