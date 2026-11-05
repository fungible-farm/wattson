"""Convert a CGMES-shaped CIM RDF graph back into a pandapower network (CIM -> pandapower).

The inverse of :func:`wattson.semantic.export.export_network`, for the same pragmatic
CGMES-shaped subset (see that module's docstring for what is and is not represented). Every
electrical parameter is read from its CIM property and converted back to pandapower units:

- ``ACLineSegment.r``/``x``/``bch``/``gch`` (whole segment) / ``Conductor.length`` ->
  ``*_per_km``; ``CurrentLimit.value`` (A) -> ``max_i_ka``.
- ``PowerTransformerEnd`` end-1 ``r``/``x``/``g``/``b`` (HV-referred ohm/S) + ``ratedU``/``ratedS``
  -> ``vkr_percent``/``vk_percent``/``pfe_kw``/``i0_percent``; ``phaseAngleClock`` -> ``shift_degree``.
- ``RegulatingControl.targetValue`` (kV) / bus nominal kV -> ``vm_pu``.
- CGMES load-sign injections (``RotatingMachine.p``, ``EnergySource.*``) are negated back.
- ``ACDCTerminal.connected`` on all of an equipment's terminals -> ``in_service``.

The ``pcx:`` extension fields (pandapower index, ``parallel``, ``df``, ``scaling``, slack weight,
ext-grid angle, bus limits) restore exactly the pandapower-only data CGMES cannot carry; they
are named stand-ins, not CIM. A graph lacking ``pcx:pandapowerIndex`` gets sequential indices.

Unit conversions are exact inverses, so the round trip differs only by floating-point rounding
(the measured bound is asserted in ``test_roundtrip.py``).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Final

import pandapower as pp
from rdflib import RDF, Graph, URIRef

from wattson.semantic.export import KA_TO_A, KW_TO_MW, NF_TO_F, PERCENT, PHASE_CLOCK_DEGREES, US_TO_S
from wattson.semantic.namespaces import CIM, MD, PCX

NAN: Final[float] = float("nan")
"""pandapower's 'unset' marker for optional numeric columns."""


class CimImportError(ValueError):
    """The graph does not carry what the subset needs (run SHACL validation first)."""


def _f(g: Graph, s: URIRef, p: URIRef, default: float = NAN) -> float:
    """Float value of ``s p ?o``, or ``default`` when absent."""
    v = g.value(s, p)
    return default if v is None else float(v.toPython())


def _i(g: Graph, s: URIRef, p: URIRef, default: int | None = None) -> int | None:
    """Integer value of ``s p ?o``, or ``default`` when absent."""
    v = g.value(s, p)
    return default if v is None else int(float(v.toPython()))


def _b(g: Graph, s: URIRef, p: URIRef, default: bool | None = None) -> bool | None:
    """Boolean value of ``s p ?o``, or ``default`` when absent.

    A plain literal (CGMES RDF/XML carries no datatypes, cgmes.py) is read from its lexical
    form: ``bool("false")`` is True in Python, which would silently connect every
    disconnected terminal.

    Raises:
        ValueError: for a lexical form that is not a boolean.
    """
    v = g.value(s, p)
    if v is None:
        return default
    value = v.toPython()
    if isinstance(value, str):
        lexical = value.strip().lower()
        if lexical not in {"true", "false", "1", "0"}:
            raise ValueError(f"{p} is not a boolean: {value!r}")
        return lexical in {"true", "1"}
    return bool(value)


def _name(g: Graph, s: URIRef, kind: str, index: int) -> str | None:
    """IdentifiedObject.name, mapping the exporter's synthesised ``<Class>_<index>`` back to None."""
    v = g.value(s, CIM["IdentifiedObject.name"])
    if v is None or str(v) == f"{kind}_{index}":
        return None
    return str(v)


class _Reader:
    """Indexes terminals and nodes once so per-element lookups are O(1)."""

    def __init__(self, g: Graph) -> None:
        self.g = g
        self.terminals: dict[URIRef, list[tuple[int, URIRef, bool]]] = defaultdict(list)
        for t in g.subjects(RDF.type, CIM.Terminal):
            eq = g.value(t, CIM["Terminal.ConductingEquipment"])
            cn = g.value(t, CIM["Terminal.ConnectivityNode"])
            if eq is None or cn is None:
                raise CimImportError(f"terminal {t} lacks equipment or node")
            seq = _i(g, t, CIM["ACDCTerminal.sequenceNumber"], 1)
            self.terminals[eq].append((seq, cn, bool(_b(g, t, CIM["ACDCTerminal.connected"], True))))
        for eq in self.terminals:
            self.terminals[eq].sort()
        self.bus_of: dict[URIRef, int] = {}
        self.vn_of: dict[URIRef, float] = {}

    def ordered(self, rdf_type: URIRef) -> list[tuple[int, URIRef]]:
        """Instances of ``rdf_type`` as ``(pandapower index, IRI)`` sorted by index."""
        subjects = sorted(self.g.subjects(RDF.type, rdf_type), key=str)
        out = [(_i(self.g, s, PCX.pandapowerIndex, n), s) for n, s in enumerate(subjects)]
        return sorted(out, key=lambda item: item[0])

    def ends(self, eq: URIRef, count: int) -> list[tuple[int, URIRef]]:
        """Terminal ``(bus index, …)`` list for ``eq``; checks the terminal count."""
        terms = self.terminals.get(eq, [])
        if len(terms) != count:
            raise CimImportError(f"{eq} has {len(terms)} terminals, expected {count}")
        return [(self.bus_of[cn], cn) for _seq, cn, _c in terms]

    def in_service(self, eq: URIRef) -> bool:
        """Equipment is in service iff every terminal is connected (SSH switching state)."""
        return all(c for _s, _cn, c in self.terminals.get(eq, []))


def import_network(graph: Graph) -> pp.pandapowerNet:
    """Build a pandapower network from a CGMES-shaped CIM graph.

    Args:
        graph: a graph produced by :func:`export_network` (or one following the same subset).

    Returns:
        A new pandapower network.

    Raises:
        CimImportError: a structural gap (terminal count, missing node); SHACL catches these with
            a clearer report, so validate first.
    """
    g = graph
    # The header holding the network fields. A CGMES profile split (cgmes.py) adds one
    # md:FullModel per profile, so "the first header" could be one without them.
    headers = list(g.subjects(RDF.type, MD.FullModel))
    header = next((h for h in headers if (h, PCX.snMva, None) in g), headers[0] if headers else None)
    sn_mva = _f(g, header, PCX.snMva, 1.0) if header is not None else 1.0
    f_hz = _f(g, header, PCX.frequencyHz, 50.0) if header is not None else 50.0
    net = pp.create_empty_network(sn_mva=sn_mva, f_hz=f_hz)
    rd = _Reader(g)

    for idx, cn in rd.ordered(CIM.ConnectivityNode):
        tn = g.value(cn, CIM["ConnectivityNode.TopologicalNode"])
        bv = g.value(tn, CIM["TopologicalNode.BaseVoltage"]) if tn is not None else None
        if bv is None:
            raise CimImportError(f"node {cn} has no TopologicalNode/BaseVoltage")
        vn = _f(g, bv, CIM["BaseVoltage.nominalVoltage"])
        rd.bus_of[cn] = idx
        rd.vn_of[cn] = vn
        zone = _f(g, cn, PCX.zone)
        pp.create_bus(net, vn_kv=vn, name=_name(g, cn, "ConnectivityNode", idx), index=idx,
                      in_service=bool(_b(g, cn, PCX.inService, True)),
                      min_vm_pu=_f(g, cn, PCX.minVmPu), max_vm_pu=_f(g, cn, PCX.maxVmPu),
                      zone=None if math.isnan(zone) else zone)

    limit_of: dict[URIRef, float] = {}
    for lim in g.subjects(RDF.type, CIM.CurrentLimit):
        ols = g.value(lim, CIM["OperationalLimit.OperationalLimitSet"])
        term = g.value(ols, CIM["OperationalLimitSet.Terminal"]) if ols is not None else None
        eq = g.value(term, CIM["Terminal.ConductingEquipment"]) if term is not None else None
        if eq is not None:
            limit_of[eq] = _f(g, lim, CIM["CurrentLimit.value"])

    for idx, eq in rd.ordered(CIM.ACLineSegment):
        (fb, _), (tb, _) = rd.ends(eq, 2)
        length = _f(g, eq, CIM["Conductor.length"])
        if eq not in limit_of:
            raise CimImportError(f"line {eq} has no CurrentLimit")
        pp.create_line_from_parameters(
            net, from_bus=fb, to_bus=tb, length_km=length,
            r_ohm_per_km=_f(g, eq, CIM["ACLineSegment.r"]) / length,
            x_ohm_per_km=_f(g, eq, CIM["ACLineSegment.x"]) / length,
            c_nf_per_km=_f(g, eq, CIM["ACLineSegment.bch"]) / (2.0 * math.pi * f_hz * NF_TO_F * length),
            g_us_per_km=_f(g, eq, CIM["ACLineSegment.gch"], 0.0) / (US_TO_S * length),
            max_i_ka=limit_of[eq] / KA_TO_A,
            name=_name(g, eq, "ACLineSegment", idx), index=idx, in_service=rd.in_service(eq),
            df=_f(g, eq, PCX.deratingFactor, 1.0), parallel=_i(g, eq, PCX.parallel, 1),
        )

    end_of: dict[URIRef, dict[int, URIRef]] = defaultdict(dict)
    for end in g.subjects(RDF.type, CIM.PowerTransformerEnd):
        pt = g.value(end, CIM["PowerTransformerEnd.PowerTransformer"])
        end_of[pt][_i(g, end, CIM["TransformerEnd.endNumber"])] = end
    tap_of: dict[URIRef, URIRef] = {}
    for tc in g.subjects(RDF.type, CIM.RatioTapChanger):
        tap_of[g.value(tc, CIM["RatioTapChanger.TransformerEnd"])] = tc

    for idx, eq in rd.ordered(CIM.PowerTransformer):
        ends = end_of.get(eq, {})
        if set(ends) != {1, 2}:
            raise CimImportError(f"transformer {eq} needs ends 1 and 2, has {sorted(ends)}")
        e1, e2 = ends[1], ends[2]
        bus = {}
        for no, end in ((1, e1), (2, e2)):
            term = g.value(end, CIM["TransformerEnd.Terminal"])
            bus[no] = rd.bus_of[g.value(term, CIM["Terminal.ConnectivityNode"])]
        vn_hv = _f(g, e1, CIM["PowerTransformerEnd.ratedU"])
        sn = _f(g, e1, CIM["PowerTransformerEnd.ratedS"])
        z_base = vn_hv * vn_hv / sn
        r = _f(g, e1, CIM["PowerTransformerEnd.r"])
        x = _f(g, e1, CIM["PowerTransformerEnd.x"])
        g_mag = _f(g, e1, CIM["PowerTransformerEnd.g"], 0.0)
        b_mag = abs(_f(g, e1, CIM["PowerTransformerEnd.b"], 0.0))
        tap: dict[str, Any] = {"tap_side": None, "tap_changer_type": None}
        for side, end in (("hv", e1), ("lv", e2)):
            tc = tap_of.get(end)
            if tc is not None:
                tap = {
                    "tap_side": side, "tap_changer_type": "Ratio", "tap_step_degree": 0.0,
                    "tap_pos": _f(g, tc, CIM["TapChanger.step"]),
                    "tap_neutral": _f(g, tc, CIM["TapChanger.neutralStep"]),
                    "tap_min": _f(g, tc, CIM["TapChanger.lowStep"]),
                    "tap_max": _f(g, tc, CIM["TapChanger.highStep"]),
                    "tap_step_percent": _f(g, tc, CIM["RatioTapChanger.stepVoltageIncrement"]),
                }
        pp.create_transformer_from_parameters(
            net, hv_bus=bus[1], lv_bus=bus[2], sn_mva=sn, vn_hv_kv=vn_hv,
            vn_lv_kv=_f(g, e2, CIM["PowerTransformerEnd.ratedU"]),
            vkr_percent=r / z_base * PERCENT,
            vk_percent=math.hypot(r, x) / z_base * PERCENT,
            pfe_kw=g_mag * vn_hv * vn_hv / KW_TO_MW,
            i0_percent=math.hypot(g_mag, b_mag) * vn_hv * vn_hv / sn * PERCENT,
            shift_degree=_i(g, e2, CIM["PowerTransformerEnd.phaseAngleClock"], 0) * PHASE_CLOCK_DEGREES,
            name=_name(g, eq, "PowerTransformer", idx), index=idx, in_service=rd.in_service(eq),
            parallel=_i(g, eq, PCX.parallel, 1), df=_f(g, eq, PCX.deratingFactor, 1.0), **tap,
        )

    def target_vm(eq: URIRef, cn: URIRef) -> float:
        """Voltage setpoint in pu from the RegulatingControl target (kV)."""
        rc = g.value(eq, CIM["RegulatingCondEq.RegulatingControl"])
        if rc is None:
            raise CimImportError(f"{eq} has no RegulatingControl")
        return _f(g, rc, CIM["RegulatingControl.targetValue"]) / rd.vn_of[cn]

    for idx, eq in rd.ordered(CIM.SynchronousMachine):
        [(bus, cn)] = rd.ends(eq, 1)
        gu = g.value(eq, CIM["RotatingMachine.GeneratingUnit"])
        controllable = _b(g, eq, PCX.controllable)
        extra: dict[str, Any] = {} if controllable is None else {"controllable": controllable}
        pp.create_gen(
            net, bus=bus, p_mw=-_f(g, eq, CIM["RotatingMachine.p"]), vm_pu=target_vm(eq, cn),
            sn_mva=_f(g, eq, CIM["RotatingMachine.ratedS"]),
            name=_name(g, eq, "SynchronousMachine", idx), index=idx,
            min_q_mvar=_f(g, eq, CIM["SynchronousMachine.minQ"]),
            max_q_mvar=_f(g, eq, CIM["SynchronousMachine.maxQ"]),
            min_p_mw=_f(g, gu, CIM["GeneratingUnit.minOperatingP"]) if gu is not None else NAN,
            max_p_mw=_f(g, gu, CIM["GeneratingUnit.maxOperatingP"]) if gu is not None else NAN,
            scaling=_f(g, eq, PCX.scaling, 1.0),
            slack=_i(g, eq, CIM["SynchronousMachine.referencePriority"], 0) == 1,
            slack_weight=_f(g, eq, PCX.slackWeight, 0.0), in_service=rd.in_service(eq), **extra,
        )

    for idx, eq in rd.ordered(CIM.ExternalNetworkInjection):
        [(bus, cn)] = rd.ends(eq, 1)
        pp.create_ext_grid(
            net, bus=bus, vm_pu=target_vm(eq, cn), va_degree=_f(g, eq, PCX.voltageAngleDegree, 0.0),
            name=_name(g, eq, "ExternalNetworkInjection", idx), index=idx,
            in_service=rd.in_service(eq), slack_weight=_f(g, eq, PCX.slackWeight, 1.0),
            min_p_mw=_f(g, eq, CIM["ExternalNetworkInjection.minP"]),
            max_p_mw=_f(g, eq, CIM["ExternalNetworkInjection.maxP"]),
            min_q_mvar=_f(g, eq, CIM["ExternalNetworkInjection.minQ"]),
            max_q_mvar=_f(g, eq, CIM["ExternalNetworkInjection.maxQ"]),
        )

    for idx, eq in rd.ordered(CIM.EnergySource):
        [(bus, _cn)] = rd.ends(eq, 1)
        pp.create_sgen(
            net, bus=bus, p_mw=-_f(g, eq, CIM["EnergySource.activePower"]),
            q_mvar=-_f(g, eq, CIM["EnergySource.reactivePower"], 0.0),
            name=_name(g, eq, "EnergySource", idx), index=idx,
            scaling=_f(g, eq, PCX.scaling, 1.0), in_service=rd.in_service(eq),
        )

    for idx, eq in rd.ordered(CIM.EnergyConsumer):
        [(bus, _cn)] = rd.ends(eq, 1)
        pp.create_load(
            net, bus=bus, p_mw=_f(g, eq, CIM["EnergyConsumer.p"]),
            q_mvar=_f(g, eq, CIM["EnergyConsumer.q"], 0.0),
            name=_name(g, eq, "EnergyConsumer", idx), index=idx,
            scaling=_f(g, eq, PCX.scaling, 1.0), in_service=rd.in_service(eq),
        )

    for idx, eq in rd.ordered(CIM.LinearShuntCompensator):
        [(bus, _cn)] = rd.ends(eq, 1)
        vn = _f(g, eq, CIM["ShuntCompensator.nomU"])
        step = _f(g, eq, CIM["ShuntCompensator.sections"], 1.0)
        pp.create_shunt(
            net, bus=bus, vn_kv=vn,
            q_mvar=-_f(g, eq, CIM["LinearShuntCompensator.bPerSection"]) * vn * vn,
            p_mw=_f(g, eq, CIM["LinearShuntCompensator.gPerSection"], 0.0) * vn * vn,
            step=int(step) if float(step).is_integer() else step,
            max_step=_i(g, eq, CIM["ShuntCompensator.maximumSections"], 1),
            name=_name(g, eq, "LinearShuntCompensator", idx), index=idx,
            in_service=rd.in_service(eq),
        )

    return net
