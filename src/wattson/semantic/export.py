"""Export a pandapower network to a CGMES-shaped CIM RDF graph (PC-REQ-020).

Sandbox stand-in, named per AGENTS.md: **a pragmatic CGMES-shaped subset, not a CGMES-conformant
export.** The class and property URIs are CIM16 / CGMES 2.4.15 (the namespace Lab 6's PRD-0007
traceability already uses), but:

What *is* represented (pandapower table -> CIM):

- ``bus`` -> one ``cim:ConnectivityNode`` (physical node) plus one ``cim:TopologicalNode``
  (electrical node) linked by ``cim:ConnectivityNode.TopologicalNode``; nominal voltage via a
  shared ``cim:BaseVoltage`` per distinct ``vn_kv``.
- ``line`` -> ``cim:ACLineSegment`` with whole-segment ``r``/``x``/``bch``/``gch`` (ohm, S),
  ``cim:Conductor.length`` (km), two ``cim:Terminal`` s, and its thermal rating as a
  ``cim:CurrentLimit`` (A) in a ``cim:OperationalLimitSet`` on terminal 1.
- ``trafo`` (two-winding) -> ``cim:PowerTransformer`` with two ``cim:PowerTransformerEnd`` s
  (``ratedU``, ``ratedS``; series ``r``/``x`` and magnetising ``g``/``b`` on end 1, referred to
  the HV side, zero on end 2 as CGMES does), ``phaseAngleClock`` for 30-degree shifts, and a
  ``cim:RatioTapChanger`` on the tap-side end.
- ``gen`` -> ``cim:SynchronousMachine`` + ``cim:GeneratingUnit`` + a voltage
  ``cim:RegulatingControl`` (target in kV); ``slack`` -> ``referencePriority = 1``.
- ``ext_grid`` -> ``cim:ExternalNetworkInjection`` + voltage ``cim:RegulatingControl``.
- ``sgen`` -> ``cim:EnergySource``; ``load`` -> ``cim:EnergyConsumer``;
  ``shunt`` -> ``cim:LinearShuntCompensator``.
- ``in_service`` -> ``cim:ACDCTerminal.connected`` on every terminal of the equipment (the SSH
  switching state); bus ``in_service`` -> ``pcx:inService`` (CIM has no node in-service flag).
- Injections use the CGMES SSH load sign convention: ``RotatingMachine.p``,
  ``EnergySource.activePower`` and ``reactivePower`` are *negative* for generation.

What is *not* represented (and therefore refused or dropped, never silently approximated):

- Separate EQ / TP / SSH / SV profile documents, ``md:Model.DependentOn``, profile URIs and
  ``rdf:about="#_mRID"`` RDF/XML framing -- one merged Turtle graph with ``urn:uuid:`` IRIs.
- The CIM OWL/RDFS schema itself (no ``rdfs:subClassOf`` inference); SHACL shapes target the
  concrete classes used here.
- ``cim:Switch``/``cim:Breaker``, ``trafo3w``, ``impedance``, ``dcline``, ``ward``/``xward``,
  ``storage``, ``motor``, asymmetric elements, FACTS: :func:`export_network` raises
  :class:`UnsupportedNetworkFeature` if any of these tables is non-empty.
- ZIP load characteristics (``cim:LoadResponseCharacteristic``), phase-shifting tap changers,
  tap-dependence tables, zero-sequence data, ``OperationalLimitType``, ``SvVoltage``/``SvPowerFlow``
  solved state, geodata, and OPF costs (``poly_cost`` is ignored: it does not affect ``runpp``).
- pandapower-only fields CGMES has no property for live under ``pcx:`` (see
  :mod:`wattson.semantic.namespaces`) so the round trip is exact; they are not CIM.

Identifiers: every resource is ``urn:uuid:<uuid5>`` derived from ``(model_id, kind, index)``, so the
same network exports to the same IRIs every time, and carries ``cim:IdentifiedObject.mRID`` and
``cim:IdentifiedObject.name``.
"""
from __future__ import annotations

import argparse
import io
import math
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Final, TypedDict

import pandapower as pp
from rdflib import RDF, XSD, Graph, Literal, URIRef
from rdflib.plugins.serializers.turtle import TurtleSerializer

from wattson.semantic.namespaces import CIM, MD, PCX, bind_prefixes

UUID_NAMESPACE: Final[uuid.UUID] = uuid.uuid5(uuid.NAMESPACE_URL, "urn:powercoder:semantic")
"""Root for deterministic uuid5 mRIDs; fixed so re-exports produce identical IRIs."""

PHASE_CLOCK_DEGREES: Final[float] = 30.0
"""IEC 60076-1 clock notation: one ``phaseAngleClock`` hour is 30 electrical degrees."""

KA_TO_A: Final[float] = 1000.0
"""``max_i_ka`` (kA, pandapower) -> ``CurrentLimit.value`` (A, CIM unit)."""

NF_TO_F: Final[float] = 1e-9
"""pandapower ``c_nf_per_km`` is nanofarad."""

US_TO_S: Final[float] = 1e-6
"""pandapower ``g_us_per_km`` is microsiemens."""

KW_TO_MW: Final[float] = 1e-3
"""pandapower ``pfe_kw`` is kilowatt; CIM works in MW/kV/S consistently."""

PERCENT: Final[float] = 100.0
"""pandapower ``vk_percent``/``vkr_percent``/``i0_percent`` are percentages."""

SUPPORTED_TABLES: Final[tuple[str, ...]] = (
    "bus", "line", "trafo", "gen", "ext_grid", "sgen", "load", "shunt",
)
"""Element tables this subset exports."""

UNSUPPORTED_TABLES: Final[tuple[str, ...]] = (
    "switch", "trafo3w", "impedance", "dcline", "ward", "xward", "storage", "motor",
    "asymmetric_load", "asymmetric_sgen", "svc", "tcsc", "ssc", "vsc", "b2b_vsc", "bi_vsc",
    "source_dc", "load_dc", "line_dc", "bus_dc",
)
"""Element tables outside the subset; a non-empty one makes the export refuse (not drop) it."""

ZIP_COLUMNS: Final[tuple[str, ...]] = (
    "const_z_percent", "const_i_percent", "const_z_p_percent", "const_i_p_percent",
    "const_z_q_percent", "const_i_q_percent",
)
"""Load voltage-dependence columns; non-zero values need LoadResponseCharacteristic (not built)."""


class UnsupportedNetworkFeature(ValueError):
    """The network uses a pandapower feature outside the exported CGMES-shaped subset."""


class GraphStats(TypedDict):
    """Size of an exported graph: total triples and instance count per rdf:type."""

    triples: int
    classes: dict[str, int]


def mrid(model_id: str, kind: str, index: Any) -> str:
    """Deterministic mRID for element ``kind``/``index`` of model ``model_id``."""
    return str(uuid.uuid5(UUID_NAMESPACE, f"{model_id}/{kind}/{index}"))


def _iri(model_id: str, kind: str, index: Any) -> tuple[URIRef, str]:
    """Return ``(urn:uuid IRI, mRID)`` for one resource."""
    m = mrid(model_id, kind, index)
    return URIRef(f"urn:uuid:{m}"), m


def _isnum(value: Any) -> bool:
    """True when ``value`` is a real, non-NaN number (pandapower uses NaN for 'unset')."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _dbl(value: Any) -> Literal:
    """xsd:double literal from a numpy/python number."""
    return Literal(float(value), datatype=XSD.double)


class _Builder:
    """Accumulates triples for one network; keeps per-kind IRIs so references resolve."""

    def __init__(self, net: pp.pandapowerNet, model_id: str) -> None:
        self.net = net
        self.model_id = model_id
        self.g = Graph()
        bind_prefixes(self.g)
        self.base_voltage: dict[float, URIRef] = {}
        self.cn: dict[int, URIRef] = {}

    def resource(self, kind: str, index: Any, rdf_type: URIRef, name: Any = None) -> URIRef:
        """Create a typed IdentifiedObject with mRID and name; returns its IRI."""
        iri, m = _iri(self.model_id, kind, index)
        self.g.add((iri, RDF.type, rdf_type))
        self.g.add((iri, CIM["IdentifiedObject.mRID"], Literal(m)))
        label = name if isinstance(name, str) and name else f"{kind}_{index}"
        self.g.add((iri, CIM["IdentifiedObject.name"], Literal(label)))
        return iri

    def add_float(self, s: URIRef, p: URIRef, value: Any) -> None:
        """Add a double property, omitting pandapower NaN ('unset')."""
        if _isnum(value):
            self.g.add((s, p, _dbl(value)))

    def pp_index(self, s: URIRef, index: Any) -> None:
        """Record the pandapower row index (extension; lets the import restore indices)."""
        self.g.add((s, PCX.pandapowerIndex, Literal(int(index), datatype=XSD.integer)))

    def bv(self, vn_kv: float) -> URIRef:
        """Shared ``cim:BaseVoltage`` for a nominal voltage (kV)."""
        key = float(vn_kv)
        if key not in self.base_voltage:
            iri = self.resource("BaseVoltage", repr(key), CIM.BaseVoltage, f"BV_{key:g}kV")
            self.g.add((iri, CIM["BaseVoltage.nominalVoltage"], _dbl(key)))
            self.base_voltage[key] = iri
        return self.base_voltage[key]

    def terminal(self, kind: str, index: Any, seq: int, equipment: URIRef, bus: int,
                 connected: bool) -> URIRef:
        """A ``cim:Terminal`` joining ``equipment`` to the bus's ConnectivityNode."""
        t = self.resource("Terminal", f"{kind}/{index}/{seq}", CIM.Terminal,
                          f"{kind}_{index}_T{seq}")
        self.g.add((t, CIM["Terminal.ConductingEquipment"], equipment))
        self.g.add((t, CIM["Terminal.ConnectivityNode"], self.cn[int(bus)]))
        self.g.add((t, CIM["ACDCTerminal.sequenceNumber"], Literal(seq, datatype=XSD.integer)))
        self.g.add((t, CIM["ACDCTerminal.connected"], Literal(bool(connected))))
        return t

    def equipment_base_voltage(self, eq: URIRef, bus: int) -> None:
        """``ConductingEquipment.BaseVoltage`` from the bus's nominal voltage."""
        self.g.add((eq, CIM["ConductingEquipment.BaseVoltage"],
                    self.bv(self.net.bus.at[int(bus), "vn_kv"])))

    def voltage_control(self, kind: str, index: Any, eq: URIRef, terminal: URIRef,
                        vm_pu: float, bus: int) -> None:
        """A voltage ``cim:RegulatingControl`` at ``terminal`` with target in kV."""
        rc = self.resource("RegulatingControl", f"{kind}/{index}", CIM.RegulatingControl)
        self.g.add((rc, CIM["RegulatingControl.mode"], CIM["RegulatingControlModeKind.voltage"]))
        self.g.add((rc, CIM["RegulatingControl.Terminal"], terminal))
        self.g.add((rc, CIM["RegulatingControl.enabled"], Literal(True)))
        vn_kv = float(self.net.bus.at[int(bus), "vn_kv"])
        self.g.add((rc, CIM["RegulatingControl.targetValue"], _dbl(float(vm_pu) * vn_kv)))
        self.g.add((eq, CIM["RegulatingCondEq.RegulatingControl"], rc))
        self.g.add((eq, CIM["RegulatingCondEq.controlEnabled"], Literal(True)))


def _check_supported(net: pp.pandapowerNet) -> None:
    """Refuse networks with features outside the subset (named, not silently dropped).

    Raises:
        UnsupportedNetworkFeature: a non-empty unsupported table, ZIP loads, a phase-shifting or
            non-ratio tap changer, a tap-dependence table, or a non-30-degree phase shift.
    """
    for table in UNSUPPORTED_TABLES:
        df = net.get(table) if hasattr(net, "get") else getattr(net, table, None)
        if df is not None and hasattr(df, "__len__") and len(df) > 0:
            raise UnsupportedNetworkFeature(f"net.{table} has {len(df)} rows; not in the subset")
    for col in ZIP_COLUMNS:
        if col in net.load.columns and (net.load[col].fillna(0.0) != 0.0).any():
            raise UnsupportedNetworkFeature(f"load.{col} non-zero needs LoadResponseCharacteristic")
    tr = net.trafo
    if len(tr):
        if "tap_step_degree" in tr and (tr["tap_step_degree"].fillna(0.0) != 0.0).any():
            raise UnsupportedNetworkFeature("phase-shifting tap changers are not in the subset")
        if "tap_changer_type" in tr:
            kinds = set(tr.loc[tr["tap_side"].notna(), "tap_changer_type"].dropna()) - {"Ratio"}
            if kinds:
                raise UnsupportedNetworkFeature(f"tap_changer_type {sorted(kinds)} not supported")
        if "tap_dependency_table" in tr and tr["tap_dependency_table"].fillna(False).astype(bool).any():
            raise UnsupportedNetworkFeature("tap dependency tables are not in the subset")
        clock = tr["shift_degree"].fillna(0.0) / PHASE_CLOCK_DEGREES
        if ((clock - clock.round()).abs() > 0.0).any():
            raise UnsupportedNetworkFeature("shift_degree must be a multiple of 30 degrees")
    if "step_dependency_table" in net.shunt and net.shunt["step_dependency_table"].fillna(False).astype(bool).any():
        raise UnsupportedNetworkFeature("shunt step dependency tables are not in the subset")


def export_network(net: pp.pandapowerNet, model_id: str = "pandapower-net",
                   source: str | None = None) -> Graph:
    """Convert a pandapower network into a CGMES-shaped CIM RDF graph.

    Args:
        net: the network; it is read, never modified.
        model_id: stable identity used to derive every mRID (e.g. the PowerMCP network id).
        source: optional provenance string (e.g. the case file's sha256) for the model header.

    Returns:
        An rdflib ``Graph`` (prefixes ``cim``, ``md``, ``pcx`` bound).

    Raises:
        UnsupportedNetworkFeature: see :func:`_check_supported`.
    """
    _check_supported(net)
    b = _Builder(net, model_id)
    g = b.g

    header = b.resource("FullModel", model_id, MD.FullModel, model_id)
    g.add((header, MD["Model.description"],
           Literal("CGMES-shaped subset (wattson.semantic), not a conformant CGMES export")))
    g.add((header, PCX.snMva, _dbl(net.sn_mva)))
    g.add((header, PCX.frequencyHz, _dbl(net.f_hz)))
    if source:
        g.add((header, PCX.source, Literal(source)))

    for idx, row in net.bus.iterrows():
        cn = b.resource("ConnectivityNode", idx, CIM.ConnectivityNode, row.get("name"))
        tn = b.resource("TopologicalNode", idx, CIM.TopologicalNode, row.get("name"))
        b.cn[int(idx)] = cn
        g.add((cn, CIM["ConnectivityNode.TopologicalNode"], tn))
        g.add((tn, CIM["TopologicalNode.BaseVoltage"], b.bv(row["vn_kv"])))
        b.pp_index(cn, idx)
        g.add((cn, PCX.inService, Literal(bool(row["in_service"]))))
        for col, prop in (("min_vm_pu", PCX.minVmPu), ("max_vm_pu", PCX.maxVmPu),
                          ("zone", PCX.zone)):
            if col in row:
                b.add_float(cn, prop, row[col])

    f_hz = float(net.f_hz)
    for idx, row in net.line.iterrows():
        eq = b.resource("ACLineSegment", idx, CIM.ACLineSegment, row.get("name"))
        b.pp_index(eq, idx)
        length = float(row["length_km"])
        g.add((eq, CIM["Conductor.length"], _dbl(length)))
        g.add((eq, CIM["ACLineSegment.r"], _dbl(row["r_ohm_per_km"] * length)))
        g.add((eq, CIM["ACLineSegment.x"], _dbl(row["x_ohm_per_km"] * length)))
        g.add((eq, CIM["ACLineSegment.bch"],
               _dbl(2.0 * math.pi * f_hz * row["c_nf_per_km"] * NF_TO_F * length)))
        g.add((eq, CIM["ACLineSegment.gch"], _dbl(row.get("g_us_per_km", 0.0) * US_TO_S * length)))
        b.equipment_base_voltage(eq, row["from_bus"])
        g.add((eq, PCX.parallel, Literal(int(row["parallel"]), datatype=XSD.integer)))
        g.add((eq, PCX.deratingFactor, _dbl(row["df"])))
        t1 = b.terminal("line", idx, 1, eq, row["from_bus"], row["in_service"])
        b.terminal("line", idx, 2, eq, row["to_bus"], row["in_service"])
        ols = b.resource("OperationalLimitSet", f"line/{idx}", CIM.OperationalLimitSet)
        g.add((ols, CIM["OperationalLimitSet.Terminal"], t1))
        lim = b.resource("CurrentLimit", f"line/{idx}", CIM.CurrentLimit)
        g.add((lim, CIM["OperationalLimit.OperationalLimitSet"], ols))
        g.add((lim, CIM["CurrentLimit.value"], _dbl(row["max_i_ka"] * KA_TO_A)))

    for idx, row in net.trafo.iterrows():
        eq = b.resource("PowerTransformer", idx, CIM.PowerTransformer, row.get("name"))
        b.pp_index(eq, idx)
        g.add((eq, PCX.parallel, Literal(int(row["parallel"]), datatype=XSD.integer)))
        g.add((eq, PCX.deratingFactor, _dbl(row["df"])))
        vn_hv, sn = float(row["vn_hv_kv"]), float(row["sn_mva"])
        z_base = vn_hv * vn_hv / sn
        r = row["vkr_percent"] / PERCENT * z_base
        z = row["vk_percent"] / PERCENT * z_base
        if z < r:
            raise UnsupportedNetworkFeature(f"trafo {idx}: vk_percent < vkr_percent")
        x = math.sqrt(z * z - r * r)
        g_mag = row["pfe_kw"] * KW_TO_MW / (vn_hv * vn_hv)
        y_mag = row["i0_percent"] / PERCENT * sn / (vn_hv * vn_hv)
        if y_mag < g_mag:
            raise UnsupportedNetworkFeature(f"trafo {idx}: i0_percent below the pfe_kw share")
        # Magnetising susceptance stored as a non-negative magnitude; the CGMES sign convention
        # for inductive B-mag is not verified here (every snemSA trafo has a zero branch).
        b_mag = math.sqrt(y_mag * y_mag - g_mag * g_mag)
        ends: dict[str, URIRef] = {}
        for end_no, side, bus_col, vn_col in ((1, "hv", "hv_bus", "vn_hv_kv"),
                                              (2, "lv", "lv_bus", "vn_lv_kv")):
            t = b.terminal("trafo", idx, end_no, eq, row[bus_col], row["in_service"])
            end = b.resource("PowerTransformerEnd", f"{idx}/{end_no}", CIM.PowerTransformerEnd)
            ends[side] = end
            g.add((end, CIM["PowerTransformerEnd.PowerTransformer"], eq))
            g.add((end, CIM["TransformerEnd.Terminal"], t))
            g.add((end, CIM["TransformerEnd.endNumber"], Literal(end_no, datatype=XSD.integer)))
            g.add((end, CIM["TransformerEnd.BaseVoltage"],
                   b.bv(net.bus.at[int(row[bus_col]), "vn_kv"])))
            g.add((end, CIM["PowerTransformerEnd.ratedU"], _dbl(row[vn_col])))
            g.add((end, CIM["PowerTransformerEnd.ratedS"], _dbl(sn)))
            values = (r, x, g_mag, b_mag) if end_no == 1 else (0.0, 0.0, 0.0, 0.0)
            for prop, value in zip(("r", "x", "g", "b"), values):
                g.add((end, CIM[f"PowerTransformerEnd.{prop}"], _dbl(value)))
            clock = 0 if end_no == 1 else int(round(float(row["shift_degree"] or 0.0)
                                                    / PHASE_CLOCK_DEGREES))
            g.add((end, CIM["PowerTransformerEnd.phaseAngleClock"],
                   Literal(clock, datatype=XSD.integer)))
        side = row.get("tap_side")
        if isinstance(side, str) and side in ends:
            tc = b.resource("RatioTapChanger", idx, CIM.RatioTapChanger)
            g.add((tc, CIM["RatioTapChanger.TransformerEnd"], ends[side]))
            g.add((tc, CIM["TapChanger.ltcFlag"], Literal(False)))
            for col, prop in (("tap_pos", "TapChanger.step"),
                              ("tap_neutral", "TapChanger.neutralStep"),
                              ("tap_min", "TapChanger.lowStep"),
                              ("tap_max", "TapChanger.highStep"),
                              ("tap_step_percent", "RatioTapChanger.stepVoltageIncrement")):
                if col in row:
                    b.add_float(tc, CIM[prop], row[col])

    for idx, row in net.gen.iterrows():
        eq = b.resource("SynchronousMachine", idx, CIM.SynchronousMachine, row.get("name"))
        b.pp_index(eq, idx)
        t = b.terminal("gen", idx, 1, eq, row["bus"], row["in_service"])
        b.equipment_base_voltage(eq, row["bus"])
        g.add((eq, CIM["RotatingMachine.p"], _dbl(-float(row["p_mw"]))))
        b.add_float(eq, CIM["RotatingMachine.ratedS"], row.get("sn_mva"))
        b.add_float(eq, CIM["SynchronousMachine.minQ"], row.get("min_q_mvar"))
        b.add_float(eq, CIM["SynchronousMachine.maxQ"], row.get("max_q_mvar"))
        g.add((eq, CIM["SynchronousMachine.referencePriority"],
               Literal(1 if bool(row["slack"]) else 0, datatype=XSD.integer)))
        gu = b.resource("GeneratingUnit", idx, CIM.GeneratingUnit)
        g.add((eq, CIM["RotatingMachine.GeneratingUnit"], gu))
        b.add_float(gu, CIM["GeneratingUnit.minOperatingP"], row.get("min_p_mw"))
        b.add_float(gu, CIM["GeneratingUnit.maxOperatingP"], row.get("max_p_mw"))
        b.voltage_control("gen", idx, eq, t, row["vm_pu"], row["bus"])
        b.add_float(eq, PCX.scaling, row.get("scaling"))
        b.add_float(eq, PCX.slackWeight, row.get("slack_weight"))
        if row.get("controllable") is not None and not (isinstance(row.get("controllable"), float)
                                                        and math.isnan(row["controllable"])):
            g.add((eq, PCX.controllable, Literal(bool(row["controllable"]))))

    for idx, row in net.ext_grid.iterrows():
        eq = b.resource("ExternalNetworkInjection", idx, CIM.ExternalNetworkInjection,
                        row.get("name"))
        b.pp_index(eq, idx)
        t = b.terminal("ext_grid", idx, 1, eq, row["bus"], row["in_service"])
        b.equipment_base_voltage(eq, row["bus"])
        g.add((eq, CIM["ExternalNetworkInjection.referencePriority"],
               Literal(1, datatype=XSD.integer)))
        for col, prop in (("min_p_mw", "minP"), ("max_p_mw", "maxP"),
                          ("min_q_mvar", "minQ"), ("max_q_mvar", "maxQ")):
            if col in row:
                b.add_float(eq, CIM[f"ExternalNetworkInjection.{prop}"], row[col])
        b.voltage_control("ext_grid", idx, eq, t, row["vm_pu"], row["bus"])
        b.add_float(eq, PCX.voltageAngleDegree, row["va_degree"])
        b.add_float(eq, PCX.slackWeight, row.get("slack_weight"))

    for idx, row in net.sgen.iterrows():
        eq = b.resource("EnergySource", idx, CIM.EnergySource, row.get("name"))
        b.pp_index(eq, idx)
        b.terminal("sgen", idx, 1, eq, row["bus"], row["in_service"])
        b.equipment_base_voltage(eq, row["bus"])
        g.add((eq, CIM["EnergySource.activePower"], _dbl(-float(row["p_mw"]))))
        g.add((eq, CIM["EnergySource.reactivePower"], _dbl(-float(row["q_mvar"]))))
        b.add_float(eq, PCX.scaling, row.get("scaling"))

    for idx, row in net.load.iterrows():
        eq = b.resource("EnergyConsumer", idx, CIM.EnergyConsumer, row.get("name"))
        b.pp_index(eq, idx)
        b.terminal("load", idx, 1, eq, row["bus"], row["in_service"])
        b.equipment_base_voltage(eq, row["bus"])
        g.add((eq, CIM["EnergyConsumer.p"], _dbl(row["p_mw"])))
        g.add((eq, CIM["EnergyConsumer.q"], _dbl(row["q_mvar"])))
        b.add_float(eq, PCX.scaling, row.get("scaling"))

    for idx, row in net.shunt.iterrows():
        eq = b.resource("LinearShuntCompensator", idx, CIM.LinearShuntCompensator,
                        row.get("name"))
        b.pp_index(eq, idx)
        b.terminal("shunt", idx, 1, eq, row["bus"], row["in_service"])
        b.equipment_base_voltage(eq, row["bus"])
        vn = float(row["vn_kv"])
        g.add((eq, CIM["ShuntCompensator.nomU"], _dbl(vn)))
        # CIM b is positive for capacitive; pandapower q_mvar is positive when absorbing.
        g.add((eq, CIM["LinearShuntCompensator.bPerSection"], _dbl(-float(row["q_mvar"]) / (vn * vn))))
        g.add((eq, CIM["LinearShuntCompensator.gPerSection"], _dbl(float(row["p_mw"]) / (vn * vn))))
        g.add((eq, CIM["ShuntCompensator.sections"], _dbl(row["step"])))
        g.add((eq, CIM["ShuntCompensator.maximumSections"],
               Literal(int(row["max_step"]), datatype=XSD.integer)))

    return g


class ExactDoubleTurtleSerializer(TurtleSerializer):
    """Turtle serializer that writes ``xsd:double`` literals with their full lexical form.

    Why: rdflib's stock Turtle serializer abbreviates every ``xsd:double`` to
    ``f"{value:e}"`` -- 7 significant digits (rdflib 7.6.0, ``Literal._literal_n3`` with
    ``use_plain=True``). Measured on snem-sa-2000 that loses up to 1.1e-3 in ``vk_percent`` and
    moves bus voltages by 4.3e-7 pu after a round trip, so the stock writer cannot carry a
    power-flow model. This subclass emits ``"<repr>"^^xsd:double`` instead, which parses back to
    the identical IEEE-754 value. It overrides one method of rdflib's public serializer class.
    """

    def label(self, node, position):  # noqa: ANN001, ANN201 -- rdflib signature
        """Full-precision typed literal for doubles; rdflib's default for everything else."""
        if isinstance(node, Literal) and node.datatype == XSD.double:
            return node._literal_n3(use_plain=False,
                                    qname_callback=lambda dt: self.get_pname(dt, False))
        return super().label(node, position)


def to_turtle(graph: Graph) -> str:
    """Serialize ``graph`` to Turtle without losing double precision (see the serializer)."""
    buffer = io.BytesIO()
    ExactDoubleTurtleSerializer(graph).serialize(buffer, encoding="utf-8")
    return buffer.getvalue().decode("utf-8")


def from_turtle(text: str) -> Graph:
    """Parse Turtle text into a new graph."""
    return Graph().parse(data=text, format="turtle")


def graph_stats(graph: Graph) -> GraphStats:
    """Count triples and instances per ``rdf:type`` (prefixed names, e.g. ``cim:Terminal``)."""
    counts: Counter[str] = Counter()
    for _s, _p, o in graph.triples((None, RDF.type, None)):
        counts[graph.namespace_manager.normalizeUri(o)] += 1
    return {"triples": len(graph), "classes": dict(sorted(counts.items()))}


def main(argv: list[str] | None = None) -> int:
    """CLI: export a PowerMCP network id to Turtle and print its graph stats."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--network", default="snem-sa-2000", help="PowerMCP network id")
    parser.add_argument("--out", type=Path, required=True, help="output .ttl path")
    args = parser.parse_args(argv)
    from wattson.inference.powermcp import _load_network_object

    net, input_hash = _load_network_object(args.network)
    graph = export_network(net, model_id=args.network, source=f"sha256:{input_hash}")
    args.out.write_text(to_turtle(graph), encoding="utf-8")
    print(graph_stats(graph))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
