#!/usr/bin/env python3
"""Independent VeraGrid vs pandapower check for outage, line-loading and generator-dispatch studies (#162).

Usage:
    python3 scripts/veragrid_studies.py --case snemSA.m [--outages 12] [--tol-v 1e-4] [--tol-mva 1e-3] [--seed 1]

Extends `scripts/veragrid_crosscheck.py` (load scaling only). Both sides read the same MATPOWER file and run Newton-Raphson. The
three study families are applied to both with the same inputs:

  outage    take one branch out of service (pandapower in_service=False, VeraGrid active=False), re-solve. Branches are chosen
            by (from,to) bus pair among the pairs with exactly one branch in the case (so the same branch is meant on both
            sides), the most heavily loaded in the base case first, then a seeded random sample.
  loading   per-branch apparent power (MVA) at the lower-numbered end, matched by bus pair, base case and after every outage.
            The case's own ratings are the 10000 MVA placeholder, so a rating is derived: 90% of the base-case 90th-percentile
            flow (a deliberately tight limit so a few branches overload). The set of overloaded branches must be identical.
  dispatch  every generator's P times 1.1 (the slack absorbs the difference) and the largest generator +25 MW, re-solve.

Buses pandapower leaves de-energised (NaN, an island with no slack) are excluded from the comparison and counted in the line as
`islanded_buses_excluded`; VeraGrid gives those buses values (it solves or leaves an island), a policy difference, not a solver
error, and the energised network is what is compared.

Verdict per run: both solvers converged (or both failed), max |dV| <= --tol-v pu, max |dS| <= --tol-mva MVA, same overload set.
Prints one `PASS|FAIL study <name>: ...` line per run and `PASS|FAIL veragrid-studies: <passed>/<n>`. Exit 0 all pass,
1 any fail, 2 unreadable input or a solver that errored.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from typing import Any, Sequence

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

TOL_V = 1e-4
TOL_MVA = 1e-3
LIMIT_FRACTION = 0.9


class StudyError(Exception):
    """An input could not be read or a solver errored."""


def max_abs_diff(a: Sequence[float], b: Sequence[float]) -> float:
    """Largest absolute difference; NaN if any pair is NaN; ValueError on a length mismatch or empty input."""
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    if not a:
        raise ValueError("nothing to compare")
    diffs = [abs(x - y) for x, y in zip(a, b)]
    return math.nan if any(math.isnan(d) for d in diffs) else max(diffs)


def derived_limit(flows: Sequence[float]) -> float:
    """LIMIT_FRACTION of the base-case 90th-percentile flow, the stand-in rating for branches with no real one."""
    ordered = sorted(flows)
    return LIMIT_FRACTION * ordered[int(0.9 * (len(ordered) - 1))]


def overloaded(flows_by_key: dict[Any, float], limit: float) -> set[Any]:
    return {key for key, flow in flows_by_key.items() if flow > limit}


def unique_pairs(pairs: Sequence[tuple[int, int]]) -> set[tuple[int, int]]:
    """Unordered bus pairs that appear exactly once; a parallel circuit would make `which branch` ambiguous."""
    seen: dict[tuple[int, int], int] = {}
    for a, b in pairs:
        key = (min(a, b), max(a, b))
        seen[key] = seen.get(key, 0) + 1
    return {key for key, count in seen.items() if count == 1}


# ------------------------------------------------------------------------------------------------------------ pandapower side


class PP:
    """The pandapower path: wattson's loader and counted Newton-Raphson. Buses are addressed by position."""

    def __init__(self, case: str) -> None:
        from wattson.gridfit import load_case
        from wattson.inference import powermcp as pm
        import copy

        self.pm, self.copy = pm, copy
        try:
            self.base, _notes = load_case(case)
        except Exception as exc:
            raise StudyError(f"pandapower load: {type(exc).__name__}: {exc}") from exc
        self.pos = {bus: i for i, bus in enumerate(self.base.bus.index)}
        # branch table: (kind, table index, lower position, higher position)
        self.branches: list[tuple[str, int, int, int]] = []
        for idx, row in self.base.line.iterrows():
            self.branches.append(("line", idx, *sorted((self.pos[row.from_bus], self.pos[row.to_bus]))))
        for idx, row in self.base.trafo.iterrows():
            self.branches.append(("trafo", idx, *sorted((self.pos[row.hv_bus], self.pos[row.lv_bus]))))

    def run(self, outage: tuple[int, int] | None = None, gen_scale: float = 1.0, gen_bump: float = 0.0) -> dict[str, Any]:
        net = self.copy.deepcopy(self.base)
        try:
            if outage is not None:
                kind, idx, _a, _b = self.branch_for(outage)
                getattr(net, kind).loc[idx, "in_service"] = False
            net.gen["p_mw"] = net.gen["p_mw"] * gen_scale
            if gen_bump:
                net.gen.loc[net.gen["p_mw"].idxmax(), "p_mw"] += gen_bump
            self.pm._runpp_counted(net, numba=True, max_iteration=50)
        except Exception as exc:
            return {"converged": False, "error": f"{type(exc).__name__}: {exc}", "vm": [], "flows": {}}
        flows: dict[tuple[int, int], list[float]] = {}
        for kind, idx, a, b in self.branches:
            res = net.res_line if kind == "line" else net.res_trafo
            row = res.loc[idx]
            from_low = (self.pos[net.line.from_bus[idx]] if kind == "line" else self.pos[net.trafo.hv_bus[idx]]) == a
            p, q = ((row.p_from_mw, row.q_from_mvar) if from_low else (row.p_to_mw, row.q_to_mvar)) if kind == "line" else \
                   ((row.p_hv_mw, row.q_hv_mvar) if from_low else (row.p_lv_mw, row.q_lv_mvar))
            flows.setdefault((a, b), []).append(math.hypot(p, q))
        return {"converged": bool(net.converged), "vm": [float(v) for v in net.res_bus.vm_pu], "flows": flows,
                "slack_mw": float(net.res_gen.p_mw.sum())}

    def branch_for(self, pair: tuple[int, int]) -> tuple[str, int, int, int]:
        return next(item for item in self.branches if (item[2], item[3]) == pair)


# --------------------------------------------------------------------------------------------------------------- VeraGrid side


class VG:
    def __init__(self, case: str) -> None:
        try:
            from VeraGridEngine.IO.file_open import FileOpen

            self.case = case
            self.grid = FileOpen(case).open()
        except Exception as exc:
            raise StudyError(f"veragrid load: {type(exc).__name__}: {exc}") from exc
        self.pos = {id(bus): i for i, bus in enumerate(self.grid.buses)}

    def run(self, outage: tuple[int, int] | None = None, gen_scale: float = 1.0, gen_bump: float = 0.0) -> dict[str, Any]:
        import numpy as np
        from VeraGridEngine.IO.file_open import FileOpen
        from VeraGridEngine.Simulations.PowerFlow.power_flow_driver import PowerFlowDriver
        from VeraGridEngine.Simulations.PowerFlow.power_flow_options import PowerFlowOptions
        from VeraGridEngine.enumerations import SolverType

        try:
            grid = FileOpen(self.case).open()  # a fresh grid per run: no state leaks between studies
            branches = list(grid.get_branches())
            keys = [tuple(sorted((self.pos_of(grid, br.bus_from), self.pos_of(grid, br.bus_to)))) for br in branches]
            if outage is not None:
                hits = [i for i, key in enumerate(keys) if key == outage]
                if len(hits) != 1:
                    raise StudyError(f"outage {outage} matches {len(hits)} VeraGrid branches")
                branches[hits[0]].active = False
            for gen in grid.generators:
                gen.P *= gen_scale
            if gen_bump:
                max(grid.generators, key=lambda g: g.P).P += gen_bump
            driver = PowerFlowDriver(grid, PowerFlowOptions(solver_type=SolverType.NR, retry_with_other_methods=False))
            driver.run()
            res = driver.results
            flows: dict[tuple[int, int], list[float]] = {}
            for i, (br, key) in enumerate(zip(branches, keys)):
                low_is_from = self.pos_of(grid, br.bus_from) == key[0]
                flows.setdefault(key, []).append(float(abs(res.Sf[i] if low_is_from else res.St[i])))
            return {"converged": bool(res.converged), "vm": [float(v) for v in np.abs(res.voltage)], "flows": flows}
        except StudyError:
            raise
        except Exception as exc:
            return {"converged": False, "error": f"{type(exc).__name__}: {exc}", "vm": [], "flows": {}}

    @staticmethod
    def pos_of(grid: Any, bus: Any) -> int:
        return next(i for i, b in enumerate(grid.buses) if b is bus)


# ---------------------------------------------------------------------------------------------------------------- comparison


def flat(flows: dict[tuple[int, int], list[float]]) -> tuple[list[tuple[int, int]], list[float]]:
    """Branch keys and flows in key order, parallel circuits sorted within the key so their order cannot matter."""
    keys, values = [], []
    for key in sorted(flows):
        for value in sorted(flows[key]):
            keys.append(key)
            values.append(value)
    return keys, values


def compare(name: str, pp: dict[str, Any], vg: dict[str, Any], limit: float, tol_v: float, tol_mva: float) -> tuple[bool, str]:
    if "error" in pp or "error" in vg:
        both = "error" in pp and "error" in vg
        return both, f"{'both errored' if both else 'one side errored'}: pp={pp.get('error')} vg={vg.get('error')}"
    if pp["converged"] != vg["converged"]:
        return False, f"convergence differs: pp={pp['converged']} vg={vg['converged']}"
    if not pp["converged"]:
        return True, "neither converged (agree)"
    live = [i for i, v in enumerate(pp["vm"]) if not math.isnan(v)]
    islanded = len(pp["vm"]) - len(live)
    live_set = set(live)
    dv = max_abs_diff([pp["vm"][i] for i in live], [vg["vm"][i] for i in live])
    pk, pv = flat(pp["flows"])
    vk, vv = flat(vg["flows"])
    if pk != vk:
        return False, "branch sets differ"
    keep = [i for i, (a, b) in enumerate(pk) if a in live_set and b in live_set]
    ds = max_abs_diff([pv[i] for i in keep], [vv[i] for i in keep])
    worst = pk[keep[max(range(len(keep)), key=lambda j: abs(pv[keep[j]] - vv[keep[j]]))]]
    over_pp = {pk[i] for i in keep if pv[i] > limit}
    over_vg = {pk[i] for i in keep if vv[i] > limit}
    ok = dv <= tol_v and ds <= tol_mva and over_pp == over_vg
    note = f" islanded_buses_excluded={islanded}" if islanded else ""
    return ok, (f"max_abs_dv={dv:.3e} max_abs_ds_mva={ds:.3e} worst_branch={worst} overloaded pp={len(over_pp)} "
                f"vg={len(over_vg)} same={over_pp == over_vg}{note}")


def pick_outages(pp: PP, base: dict[str, Any], count: int, seed: int) -> list[tuple[int, int]]:
    ok = unique_pairs([(a, b) for _k, _i, a, b in pp.branches])
    ranked = sorted((key for key in base["flows"] if key in ok), key=lambda key: -base["flows"][key][0])
    top = ranked[: max(1, count // 2)]
    rest = [key for key in ranked if key not in top]
    random.Random(seed).shuffle(rest)
    return top + rest[: count - len(top)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="veragrid_studies.py", description=__doc__.split("\n")[0])
    parser.add_argument("--case", required=True, metavar="FILE.m")
    parser.add_argument("--outages", type=int, default=12)
    parser.add_argument("--tol-v", type=float, default=TOL_V)
    parser.add_argument("--tol-mva", type=float, default=TOL_MVA)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    if not os.path.isfile(args.case):
        print(f"ERROR veragrid-studies: cannot read {args.case}", file=sys.stderr)
        return 2
    try:
        pp, vg = PP(args.case), VG(args.case)
        base_pp = pp.run()
        if "error" in base_pp or not base_pp["converged"]:
            raise StudyError(f"pandapower base case did not solve: {base_pp.get('error', 'not converged')}")
        limit = derived_limit([f for key, vals in base_pp["flows"].items() for f in vals])
        print(f"derived limit {limit:.3f} MVA over {sum(len(v) for v in base_pp['flows'].values())} branches")
        runs: list[tuple[str, dict[str, Any]]] = [("base", {})]
        runs += [(f"outage {a}-{b}", {"outage": (a, b)}) for a, b in pick_outages(pp, base_pp, args.outages, args.seed)]
        runs += [("dispatch x1.1", {"gen_scale": 1.1}), ("dispatch +25MW on largest", {"gen_bump": 25.0})]
        results = []
        for name, kwargs in runs:
            ok, detail = compare(name, pp.run(**kwargs), vg.run(**kwargs), limit, args.tol_v, args.tol_mva)
            results.append(ok)
            print(f"{'PASS' if ok else 'FAIL'} study {name}: {detail}")
    except StudyError as exc:
        print(f"ERROR veragrid-studies: {exc}", file=sys.stderr)
        return 2
    passed = sum(results)
    print(f"{'PASS' if passed == len(results) else 'FAIL'} veragrid-studies: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
