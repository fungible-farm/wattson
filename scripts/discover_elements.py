#!/usr/bin/env python
"""M04 discovery: find monotonic, bracketed (case, element) combos for fit tasks.

Runs real pandapower on snem-sa-2000 and reports which buses / lines /
(gen,trafo) pairs give a monotonic, well-bracketed metric over a load or
element scale in [0.9, 1.1]. Output is a JSON blob of curated element lists
that generate.py hard-codes (with this script as the cited source).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "labs" / "_shared"))

import pandapower as pp  # noqa: E402
from wattson.gridfit import load_case, scale_loads  # noqa: E402

SCALE_LO = 0.9
SCALE_HI = 1.1
MIN_RANGE = 0.005  # pu (voltage) or % (loading) — minimum endpoint spread to be useful


def vm_at(net, s):
    n = scale_loads(net, s)
    pp.runpp(n, numba=True)
    return n


def load_scale_buses(net, sample=60):
    """Buses whose vm_pu moves monotonically and >= MIN_RANGE over s in [0.9,1.1]."""
    idx = sorted(int(i) for i in net.bus.index)
    step = max(1, len(idx) // sample)
    picks = idx[::step]
    out = []
    n_lo = vm_at(net, SCALE_LO)
    n_hi = vm_at(net, SCALE_HI)
    for b in picks:
        v_lo = float(n_lo.res_bus.loc[b, "vm_pu"])
        v_hi = float(n_hi.res_bus.loc[b, "vm_pu"])
        spread = abs(v_hi - v_lo)
        if spread >= MIN_RANGE:
            out.append({"bus": b, "vm_lo": round(v_lo, 6), "vm_hi": round(v_hi, 6)})
    return out


def line_rating_lines(net, sample=60):
    """Lines whose loading_percent moves monotonically and >= MIN_RANGE when
    max_i_ka is scaled over [0.9, 1.1]."""
    idx = sorted(int(i) for i in net.line.index)
    step = max(1, len(idx) // sample)
    picks = idx[::step]
    out = []
    for L in picks:
        n_lo = copy.deepcopy(net)
        n_lo.line.loc[L, "max_i_ka"] *= SCALE_LO
        n_hi = copy.deepcopy(net)
        n_hi.line.loc[L, "max_i_ka"] *= SCALE_HI
        try:
            pp.runpp(n_lo, numba=True)
            pp.runpp(n_hi, numba=True)
        except Exception:
            continue
        l_lo = float(n_lo.res_line.loc[L, "loading_percent"])
        l_hi = float(n_hi.res_line.loc[L, "loading_percent"])
        spread = abs(l_hi - l_lo)
        if spread >= MIN_RANGE and 0 < l_lo < 200 and 0 < l_hi < 200:
            out.append({"line": L, "load_lo": round(l_lo, 4), "load_hi": round(l_hi, 4)})
    return out


def gen_dispatch_pairs(net, sample=40):
    """(gen_bus, trafo) pairs whose trafo loading moves monotonically and
    >= MIN_RANGE when the gen's p_mw is scaled over [0.9, 1.1]."""
    gens = sorted(int(i) for i in net.gen.index)
    trafos = sorted(int(i) for i in net.trafo.index)
    out = []
    for g in gens[:12]:
        gb = int(net.gen.loc[g, "bus"])
        # candidate trafos: the one electrically nearest to the gen bus (by index proximity is a proxy)
        for t in trafos:
            tb_from = int(net.trafo.loc[t, "hv_bus"])
            tb_to = int(net.trafo.loc[t, "lv_bus"])
            if gb not in (tb_from, tb_to):
                continue
            n_lo = copy.deepcopy(net)
            n_lo.gen.loc[g, "p_mw"] *= SCALE_LO
            n_hi = copy.deepcopy(net)
            n_hi.gen.loc[g, "p_mw"] *= SCALE_HI
            try:
                pp.runpp(n_lo, numba=True)
                pp.runpp(n_hi, numba=True)
            except Exception:
                continue
            l_lo = float(n_lo.res_trafo.loc[t, "loading_percent"])
            l_hi = float(n_hi.res_trafo.loc[t, "loading_percent"])
            spread = abs(l_hi - l_lo)
            if spread >= MIN_RANGE and 0 < l_lo < 200 and 0 < l_hi < 200:
                out.append({"gen": g, "gen_bus": gb, "trafo": t,
                            "load_lo": round(l_lo, 4), "load_hi": round(l_hi, 4)})
            if len(out) >= sample:
                break
        if len(out) >= sample:
            break
    return out


def main():
    case = REPO_ROOT / "data" / "snemSA.m"
    net, _ = load_case(str(case))
    pp.runpp(net, numba=True)  # warm numba
    result = {
        "case": "snem-sa-2000",
        "scale": [SCALE_LO, SCALE_HI],
        "load_scale_buses": load_scale_buses(net),
        "line_rating_lines": line_rating_lines(net),
        "gen_dispatch_pairs": gen_dispatch_pairs(net),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
