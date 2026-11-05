"""N-1 branch-outage screening with coverage labels and base-relative violations (PC-REQ-012).

Two physics models, used for what each can establish:

- **DC** (``pandapower.rundcpp``): fast, linear, active power only. It can enumerate
  every branch outage, find islanding and redistribute flows, but it has no voltages,
  so it can never clear a contingency of voltage violations.
- **AC** (``pandapower.runpp``, Newton-Raphson, via ``powermcp._runpp_counted`` so the
  iteration count is the solver's own): voltages and thermal loading. Each solved AC
  contingency is *validated* by an independent Kirchhoff residual recomputed from the
  result tables (bus element injections plus branch end flows must sum to zero), so a
  solver flag alone never stands as proof (PC-REQ-016).

Coverage labels (PC-REQ-012), one per contingency in the declared universe:

- ``validated``: AC-solved and its Kirchhoff residual is within tolerance.
- ``exhaustive``: examined as part of a complete enumeration (DC over the whole universe,
  or AC over the whole universe when its AC result could not be validated), not validated.
- ``heuristic``: examined only because a selection rule picked it (AC top-k by DC
  severity, or an explicit candidate list) and not validated.
- ``unexamined``: in the universe, not examined by any model.

A limited search is never reported as complete: ``screening_claim`` states the model,
the count examined and how the AC set was chosen.

Violations are judged *relative to the base case*. snem-sa-2000 already has 39
bus-voltage band violations intact (AGENTS.md FM-17), so a contingency is only charged
with violations it introduces (``new``) or materially worsens (``worsened``).

Isolated buses: an outage can de-energize buses, which pandapower returns as NaN. As in
``powercoder/inference/studies.py`` (AGENTS.md FM-25), those buses are skipped in the
violation checks and listed, together with the load they carried. A NaN voltage on the
intact network is still a solver failure.

Licences: pandapower BSD-3-Clause; numpy BSD-3-Clause.
"""

from __future__ import annotations

import copy
import math
from typing import Literal, TypedDict

import numpy as np
import pandapower as pp

from wattson.engineering.proof import ConstraintViolation, NumericalProof, make_proof
from wattson.inference import powermcp as pm

# Same voltage band as run_power_flow / run_study_trial, so "39 base violations" means
# the same thing here as in FM-17.
VOLTAGE_LIMIT_LOW_PU: float = pm.VOLTAGE_LIMIT_LOW_PU
VOLTAGE_LIMIT_HIGH_PU: float = pm.VOLTAGE_LIMIT_HIGH_PU
# Nameplate loading screen (percent of max_i_ka / sn_mva), as in studies.py.
THERMAL_LIMIT_PERCENT: float = 100.0
# Newton-Raphson budget per contingency; studies.MAX_ITERATIONS uses the same cap.
MAX_ITERATIONS: int = 100
# Independent Kirchhoff residual bound (MW and Mvar). pandapower's NR stops at
# tolerance_mva = 1e-8 (runpp default) in per-unit-scaled mismatch; result-table sums of
# up to ~10 terms in MW at bus loads of O(100) MW lose a few digits, so 1e-3 MVA is a
# bound a correct solution meets with orders to spare, and a wrong one (O(1) MW) fails.
KCL_RESIDUAL_TOLERANCE_MVA: float = 1e-3
# A base-case violation counts as "worsened" when its excursion beyond the band grows by
# more than this. A declared screening threshold (0.001 pu = 0.1 % of nominal voltage,
# ~ the voltage effect of a few MW on these 66-275 kV buses), not a planning standard;
# worsened violations are reported separately from new ones, never merged into them.
VOLTAGE_WORSENING_TOLERANCE_PU: float = 1e-3
THERMAL_WORSENING_TOLERANCE_PERCENT: float = 1.0
# The CSIRO snemSA.m file writes rateA = rateB = rateC = 10000 MVA on all 698 branches
# (checked by reading mpc.branch columns 6-8); 10000 is the conventional MATPOWER
# "no limit" sentinel, not a rating. Any rating at or above this is flagged as a
# placeholder, so a thermal screen against it is reported as uninformative.
PLACEHOLDER_RATING_MVA: float = 9999.0
# How many de-energized bus ids an outcome lists (the count is always given); matches
# studies.DE_ENERGIZED_IDS_SHOWN.
DE_ENERGIZED_IDS_SHOWN: int = 10

CoverageLabel = Literal["exhaustive", "heuristic", "validated", "unexamined"]
COVERAGE_LABELS: tuple[CoverageLabel, ...] = ("exhaustive", "heuristic", "validated", "unexamined")
Branch = tuple[str, int]


class DcOutcome(TypedDict):
    """DC re-solve of one outage."""

    status: str  # "solved" or "error"
    islanded_buses: int
    islanded_load_mw: float
    max_loading_percent: float | None
    max_flow_change_mw: float | None
    new_thermal_violations: int
    error: str | None


class AcOutcome(TypedDict):
    """AC re-solve of one outage, judged against the AC base case."""

    status: str  # "solved", "nonconvergent" or "error"
    iterations: int | None
    de_energized_buses: int
    de_energized_bus_ids: list[int]
    de_energized_load_mw: float
    # Generation stranded in de-energized islands. Non-zero means pandapower blacked out an
    # island that has its own generators because it holds no slack (single-slack model),
    # so the de-energized load is an upper bound, not a predicted loss of supply.
    de_energized_generation_mw: float
    new_violations: list[ConstraintViolation]
    worsened_violations: list[ConstraintViolation]
    base_violations_cleared: int
    min_vm_pu: float | None
    max_vm_pu: float | None
    max_loading_percent: float | None
    kcl_residual_mva: float | None
    error: str | None


class Contingency(TypedDict):
    """One branch outage in the screening universe."""

    element_type: str
    element_id: int
    coverage: CoverageLabel
    dc: DcOutcome | None
    ac: AcOutcome | None
    severity_rank: int


class BaseCase(TypedDict):
    """The intact network every contingency is compared with."""

    ac_converged: bool
    ac_iterations: int | None
    voltage_violations: int
    thermal_violations: int
    violations: list[ConstraintViolation]
    max_loading_percent: float | None
    kcl_residual_mva: float | None
    placeholder_ratings: int
    rated_branches: int


class ScreeningResult(TypedDict):
    """N-1 screening result: ranked contingencies, coverage and proof."""

    universe: str
    universe_size: int
    base: BaseCase
    contingencies: list[Contingency]
    coverage_counts: dict[str, int]
    dc_complete: bool
    ac_complete: bool
    ac_selection: str
    screening_claim: str
    contingencies_with_new_violations: int
    ac_nonconvergent: int
    islanding_contingencies: int
    proof: NumericalProof


def branch_universe(net: pp.pandapowerNet) -> list[Branch]:
    """Every in-service line and two-winding transformer: the N-1 universe screened here.

    Generator, load and three-winding transformer outages are outside it, and the
    universe string in the result says so.
    """
    return [("line", int(i)) for i in net.line.index[net.line.in_service]] + \
           [("trafo", int(i)) for i in net.trafo.index[net.trafo.in_service]]


def _energized_mask(net: pp.pandapowerNet, column: str) -> np.ndarray:
    """In-service buses with a finite result in ``res_bus[column]``."""
    values = net.res_bus[column].reindex(net.bus.index).astype(float).to_numpy()
    return net.bus.in_service.to_numpy() & np.isfinite(values)


def _generation_at(net: pp.pandapowerNet, buses: list[int]) -> float:
    """In-service generator set points (MW) at ``buses``."""
    if not buses or not len(net.gen):
        return 0.0
    mask = net.gen.in_service & net.gen.bus.isin(buses)
    return float((net.gen.p_mw[mask] * net.gen.scaling[mask]).sum())


def _load_at(net: pp.pandapowerNet, buses: list[int]) -> float:
    """In-service load (MW, nominal) at ``buses``."""
    if not buses or not len(net.load):
        return 0.0
    mask = net.load.in_service & net.load.bus.isin(buses)
    return float((net.load.p_mw[mask] * net.load.scaling[mask]).sum())


def kcl_residual_mva(net: pp.pandapowerNet) -> float:
    """Max nodal |P| and |Q| imbalance (MW / Mvar) of an AC result, from result tables.

    For every energized bus, the net element withdrawal ``res_bus.p_mw / q_mvar`` plus
    the power leaving through every connected branch end must be zero. Branch end
    flows come from the branch models, bus totals from the element results, so the sum
    is a Kirchhoff check that does not reuse the solver's own mismatch vector.
    """
    p = net.res_bus.p_mw.reindex(net.bus.index).astype(float).copy()
    q = net.res_bus.q_mvar.reindex(net.bus.index).astype(float).copy()
    for table, ends in (("line", (("from_bus", "p_from_mw", "q_from_mvar"), ("to_bus", "p_to_mw", "q_to_mvar"))),
                        ("trafo", (("hv_bus", "p_hv_mw", "q_hv_mvar"), ("lv_bus", "p_lv_mw", "q_lv_mvar")))):
        if not len(net[table]):
            continue
        res = net[f"res_{table}"]
        live = net[table].in_service
        for bus_col, p_col, q_col in ends:
            buses = net[table].loc[live, bus_col]
            p = p.add(res.loc[live.index[live], p_col].groupby(buses.values).sum(), fill_value=0.0)
            q = q.add(res.loc[live.index[live], q_col].groupby(buses.values).sum(), fill_value=0.0)
    energized = _energized_mask(net, "vm_pu")
    p, q = p.to_numpy()[energized], q.to_numpy()[energized]
    if not len(p):
        return math.nan
    return float(max(np.nanmax(np.abs(p)), np.nanmax(np.abs(q))))


def _ac_violations(net: pp.pandapowerNet) -> dict[tuple[str, int, str], ConstraintViolation]:
    """Voltage-band and nameplate-loading violations of an AC result, keyed by element/kind.

    Recomputed from raw ``vm_pu`` / ``loading_percent``; de-energized (NaN) elements skipped.
    """
    out: dict[tuple[str, int, str], ConstraintViolation] = {}
    vm = net.res_bus.vm_pu.reindex(net.bus.index).astype(float)
    for bus, value in vm.items():
        if not net.bus.at[bus, "in_service"] or not math.isfinite(value):
            continue
        if value < VOLTAGE_LIMIT_LOW_PU:
            out[("bus", int(bus), "below_min")] = ConstraintViolation(element_type="bus", element_id=int(bus), quantity="vm_pu",
                                                                      value=float(value), limit=VOLTAGE_LIMIT_LOW_PU, kind="below_min")
        elif value > VOLTAGE_LIMIT_HIGH_PU:
            out[("bus", int(bus), "above_max")] = ConstraintViolation(element_type="bus", element_id=int(bus), quantity="vm_pu",
                                                                      value=float(value), limit=VOLTAGE_LIMIT_HIGH_PU, kind="above_max")
    for table in ("line", "trafo"):
        if not len(net[table]):
            continue
        loading = net[f"res_{table}"].loading_percent.astype(float)
        for idx, value in loading.items():
            if net[table].at[idx, "in_service"] and math.isfinite(value) and value > THERMAL_LIMIT_PERCENT:
                out[(table, int(idx), "above_max")] = ConstraintViolation(element_type=table, element_id=int(idx), quantity="loading_percent",
                                                                          value=float(value), limit=THERMAL_LIMIT_PERCENT, kind="above_max")
    return out


def _excursion(v: ConstraintViolation) -> float:
    """How far beyond its limit a violation is, in its own unit."""
    return abs(v["value"] - v["limit"])


def _max_loading(net: pp.pandapowerNet) -> float | None:
    """Largest finite line/trafo loading percent of the last result."""
    values = [net[f"res_{t}"].loading_percent.astype(float).to_numpy() for t in ("line", "trafo") if len(net[t])]
    finite = np.concatenate(values)[np.isfinite(np.concatenate(values))] if values else np.empty(0)
    return float(finite.max()) if finite.size else None


def _branch_flows_dc(net: pp.pandapowerNet, branches: list[Branch]) -> np.ndarray:
    """From-side DC flows (MW); NaN for de-energized or out-of-service branches."""
    out = np.empty(len(branches))
    for k, (table, idx) in enumerate(branches):
        out[k] = float(net[f"res_{table}"].at[idx, "p_from_mw" if table == "line" else "p_hv_mw"])
    return out


def _rated_mva(net: pp.pandapowerNet) -> np.ndarray:
    """Nameplate rating (MVA) of every in-service line and trafo."""
    line = net.line[net.line.in_service]
    vn = net.bus.vn_kv.reindex(line.from_bus).to_numpy()
    line_mva = math.sqrt(3.0) * line.max_i_ka.to_numpy() * vn * line.parallel.to_numpy()
    trafo_mva = net.trafo.sn_mva[net.trafo.in_service].to_numpy() * net.trafo.parallel[net.trafo.in_service].to_numpy()
    return np.r_[line_mva, trafo_mva]


def _set_outage(net: pp.pandapowerNet, branch: Branch, out: bool) -> None:
    """Take ``branch`` out of (or back into) service."""
    table, idx = branch
    net[table].at[idx, "in_service"] = not out


def _solve_dc(net: pp.pandapowerNet, branch: Branch, universe: list[Branch], base_flows: np.ndarray,
              base_thermal: set[Branch]) -> DcOutcome:
    """DC re-solve of one outage on a working copy (restored afterwards)."""
    _set_outage(net, branch, True)
    try:
        pp.rundcpp(net)
        energized = _energized_mask(net, "va_degree")
        islanded = [int(b) for b in net.bus.index[net.bus.in_service.to_numpy() & ~energized]]
        flows = _branch_flows_dc(net, universe)
        change = np.abs(flows - base_flows)
        change[[k for k, b in enumerate(universe) if b == branch]] = np.nan
        new_thermal = 0
        for table in ("line", "trafo"):
            loading = net[f"res_{table}"].loading_percent.astype(float)
            for idx, value in loading.items():
                if (table, int(idx)) != branch and math.isfinite(value) and value > THERMAL_LIMIT_PERCENT \
                        and (table, int(idx)) not in base_thermal:
                    new_thermal += 1
        finite = change[np.isfinite(change)]
        return DcOutcome(status="solved", islanded_buses=len(islanded), islanded_load_mw=_load_at(net, islanded),
                         max_loading_percent=_max_loading(net), max_flow_change_mw=float(finite.max()) if finite.size else 0.0,
                         new_thermal_violations=new_thermal, error=None)
    except Exception as error:  # noqa: BLE001 -- a failed re-solve is an outcome, never a pass
        return DcOutcome(status="error", islanded_buses=0, islanded_load_mw=0.0, max_loading_percent=None,
                         max_flow_change_mw=None, new_thermal_violations=0, error=f"{type(error).__name__}: {error}")
    finally:
        _set_outage(net, branch, False)


def _solve_ac(net: pp.pandapowerNet, branch: Branch,
              base_violations: dict[tuple[str, int, str], ConstraintViolation]) -> AcOutcome:
    """AC re-solve of one outage on a working copy (restored afterwards)."""
    _set_outage(net, branch, True)
    try:
        iterations = pm._runpp_counted(net, numba=True, max_iteration=MAX_ITERATIONS)
        energized = _energized_mask(net, "vm_pu")
        de_energized = [int(b) for b in net.bus.index[net.bus.in_service.to_numpy() & ~energized]]
        post = _ac_violations(net)
        new = [v for key, v in post.items() if key not in base_violations]
        worsened = []
        for key, v in post.items():
            if key in base_violations:
                margin = VOLTAGE_WORSENING_TOLERANCE_PU if v["quantity"] == "vm_pu" else THERMAL_WORSENING_TOLERANCE_PERCENT
                if _excursion(v) > _excursion(base_violations[key]) + margin:
                    worsened.append(v)
        de_set = set(de_energized)
        cleared = sum(1 for key in base_violations if key not in post and not (key[0] == "bus" and key[1] in de_set))
        vm = net.res_bus.vm_pu.astype(float)
        finite = vm[np.isfinite(vm)]
        return AcOutcome(status="solved", iterations=iterations, de_energized_buses=len(de_energized),
                         de_energized_bus_ids=de_energized[:DE_ENERGIZED_IDS_SHOWN], de_energized_load_mw=_load_at(net, de_energized),
                         de_energized_generation_mw=_generation_at(net, de_energized),
                         new_violations=sorted(new, key=_excursion, reverse=True),
                         worsened_violations=sorted(worsened, key=_excursion, reverse=True),
                         base_violations_cleared=cleared,
                         min_vm_pu=float(finite.min()) if len(finite) else None, max_vm_pu=float(finite.max()) if len(finite) else None,
                         max_loading_percent=_max_loading(net), kcl_residual_mva=kcl_residual_mva(net), error=None)
    except pp.LoadflowNotConverged as error:
        return _ac_failure("nonconvergent", f"LoadflowNotConverged: {error}")
    except Exception as error:  # noqa: BLE001
        return _ac_failure("error", f"{type(error).__name__}: {error}")
    finally:
        _set_outage(net, branch, False)


def _ac_failure(status: str, message: str) -> AcOutcome:
    """An AC outcome with no solution: no voltages, no violations, no residual."""
    return AcOutcome(status=status, iterations=None, de_energized_buses=0, de_energized_bus_ids=[], de_energized_load_mw=0.0,
                     de_energized_generation_mw=0.0,
                     new_violations=[], worsened_violations=[], base_violations_cleared=0, min_vm_pu=None, max_vm_pu=None,
                     max_loading_percent=None, kcl_residual_mva=None, error=message)


def _dc_severity_key(outcome: DcOutcome | None) -> tuple[float, ...]:
    """Larger is worse: errors, islanded load, new thermal, max loading, flow change."""
    if outcome is None:
        return (0.0,) * 5
    if outcome["status"] != "solved":
        return (1.0, math.inf, 0.0, 0.0, 0.0)
    return (0.0, outcome["islanded_load_mw"], float(outcome["new_thermal_violations"]),
            outcome["max_loading_percent"] or 0.0, outcome["max_flow_change_mw"] or 0.0)


def _severity_key(c: Contingency) -> tuple[float, ...]:
    """Defined severity order, most severe first when sorted ascending.

    Tier 0: AC non-convergent or errored (no operating point exists or was found).
    Tier 1: AC solved, by de-energized load, then new violations, then the worst new
            excursion, then worsened violations.
    Tier 2: DC only, by ``_dc_severity_key``. Tier 3: unexamined.
    AC outcomes outrank DC ones because only AC can see voltage violations.
    """
    ac, dc = c["ac"], c["dc"]
    if ac is not None and ac["status"] != "solved":
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    if ac is not None:
        worst = max((_excursion(v) for v in ac["new_violations"]), default=0.0)
        return (1.0, -ac["de_energized_load_mw"], -float(len(ac["new_violations"])), -worst,
                -float(len(ac["worsened_violations"])), 0.0)
    if dc is not None:
        return (2.0, *(-value for value in _dc_severity_key(dc)))
    return (3.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def screen_n1(net: pp.pandapowerNet, *, dc: bool = True, dc_elements: list[Branch] | None = None,
              ac_selection: Literal["all", "none"] | int | list[Branch] = "all") -> ScreeningResult:
    """Screen single branch outages of ``net`` (not modified).

    Args:
        net: pandapower network; the intact case must solve in AC.
        dc: run the DC screen.
        dc_elements: restrict the DC screen to these branches (default: the whole
            universe). A restriction leaves the rest ``unexamined`` and the DC screen incomplete.
        ac_selection: ``"all"`` (AC on every branch -- exhaustive), ``"none"``, an
            integer k (AC on the k most severe DC contingencies -- heuristic, needs ``dc``),
            or an explicit branch list (heuristic).

    Returns:
        A ``ScreeningResult`` whose contingencies are sorted by ``_severity_key``.
        If the intact network does not solve, no contingency is examined, every one is
        ``unexamined`` and the proof is ``indeterminate`` or ``failed``.
    """
    work = copy.deepcopy(net)
    universe = branch_universe(work)
    ratings = _rated_mva(work)
    placeholders = int(np.sum(ratings >= PLACEHOLDER_RATING_MVA))
    warnings: list[str] = []
    universe_text = "single outages of every in-service line and two-winding transformer (no generator/load outages)"

    try:
        base_iterations = pm._runpp_counted(work, numba=True, max_iteration=MAX_ITERATIONS)
        if not _energized_mask(work, "vm_pu")[work.bus.in_service.to_numpy()].all():
            raise ValueError("Unsolved or nonfinite voltage on an in-service bus of the intact network")
    except Exception as error:  # noqa: BLE001
        status = "indeterminate" if isinstance(error, pp.LoadflowNotConverged) else "failed"
        warnings.append(f"base case did not solve ({type(error).__name__}: {error}); nothing screened")
        contingencies = [Contingency(element_type=t, element_id=i, coverage="unexamined", dc=None, ac=None, severity_rank=k)
                         for k, (t, i) in enumerate(universe)]
        proof = make_proof(converged=False, feasibility_status=status, solver_name="pandapower.runpp (Newton-Raphson)",  # type: ignore[arg-type]
                           solver_distribution="pandapower", validation_warnings=warnings)
        base = BaseCase(ac_converged=False, ac_iterations=None, voltage_violations=0, thermal_violations=0, violations=[],
                        max_loading_percent=None, kcl_residual_mva=None, placeholder_ratings=placeholders, rated_branches=len(ratings))
        return ScreeningResult(universe=universe_text, universe_size=len(universe), base=base, contingencies=contingencies,
                               coverage_counts={**{label: 0 for label in COVERAGE_LABELS}, "unexamined": len(universe)},
                               dc_complete=False, ac_complete=False, ac_selection=str(ac_selection),
                               screening_claim="nothing screened: the intact network did not solve",
                               contingencies_with_new_violations=0, ac_nonconvergent=0, islanding_contingencies=0, proof=proof)

    base_violations = _ac_violations(work)
    base_kcl = kcl_residual_mva(work)
    base = BaseCase(
        ac_converged=True, ac_iterations=base_iterations,
        voltage_violations=sum(1 for k in base_violations if k[0] == "bus"),
        thermal_violations=sum(1 for k in base_violations if k[0] != "bus"),
        violations=list(base_violations.values()), max_loading_percent=_max_loading(work),
        kcl_residual_mva=base_kcl, placeholder_ratings=placeholders, rated_branches=len(ratings),
    )

    dc_results: dict[Branch, DcOutcome] = {}
    if dc:
        dc_net = copy.deepcopy(work)
        pp.rundcpp(dc_net)
        base_flows = _branch_flows_dc(dc_net, universe)
        base_thermal = {(t, int(i)) for t in ("line", "trafo") if len(dc_net[t])
                        for i, v in dc_net[f"res_{t}"].loading_percent.astype(float).items() if v > THERMAL_LIMIT_PERCENT}
        targets = universe if dc_elements is None else [b for b in dc_elements if b in set(universe)]
        for branch in targets:
            dc_results[branch] = _solve_dc(dc_net, branch, universe, base_flows, base_thermal)
    dc_complete = dc and len(dc_results) == len(universe)

    if ac_selection == "all":
        ac_targets, ac_mode = list(universe), "exhaustive: every branch in the universe"
    elif ac_selection == "none":
        ac_targets, ac_mode = [], "none"
    elif isinstance(ac_selection, int) and not isinstance(ac_selection, bool):
        if not dc_results:
            raise ValueError("ac_selection=k ranks by the DC screen; run it (dc=True)")
        ranked = sorted(dc_results, key=lambda b: _dc_severity_key(dc_results[b]), reverse=True)
        ac_targets, ac_mode = ranked[:ac_selection], f"heuristic: top {ac_selection} by DC severity (islanded load, new thermal, loading, flow change)"
    else:
        ac_targets, ac_mode = [b for b in ac_selection if b in set(universe)], "heuristic: explicit candidate list"
    ac_results = {branch: _solve_ac(work, branch, base_violations) for branch in ac_targets}
    ac_complete = len(ac_results) == len(universe)

    contingencies: list[Contingency] = []
    for table, idx in universe:
        branch = (table, idx)
        ac, dco = ac_results.get(branch), dc_results.get(branch)
        residual = ac["kcl_residual_mva"] if ac is not None else None
        if ac is not None and ac["status"] == "solved" and residual is not None and math.isfinite(residual) \
                and residual <= KCL_RESIDUAL_TOLERANCE_MVA:
            label: CoverageLabel = "validated"
        elif ac is not None:
            label = "exhaustive" if ac_complete or dc_complete else "heuristic"
        elif dco is not None:
            label = "exhaustive" if dc_complete else "heuristic"
        else:
            label = "unexamined"
        contingencies.append(Contingency(element_type=table, element_id=idx, coverage=label, dc=dco, ac=ac, severity_rank=0))
    contingencies.sort(key=_severity_key)
    for rank, c in enumerate(contingencies, start=1):
        c["severity_rank"] = rank

    counts = {label: sum(1 for c in contingencies if c["coverage"] == label) for label in COVERAGE_LABELS}
    with_new = sum(1 for c in contingencies if c["ac"] is not None and c["ac"]["new_violations"])
    nonconv = sum(1 for c in contingencies if c["ac"] is not None and c["ac"]["status"] != "solved")
    islanding = sum(1 for c in contingencies
                    if (c["ac"] is not None and c["ac"]["de_energized_buses"]) or (c["dc"] is not None and c["dc"]["islanded_buses"]))
    claim_parts = [f"DC: {len(dc_results)}/{len(universe)} outages examined ({'complete' if dc_complete else 'INCOMPLETE'})"]
    claim_parts.append(f"AC: {len(ac_results)}/{len(universe)} examined ({'complete' if ac_complete else 'INCOMPLETE'}; {ac_mode}), "
                       f"{counts['validated']} validated by Kirchhoff residual")
    if not ac_complete:
        claim_parts.append("voltage violations are only established for AC-examined outages; DC cannot see voltages")
    if counts["unexamined"]:
        claim_parts.append(f"{counts['unexamined']} outages unexamined")
    if placeholders == len(ratings) and len(ratings):
        warnings.append(f"all {len(ratings)} branch ratings are >= {PLACEHOLDER_RATING_MVA:g} MVA placeholders: "
                        "the thermal screen is uninformative for this case")
    if base_violations:
        warnings.append(f"the intact case already has {len(base_violations)} violations; only new or worsened ones are charged to a contingency")
    stranded = sum(1 for c in contingencies if c["ac"] is not None and c["ac"]["de_energized_generation_mw"] > 0.0)
    if stranded:
        warnings.append(f"{stranded} outages de-energize islands that contain generation: pandapower energizes only "
                        "islands holding a slack, so their de-energized load is an upper bound pending a slack-reassignment study")
    if nonconv:
        warnings.append(f"{nonconv} AC contingencies did not converge (reported, not solved)")
    validated_residuals = [c["ac"]["kcl_residual_mva"] for c in contingencies
                           if c["coverage"] == "validated" and c["ac"] is not None and c["ac"]["kcl_residual_mva"] is not None]
    residuals = {"base_kcl_mva": base_kcl}
    if validated_residuals:
        residuals["max_validated_contingency_kcl_mva"] = float(max(validated_residuals))
    proof = make_proof(
        converged=True, feasibility_status="feasible" if base_kcl <= KCL_RESIDUAL_TOLERANCE_MVA else "indeterminate",
        solver_name="pandapower.runpp (Newton-Raphson) + pandapower.rundcpp", solver_distribution="pandapower",
        equation_residuals=residuals, constraint_violations=list(base_violations.values()),
        solver_iterations=base_iterations, validation_warnings=warnings,
    )
    return ScreeningResult(
        universe=universe_text, universe_size=len(universe), base=base, contingencies=contingencies,
        coverage_counts=counts, dc_complete=dc_complete, ac_complete=ac_complete, ac_selection=ac_mode,
        screening_claim="; ".join(claim_parts), contingencies_with_new_violations=with_new,
        ac_nonconvergent=nonconv, islanding_contingencies=islanding, proof=proof,
    )


# ---------------------------------------------------------------------------
# Slack reassignment for islanding outages (backlog 0023, proposal 3).
#
# pandapower energizes only islands that hold a slack. On snemSA (one slack generator)
# 70 outages strand generation, so their de-energized load is an upper bound (0023).
# This re-solves such an outage with one slack per otherwise-unslacked island that has
# generators, and reports the result beside the single-slack one. The slack chosen is
# the island's in-service generator with the largest max_p_mw: a named convention, not
# data. An island whose slack must exceed that generator's max_p_mw (or fall below its
# min_p_mw) cannot actually be balanced by it, and is flagged rather than counted served.


class IslandOutcome(TypedDict):
    """One island given a slack by reassignment."""

    slack_gen: int
    buses: int
    load_mw: float
    scheduled_generation_mw: float
    slack_p_mw: float | None
    within_slack_capacity: bool | None


class ReassignedOutcome(TypedDict):
    """An islanding outage re-solved with one slack per island that has generators."""

    element_type: str
    element_id: int
    single_slack_de_energized_buses: int
    single_slack_de_energized_load_mw: float
    status: str  # "solved", "nonconvergent", "error", or "no_island_with_generation"
    de_energized_buses: int
    de_energized_load_mw: float
    islands: list[IslandOutcome]
    min_vm_pu: float | None
    max_vm_pu: float | None
    kcl_residual_mva: float | None
    error: str | None


def _unslacked_islands(net: pp.pandapowerNet) -> list[set[int]]:
    """In-service bus sets with no slack (ext_grid or slack gen) but at least one in-service gen."""
    import pandapower.topology as top

    graph = top.create_nxgraph(net, respect_switches=True, include_out_of_service=False)
    slack_buses = set(net.ext_grid.bus[net.ext_grid.in_service]) | set(net.gen.bus[net.gen.in_service & net.gen.slack])
    gen_buses = set(net.gen.bus[net.gen.in_service])
    import networkx as nx

    return [set(c) for c in nx.connected_components(graph) if not (set(c) & slack_buses) and set(c) & gen_buses]


def reassign_island_slacks(net: pp.pandapowerNet, branch: Branch) -> ReassignedOutcome:
    """Re-solve one outage with a slack in every unslacked island that has generators.

    Args:
        net: The intact network (not modified).
        branch: The outage, ``(table, index)``.

    Returns:
        The single-slack de-energized count and load, and the reassigned solution.
    """
    work = copy.deepcopy(net)
    single = _solve_ac(copy.deepcopy(net), branch, {})
    _set_outage(work, branch, True)
    islands = _unslacked_islands(work)
    record = ReassignedOutcome(element_type=branch[0], element_id=branch[1],
                               single_slack_de_energized_buses=single["de_energized_buses"],
                               single_slack_de_energized_load_mw=single["de_energized_load_mw"],
                               status="no_island_with_generation", de_energized_buses=single["de_energized_buses"],
                               de_energized_load_mw=single["de_energized_load_mw"], islands=[],
                               min_vm_pu=single["min_vm_pu"], max_vm_pu=single["max_vm_pu"],
                               kcl_residual_mva=single["kcl_residual_mva"], error=None)
    if not islands:
        return record
    chosen: list[tuple[int, set[int]]] = []
    for buses in islands:
        candidates = work.gen[work.gen.in_service & work.gen.bus.isin(buses)]
        slack = int(candidates.max_p_mw.idxmax())
        work.gen.at[slack, "slack"] = True
        chosen.append((slack, buses))
    try:
        pm._runpp_counted(work, numba=True, max_iteration=MAX_ITERATIONS)
    except pp.LoadflowNotConverged as error:
        record.update(status="nonconvergent", error=f"LoadflowNotConverged: {error}")
        return record
    except Exception as error:  # noqa: BLE001
        record.update(status="error", error=f"{type(error).__name__}: {error}")
        return record
    energized = _energized_mask(work, "vm_pu")
    de_energized = [int(b) for b in work.bus.index[work.bus.in_service.to_numpy() & ~energized]]
    vm = work.res_bus.vm_pu.astype(float)
    finite = vm[np.isfinite(vm)]
    outcomes = []
    for slack, buses in chosen:
        p = work.res_gen.p_mw.get(slack)
        p = float(p) if p is not None and math.isfinite(float(p)) else None
        row = work.gen.loc[slack]
        within = None if p is None else bool(float(row.min_p_mw) <= p <= float(row.max_p_mw)) \
            if "min_p_mw" in row and "max_p_mw" in row else None
        outcomes.append(IslandOutcome(slack_gen=slack, buses=len(buses), load_mw=_load_at(work, sorted(buses)),
                                      scheduled_generation_mw=_generation_at(work, sorted(buses)),
                                      slack_p_mw=p, within_slack_capacity=within))
    record.update(status="solved", de_energized_buses=len(de_energized), de_energized_load_mw=_load_at(work, de_energized),
                  islands=outcomes, min_vm_pu=float(finite.min()) if len(finite) else None,
                  max_vm_pu=float(finite.max()) if len(finite) else None, kcl_residual_mva=kcl_residual_mva(work))
    return record


def slack_reassignment_study(net: pp.pandapowerNet, screening: ScreeningResult) -> list[ReassignedOutcome]:
    """Re-solve every screened outage that stranded generation, under both slack conventions."""
    return [reassign_island_slacks(net, (c["element_type"], c["element_id"])) for c in screening["contingencies"]
            if c["ac"] is not None and c["ac"]["de_energized_generation_mw"] > 0.0]


# ---------------------------------------------------------------------------
# Bounded N-2 screening (G2 deliverable). Exhaustive N-2 grows as n^2/2 (snemSA: about
# 243,000 pairs of its 698 branches), so this screens every pair among the M most severe
# single outages of an exhaustive AC N-1 screen, each by an exact AC re-solve. The coverage
# is heuristic by construction and is labelled so: a pair of two individually mild outages
# is not examined.

# Single outages whose pairs are screened; 10 gives 45 AC re-solves.
N2_TOP_SINGLES: int = 10


class PairOutcome(TypedDict):
    """One N-2 pair: both branches out, AC re-solved."""

    first: Branch
    second: Branch
    ac: AcOutcome


class N2Screening(TypedDict):
    """Bounded N-2: which pairs were examined, and what they found."""

    coverage: str
    singles_used: list[Branch]
    pairs_examined: int
    pairs_with_new_violations: int
    pairs_nonconvergent: int
    pairs: list[PairOutcome]


def screen_n2_bounded(net: pp.pandapowerNet, n1: ScreeningResult, top_singles: int = N2_TOP_SINGLES) -> N2Screening:
    """AC re-solve every pair among the ``top_singles`` most severe AC-examined single outages.

    Args:
        net: The intact network (not modified).
        n1: An N-1 screen of ``net`` with AC outcomes (``screen_n1(..., ac_selection="all")``).
        top_singles: How many of the most severe single outages to pair.
    """
    from itertools import combinations

    work = copy.deepcopy(net)
    pm._runpp_counted(work, numba=True, max_iteration=MAX_ITERATIONS)
    base_violations = _ac_violations(work)
    singles = [(c["element_type"], c["element_id"]) for c in n1["contingencies"]
               if c["ac"] is not None and c["ac"]["status"] == "solved"][:top_singles]
    pairs: list[PairOutcome] = []
    for first, second in combinations(singles, 2):
        _set_outage(work, first, True)
        try:
            pairs.append(PairOutcome(first=first, second=second, ac=_solve_ac(work, second, base_violations)))
        finally:
            _set_outage(work, first, False)
    return N2Screening(
        coverage=(f"heuristic: every pair among the {len(singles)} most severe single outages of an exhaustive AC N-1 "
                  "screen; pairs of individually milder outages are not examined"),
        singles_used=singles, pairs_examined=len(pairs),
        pairs_with_new_violations=sum(1 for p in pairs if p["ac"]["new_violations"]),
        pairs_nonconvergent=sum(1 for p in pairs if p["ac"]["status"] != "solved"), pairs=pairs)
