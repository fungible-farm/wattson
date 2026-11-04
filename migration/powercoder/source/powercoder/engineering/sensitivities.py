"""PTDF and LODF with conditioning and independent residual checks (PC-REQ-013).

Factors come from pandapower's PYPOWER ports (``makePTDF``, ``makeLODF``) applied to
the in-service MATPOWER-form case. They are checked two independent ways:

- PTDF against a *finite-difference* DC power flow: inject a known power at a bus,
  re-solve with ``pandapower.rundcpp`` (absorbed at the slack), and divide the change in
  every branch flow by the injection. The DC model is linear, so the two must agree to
  round-off -- any larger gap is a modelling or indexing error.
- LODF against an *actual DC outage re-solve*: take the branch out of service, re-solve,
  and compare every other branch's flow change with ``LODF[:, k] * flow_k``.

Assumptions (stated because a sensitivity is meaningless without them): lossless DC
model, flat 1 pu voltages, single slack (the case's reference bus) absorbing every
injection, branch "from" side flows (``p_from_mw`` for lines, ``p_hv_mw`` for trafos).
A bridge branch (one whose outage islands part of the network) has no LODF: its PTDF
self-sensitivity is 1 and ``makeLODF`` divides by ``1 - 1``. Those branches are listed
and their LODF columns set to NaN rather than returned as infinities.

Licences: pandapower BSD-3-Clause; numpy/scipy BSD-3-Clause.
"""

from __future__ import annotations

import copy
import math
from typing import TypedDict

import numpy as np
import pandapower as pp
from pandapower.pypower.idx_brch import F_BUS, T_BUS
from pandapower.pypower.idx_bus import BUS_I, BUS_TYPE, NONE
from pandapower.pypower.makeBdc import makeBdc
from pandapower.pypower.makeLODF import makeLODF
from pandapower.pypower.makePTDF import makePTDF

from powercoder.engineering.dc_model import DcCase, UnsupportedModel, build_dc_case
from powercoder.engineering.proof import NumericalProof, make_proof

# Injection used for the finite-difference PTDF check. The DC model is linear, so the
# value only sets the round-off scale: 10 MW is ~1/20 of the largest snem-sa-2000
# branch flow (273 MW measured), large enough to sit far above float64 noise in MW flows.
FD_INJECTION_MW: float = 10.0
# Agreement bounds, declared before measuring. A direct LU solve is accurate to about
# cond(B) * eps; with eps = 2.2e-16 and cond up to ~1e8 (checked and reported), that is
# ~2e-8 in PTDF (MW/MW). 1e-6 leaves margin without hiding an indexing error (which is
# O(0.1-1)). For LODF the comparison is in MW of post-outage flow: 1e-3 MW, the same
# scale as the DC OPF dispatch tolerance.
PTDF_ABS_TOLERANCE: float = 1e-6
LODF_FLOW_ABS_TOLERANCE_MW: float = 1e-3
# A branch is a bridge when its PTDF self-sensitivity 1 - h_kk is below this. In exact
# arithmetic it is 0 for a bridge and >0 otherwise; 1e-8 is ~100x the PTDF error bound
# above at cond 1e8 times eps, so round-off cannot flip the classification silently.
BRIDGE_TOLERANCE: float = 1e-8
# How many buses / branches the default cross-checks probe ("several elements", backlog
# 0016). Six of each keeps the snem-sa-2000 check under a second (one DC re-solve
# measured at ~5 ms) while covering lines and transformers.
DEFAULT_PROBES: int = 6


class Conditioning(TypedDict):
    """Conditioning of the reduced DC susceptance matrix the factors are solved from."""

    reduced_bbus_condition_number: float
    min_branch_susceptance_pu: float
    max_branch_susceptance_pu: float
    bridge_branches: int


class SensitivityFactors(TypedDict):
    """PTDF (branches x buses) and LODF (branches x branches) with their indexing."""

    bus_ids: list[int]
    branch_ids: list[tuple[str, int]]
    slack_bus: int
    ptdf: np.ndarray
    lodf: np.ndarray
    bridge_branch_ids: list[tuple[str, int]]
    conditioning: Conditioning
    assumptions: list[str]
    proof: NumericalProof


class ProbeError(TypedDict):
    """Largest disagreement found for one probed element."""

    element: str  # "bus 1635" or "line 12"
    max_abs_error: float
    unit: str


class CrossCheckReport(TypedDict):
    """Independent re-solve check of PTDF or LODF on several elements."""

    method: str
    probes: list[ProbeError]
    max_abs_error: float
    tolerance: float
    within_tolerance: bool
    skipped: list[str]


ASSUMPTIONS: list[str] = [
    "lossless DC power flow, flat 1 pu voltage magnitudes",
    "single slack: the case reference bus absorbs every injection",
    "flows are branch from-side active power (line p_from_mw, trafo p_hv_mw)",
    "bridge branches have no LODF (their outage islands buses); their columns are NaN",
]


def _sub_case_arrays(case: DcCase) -> tuple[np.ndarray, np.ndarray]:
    """Consecutively renumbered in-service bus and branch arrays for PYPOWER routines."""
    bus = np.real(case.ppc["bus"]).copy()
    in_bus = np.flatnonzero(bus[:, BUS_TYPE] != NONE)
    sub_bus = bus[in_bus].copy()
    sub_bus[:, BUS_I] = np.arange(len(in_bus))
    sub_branch = np.real(case.ppc["branch"])[case.ppc_branch_rows].copy()
    sub_branch[:, F_BUS] = case.from_rows
    sub_branch[:, T_BUS] = case.to_rows
    return sub_bus, sub_branch


def compute_factors(net: pp.pandapowerNet) -> SensitivityFactors:
    """PTDF and LODF of ``net`` (not modified) with conditioning and residuals.

    Raises:
        UnsupportedModel: when the case has no or several reference buses (a single
            slack is an assumption of the factors) or an element the model omits.
    """
    case = build_dc_case(net, with_costs=False)
    if len(case.ref_rows) != 1:
        raise UnsupportedModel(f"PTDF needs exactly one reference bus, found {len(case.ref_rows)}")
    slack = case.ref_rows[0]
    bus, branch = _sub_case_arrays(case)
    ptdf = makePTDF(case.base_mva, bus, branch, slack=slack)
    with np.errstate(divide="ignore", invalid="ignore"):
        lodf = makeLODF(branch, ptdf)
    nb, nl = len(case.bus_ids), len(case.branch_ids)
    self_sensitivity = ptdf[np.arange(nl), case.from_rows] - ptdf[np.arange(nl), case.to_rows]
    bridges = np.flatnonzero(np.abs(1.0 - self_sensitivity) < BRIDGE_TOLERANCE)
    lodf[:, bridges] = np.nan

    # Defining-equation residual: H[:, noslack] @ Bbus[noslack, noref] = Bf[:, noref],
    # with pandapower's own makeBdc, and the independent Bbus against makeBdc's.
    bbus_pp, bf_pp, *_ = makeBdc(bus, branch)
    bbus_pp, bf_pp = np.real(bbus_pp.toarray()), np.real(bf_pp.toarray())
    noslack = np.flatnonzero(np.arange(nb) != slack)
    noref = np.arange(1, nb)
    reduced = bbus_pp[np.ix_(noslack, noref)]
    ptdf_residual = float(np.max(np.abs(ptdf[:, noslack] @ reduced - bf_pp[:, noref]))) if nl else 0.0
    model_residual = float(np.max(np.abs(case.bbus.toarray() - bbus_pp))) if nb else 0.0
    cond = float(np.linalg.cond(bbus_pp[np.ix_(noslack, noslack)]))
    finite_lodf = lodf[np.isfinite(lodf)]
    conditioning = Conditioning(
        reduced_bbus_condition_number=cond,
        min_branch_susceptance_pu=float(np.min(np.abs(case.b_pu))) if nl else math.nan,
        max_branch_susceptance_pu=float(np.max(np.abs(case.b_pu))) if nl else math.nan,
        bridge_branches=int(len(bridges)),
    )
    warnings: list[str] = []
    if len(bridges):
        warnings.append(f"{len(bridges)} bridge branches have no LODF (outage islands buses)")
    if finite_lodf.size and np.max(np.abs(finite_lodf)) > 1.0 / BRIDGE_TOLERANCE:
        warnings.append("LODF entries exceed 1/BRIDGE_TOLERANCE: near-bridge branches amplify round-off")
    proof = make_proof(
        converged=True, feasibility_status="feasible",
        solver_name="pandapower.pypower makePTDF/makeLODF (dense LU)", solver_distribution="pandapower",
        equation_residuals={"ptdf_defining_equation_pu": ptdf_residual, "independent_bbus_vs_makeBdc_pu": model_residual},
        conditioning={k: float(v) for k, v in conditioning.items()},  # type: ignore[misc]
        validation_warnings=warnings,
    )
    return SensitivityFactors(
        bus_ids=case.bus_ids, branch_ids=case.branch_ids, slack_bus=case.bus_ids[slack], ptdf=ptdf, lodf=lodf,
        bridge_branch_ids=[case.branch_ids[k] for k in bridges], conditioning=conditioning,
        assumptions=list(ASSUMPTIONS), proof=proof,
    )


def _branch_flows(net: pp.pandapowerNet, branch_ids: list[tuple[str, int]]) -> np.ndarray:
    """From-side DC flows (MW) of ``branch_ids`` after ``rundcpp``."""
    return np.array([float(net[f"res_{t}"].at[i, "p_from_mw" if t == "line" else "p_hv_mw"]) for t, i in branch_ids])


def cross_check_ptdf(net: pp.pandapowerNet, factors: SensitivityFactors, buses: list[int] | None = None) -> CrossCheckReport:
    """Compare PTDF columns with finite-difference DC re-solves at ``buses``.

    Default probes: ``DEFAULT_PROBES`` non-slack buses evenly spaced in model order.
    """
    work = copy.deepcopy(net)
    pp.rundcpp(work)
    base = _branch_flows(work, factors["branch_ids"])
    row_of = {b: r for r, b in enumerate(factors["bus_ids"])}
    if buses is None:
        candidates = [b for b in factors["bus_ids"] if b != factors["slack_bus"]]
        picks = np.unique(np.linspace(0, len(candidates) - 1, min(DEFAULT_PROBES, len(candidates))).astype(int))
        buses = [candidates[i] for i in picks]
    probes: list[ProbeError] = []
    skipped: list[str] = []
    for bus in buses:
        if bus not in row_of or bus == factors["slack_bus"]:
            skipped.append(f"bus {bus}: not a non-slack model bus")
            continue
        sgen = pp.create_sgen(work, bus, p_mw=FD_INJECTION_MW, q_mvar=0.0, name="fd-probe")
        try:
            pp.rundcpp(work)
            numeric = (_branch_flows(work, factors["branch_ids"]) - base) / FD_INJECTION_MW
        finally:
            work.sgen.drop(index=sgen, inplace=True)
        error = float(np.max(np.abs(numeric - factors["ptdf"][:, row_of[bus]])))
        probes.append(ProbeError(element=f"bus {bus}", max_abs_error=error, unit="MW/MW"))
    worst = max((p["max_abs_error"] for p in probes), default=math.nan)
    return CrossCheckReport(method="finite-difference DC injection re-solve (pandapower.rundcpp)", probes=probes,
                            max_abs_error=worst, tolerance=PTDF_ABS_TOLERANCE,
                            within_tolerance=bool(probes) and worst <= PTDF_ABS_TOLERANCE, skipped=skipped)


def cross_check_lodf(net: pp.pandapowerNet, factors: SensitivityFactors,
                     outages: list[tuple[str, int]] | None = None) -> CrossCheckReport:
    """Compare LODF columns with actual DC outage re-solves of ``outages``.

    Default probes: ``DEFAULT_PROBES`` non-bridge branches, half lines and half
    transformers, each the largest by base flow magnitude (the outages whose
    redistribution is largest, so the check has the most signal). Bridges are skipped
    and named: they have no LODF by construction.
    """
    work = copy.deepcopy(net)
    pp.rundcpp(work)
    branch_ids = factors["branch_ids"]
    base = _branch_flows(work, branch_ids)
    column = {b: k for k, b in enumerate(branch_ids)}
    bridges = set(factors["bridge_branch_ids"])
    if outages is None:
        order = np.argsort(-np.abs(base), kind="stable")
        ranked = [branch_ids[k] for k in order if branch_ids[k] not in bridges]
        # Half lines, half transformers when both exist, so both branch models are probed.
        per_table = max(1, DEFAULT_PROBES // 2)
        outages = [b for b in ranked if b[0] == "line"][:per_table] + [b for b in ranked if b[0] == "trafo"][:per_table]
        outages += [b for b in ranked if b not in outages][:max(0, DEFAULT_PROBES - len(outages))]
    probes: list[ProbeError] = []
    skipped: list[str] = []
    for outage in outages:
        table, idx = outage
        if outage not in column:
            skipped.append(f"{table} {idx}: not an in-service model branch")
            continue
        if outage in bridges:
            skipped.append(f"{table} {idx}: bridge branch, no LODF")
            continue
        k = column[outage]
        work[table].at[idx, "in_service"] = False
        try:
            pp.rundcpp(work)
            post = _branch_flows(work, branch_ids)
        finally:
            work[table].at[idx, "in_service"] = True
        predicted = base + factors["lodf"][:, k] * base[k]
        others = np.arange(len(branch_ids)) != k
        error = float(np.nanmax(np.abs(post[others] - predicted[others])))
        probes.append(ProbeError(element=f"{table} {idx}", max_abs_error=error, unit="MW"))
    worst = max((p["max_abs_error"] for p in probes), default=math.nan)
    return CrossCheckReport(method="actual DC outage re-solve (pandapower.rundcpp)", probes=probes,
                            max_abs_error=worst, tolerance=LODF_FLOW_ABS_TOLERANCE_MW,
                            within_tolerance=bool(probes) and worst <= LODF_FLOW_ABS_TOLERANCE_MW, skipped=skipped)


def bridge_outage_islands(net: pp.pandapowerNet, bridge: tuple[str, int]) -> list[int]:
    """In-service buses left without a DC solution (NaN angle) when ``bridge`` is out.

    The independent confirmation that a branch classified as a bridge really islands
    buses -- the DC analogue of the FM-25 isolated-bus case.
    """
    work = copy.deepcopy(net)
    table, idx = bridge
    work[table].at[idx, "in_service"] = False
    pp.rundcpp(work)
    va = work.res_bus.va_degree
    return [int(b) for b in work.bus.index[work.bus.in_service & ~np.isfinite(va.astype(float))]]
