"""Independent DC network model built from a case's MATPOWER-form matrices.

The G2 cross-checks (backlog 0016) need a formulation that does not share code with
the pandapower solver path they check. This module takes only the *case data* --
pandapower's MATPOWER-form ``ppc`` arrays (bus, branch) and its element tables
(gen, ext_grid, poly_cost) -- and builds the DC susceptance matrices itself with numpy
and scipy.sparse, following the textbook lossless DC model (MATPOWER manual §3.7):

    Pf = Bf @ theta + Pfinj,     P_bus = Bbus @ theta + Pbusinj,
    b_k = status_k / (x_k * tau_k),  Pfinj_k = -b_k * shift_k.

It does not call ``makeBdc``, ``dcpf`` or the OPF; ``tests`` compare its matrices with
``makeBdc`` as one more independent agreement check.

Licences: pandapower BSD-3-Clause (``to_ppc`` conversion only), numpy/scipy BSD-3-Clause.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
import pandapower as pp
import scipy.sparse as sp
from pandapower.converter.pypower.to_ppc import to_ppc
from pandapower.pypower.idx_brch import BR_STATUS, BR_X, F_BUS, RATE_A, SHIFT, T_BUS, TAP
from pandapower.pypower.idx_bus import BUS_TYPE, GS, NONE, PD, REF

# Below this |x| (pu) a branch is treated as a zero-impedance model error rather than
# divided by: MATPOWER's own dcpf divides blindly and would return inf. 1e-9 pu is ~6
# orders below the smallest reactance in the CSIRO SA case (min line x ~0.27 ohm/km
# at 66-275 kV, i.e. >1e-5 pu); a real branch never comes close.
MIN_BRANCH_REACTANCE_PU: float = 1e-9


class UnsupportedModel(ValueError):
    """The case uses an element or cost the independent formulation does not model.

    Raised rather than silently dropping the element, so a cross-check is reported as
    unavailable instead of as a (wrong) agreement.
    """


@dataclass
class DcGenerator:
    """One dispatchable (or fixed) injection in the independent model."""

    element_type: str  # "gen" or "ext_grid"
    element_id: int
    bus_row: int  # row in the in-service DC model
    p_min_mw: float
    p_max_mw: float
    cost_c1_per_mw: float
    cost_c0: float
    # Quadratic coefficient (EUR/MW^2/h); nonzero makes the DC OPF a QP (dc_opf.solve_independent_qp).
    cost_c2_per_mw2: float = 0.0


@dataclass
class DcCase:
    """In-service DC model of one network, indexed consecutively from 0.

    ``bus_ids[i]`` is the pandapower bus behind model row ``i`` (the first pandapower
    bus that pandapower fused into that ppc bus); ``branch_ids[k]`` is the pandapower
    ``(table, index)`` behind model branch ``k``.
    """

    base_mva: float
    bus_ids: list[int]
    ref_rows: list[int]
    branch_ids: list[tuple[str, int]]
    from_rows: np.ndarray
    to_rows: np.ndarray
    b_pu: np.ndarray
    rate_mw: np.ndarray  # 0 = unlimited (pandapower OPF convention, build_branch.py)
    bbus: sp.csr_matrix
    bf: sp.csr_matrix
    pfinj_pu: np.ndarray
    pbusinj_pu: np.ndarray
    pd_mw: np.ndarray  # fixed withdrawal per bus: loads - non-dispatchable sgens + shunt G at 1 pu
    generators: list[DcGenerator] = field(default_factory=list)
    ppc_bus_to_row: dict[int, int] = field(default_factory=dict)
    ppc_branch_rows: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))
    ppc: dict = field(default_factory=dict)


def _branch_table_ids(net: pp.pandapowerNet) -> list[tuple[str, int]]:
    """pandapower (table, index) for every ppc branch row, from ``_pd2ppc_lookups``."""
    ids: list[tuple[str, int] | None] = []
    lookups = net._pd2ppc_lookups["branch"]
    total = max(end for _, end in lookups.values()) if lookups else 0
    ids = [None] * total
    for table, (start, end) in lookups.items():
        for offset, element in enumerate(net[table].index):
            ids[start + offset] = (table, int(element))
    if any(item is None for item in ids):
        raise UnsupportedModel("could not map every ppc branch row to a pandapower element")
    return ids  # type: ignore[return-value]


def _costs(net: pp.pandapowerNet) -> dict[tuple[str, int], tuple[float, float, float]]:
    """Polynomial generator costs ``(c1, c0, c2)`` by ``(et, element)``; anything else is unsupported."""
    if len(net.pwl_cost):
        raise UnsupportedModel("piecewise-linear costs are not in the independent LP")
    costs: dict[tuple[str, int], tuple[float, float]] = {}
    for row in net.poly_cost.itertuples():
        if row.et not in ("gen", "ext_grid"):
            raise UnsupportedModel(f"cost on controllable {row.et} is not modelled")
        if row.cp2_eur_per_mw2 < 0.0:
            raise UnsupportedModel("a negative quadratic cost makes the DC OPF non-convex")
        costs[(row.et, int(row.element))] = (float(row.cp1_eur_per_mw), float(row.cp0_eur), float(row.cp2_eur_per_mw2))
    return costs


def build_dc_case(net: pp.pandapowerNet, with_costs: bool = True) -> DcCase:
    """Build the independent in-service DC model of ``net`` (not modified).

    ``with_costs=False`` skips the cost model (sensitivities need only the network);
    generator costs are then zero.

    Why ``to_ppc(mode="opf")``: in ``"pf"`` mode pandapower writes a placeholder
    ``RATE_A = 100`` MVA for branches without ``max_loading_percent``
    (``build_branch.py``); ``"opf"`` mode writes 0, which is what the OPF actually
    enforces (unlimited), so both formulations see the same limits.

    Raises:
        UnsupportedModel: for controllable sgens/loads/storage, DC lines, piecewise or
            negative-quadratic costs, or a zero-reactance in-service branch.
    """
    work = copy.deepcopy(net)
    for table in ("sgen", "load", "storage") if with_costs else ():
        if len(work[table]) and "controllable" in work[table] and work[table].controllable.fillna(False).astype(bool).any():
            raise UnsupportedModel(f"controllable {table} is not modelled")
    if len(work.dcline):
        raise UnsupportedModel("DC lines are not modelled")
    costs = _costs(work) if with_costs else {}
    ppc = to_ppc(work, mode="opf", init="flat", calculate_voltage_angles=True)
    bus = np.real(ppc["bus"])
    branch = np.real(ppc["branch"])
    base_mva = float(ppc["baseMVA"])

    in_bus = np.flatnonzero(bus[:, BUS_TYPE] != NONE)
    ppc_bus_to_row = {int(b): r for r, b in enumerate(in_bus)}
    lookup = work._pd2ppc_lookups["bus"]
    bus_ids: list[int] = [-1] * len(in_bus)
    for pp_bus in work.bus.index:
        ppc_row = int(lookup[pp_bus])
        row = ppc_bus_to_row.get(ppc_row)
        if row is not None and bus_ids[row] == -1:
            bus_ids[row] = int(pp_bus)

    all_branch_ids = _branch_table_ids(work)
    live = (branch[:, BR_STATUS] > 0) & np.isin(branch[:, F_BUS], in_bus) & np.isin(branch[:, T_BUS], in_bus)
    branch_rows = np.flatnonzero(live)
    x = branch[branch_rows, BR_X]
    if np.any(np.abs(x) < MIN_BRANCH_REACTANCE_PU):
        raise UnsupportedModel("zero-reactance in-service branch")
    tap = branch[branch_rows, TAP].copy()
    tap[tap == 0.0] = 1.0
    b = 1.0 / (x * tap)
    shift_rad = np.deg2rad(branch[branch_rows, SHIFT])
    f = np.array([ppc_bus_to_row[int(v)] for v in branch[branch_rows, F_BUS]], dtype=int)
    t = np.array([ppc_bus_to_row[int(v)] for v in branch[branch_rows, T_BUS]], dtype=int)
    nb, nl = len(in_bus), len(branch_rows)
    rows = np.r_[np.arange(nl), np.arange(nl)]
    cft = sp.csr_matrix((np.r_[np.ones(nl), -np.ones(nl)], (rows, np.r_[f, t])), shape=(nl, nb))
    bf = sp.csr_matrix((np.r_[b, -b], (rows, np.r_[f, t])), shape=(nl, nb))
    bbus = (cft.T @ bf).tocsr()
    pfinj = -b * shift_rad
    pbusinj = cft.T @ pfinj

    pd_mw = bus[in_bus, PD] + bus[in_bus, GS]
    generators: list[DcGenerator] = []
    for table in ("gen", "ext_grid"):
        if not len(work[table]):
            continue
        for element, row in work[table].iterrows():
            if not bool(row.in_service):
                continue
            ppc_bus = int(lookup[int(row.bus)])
            if ppc_bus not in ppc_bus_to_row:
                continue
            flag = row.get("controllable", True)
            controllable = True if flag is None or (isinstance(flag, float) and np.isnan(flag)) else bool(flag)
            if table == "ext_grid" and not controllable:
                raise UnsupportedModel("non-controllable ext_grid is not modelled")
            p_min = float(row.get("min_p_mw", -np.inf)) if "min_p_mw" in row else -np.inf
            p_max = float(row.get("max_p_mw", np.inf)) if "max_p_mw" in row else np.inf
            p_min = -np.inf if np.isnan(p_min) else p_min
            p_max = np.inf if np.isnan(p_max) else p_max
            if table == "gen" and not controllable:
                p_min = p_max = float(row.p_mw) * float(row.get("scaling", 1.0))
            c1, c0, c2 = costs.get((table, int(element)), (0.0, 0.0, 0.0))
            generators.append(DcGenerator(table, int(element), ppc_bus_to_row[ppc_bus], p_min, p_max, c1, c0, c2))

    ref_rows = [ppc_bus_to_row[int(v)] for v in np.flatnonzero(bus[:, BUS_TYPE] == REF) if int(v) in ppc_bus_to_row]
    return DcCase(
        base_mva=base_mva, bus_ids=bus_ids, ref_rows=ref_rows,
        branch_ids=[all_branch_ids[int(k)] for k in branch_rows],
        from_rows=f, to_rows=t, b_pu=b, rate_mw=branch[branch_rows, RATE_A].copy(),
        bbus=bbus, bf=bf, pfinj_pu=pfinj, pbusinj_pu=np.asarray(pbusinj).ravel(), pd_mw=pd_mw,
        generators=generators, ppc_bus_to_row=ppc_bus_to_row, ppc_branch_rows=branch_rows, ppc=ppc,
    )
