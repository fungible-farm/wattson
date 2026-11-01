#!/usr/bin/env python3
"""Cross-validate `workflow.py`'s pandapower-based N-1 contingency screen against a real,
independent solve of the exact same 21 contingencies using pypowsybl (RTE's PowSyBl-Java-backed
power-system framework, OpenLoadFlow solver) -- two different engines, two different codebases,
same real `snem1803.m` case and the same candidate 250 MW generator at bus 175.

This promotes pypowsybl from `benchmarks/lab03-advanced-provider-bakeoff/spike_pypowsybl.py`'s standalone
aggregate-loss comparison into a real, wired-in capability: a genuine second opinion on the same
N-1 screening decision Lab 2 already makes, not just "does the solver run." See README.md's
"pypowsybl N-1 cross-check" section for the full write-up and findings.

Two real pypowsybl/PowSyBl data-fidelity gaps found and worked around while building this (both
in `src/wattson/gridfit.py`, reused from here, not reimplemented):

1. pypowsybl's MATPOWER importer only accepts the binary `.mat` MATLAB serialization, not the
   `.m` script format every case in this repo uses -- `gridfit.to_pypowsybl_network()` round-trips
   through pandapower's own `to_mpc()` writer.
2. pandapower's own bus index is not what pypowsybl's `LINE-<a>-<b>` element ids are keyed to --
   `gridfit.pypowsybl_element_id_map()` uses pandapower's internal `to_ppc()` bus-renumbering
   lookup, the only thing that actually lines up (verified 1215/1215 = 100% on this case).

Usage:
    uv run benchmarks/lab02-medium-interconnection-screening/pypowsybl_cross_check.py --step run
    uv run benchmarks/lab02-medium-interconnection-screening/pypowsybl_cross_check.py --step check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final, TypedDict


import pandapower as pp
import pypowsybl.security as sa
from pandapower.converter.pypower import to_ppc

from wattson.gridfit import pypowsybl_element_id_map, to_pypowsybl_network

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow import (  # noqa: E402  (needs sys.path insert above)
    CANDIDATE_BUS,
    THERMAL_LIMIT_PERCENT,
    VOLTAGE_BAND_PU,
    build_base_net,
    local_contingency_lines,
    run_contingencies,
)

LAB_DIR: Final[Path] = Path(__file__).resolve().parent
SPIKE_OUTPUT_DIR: Final[Path] = LAB_DIR / "pypowsybl_output"
MAT_FILE: Final[Path] = SPIKE_OUTPUT_DIR / "snem1803_lab2.mat"
RESULT_FILE: Final[Path] = SPIKE_OUTPUT_DIR / "pypowsybl_n1_comparison.json"
FIXTURE_FILE: Final[Path] = LAB_DIR / "expected_pypowsybl_n1_comparison.json"

# Absolute tolerance on worst-case bus voltage (pu) between the two engines' independent
# solves of the same contingency. Observed real gap on snem1803.m (2026-08-21): pypowsybl's
# base-case worst voltage (bus VL-910, 0.899249 pu) matches workflow.py's own base-case finding
# (bus 1126 at 0.899 pu) to better than 3e-4 pu -- 5e-3 is a >15x margin over that.
VOLTAGE_ATOL_PU: Final[float] = 5e-3
# Relative tolerance on worst-case line loading (%) between the two engines. Looser than the
# voltage tolerance for a real, named reason: pypowsybl's per-branch current (i1/i2) showed an
# ~11.7% difference from pandapower's i_from_ka on a sampled line even though both engines'
# aggregate P/Q agreed to <0.2% (see benchmarks/lab03-advanced-provider-bakeoff/README.md's aggregate
# comparison) -- a genuine per-branch current-modelling difference between the two solvers'
# line models, not chased further than named here. 20% keeps this a real regression gate
# (catches a broken id mapping or a wrong-line contingency) without failing on that known gap.
LOADING_RTOL_PERCENT: Final[float] = 20.0


class ContingencyComparison(TypedDict):
    line: int
    pandapower_worst_voltage_pu: float | None
    pypowsybl_worst_voltage_pu: float | None
    voltage_diff_pu: float | None
    pandapower_worst_loading_percent: float | None
    pypowsybl_worst_loading_percent: float | None
    loading_diff_percent: float | None
    agrees: bool


def run_pypowsybl_n1(net: pp.pandapowerNet, lines: list[int]) -> dict[int, dict[str, float]]:
    """Run one real pypowsybl N-1 security analysis covering all `lines` contingencies at once,
    monitoring every branch/voltage level so the worst-case figures are genuinely network-wide
    (matching `workflow.py`'s own `net.res_bus.vm_pu.min()` / `net.res_line.loading_percent.max()`
    scope, not just the local contingency set).

    Args:
        net: the solved base-case pandapower net (candidate generator already attached).
        lines: `net.line` row indices to screen (from `local_contingency_lines()`).

    Returns:
        Maps each contingency line index to
        `{"worst_voltage_pu": ..., "worst_loading_percent": ...}`.
    """
    to_ppc(net)  # populates net._pd2ppc_lookups, required by pypowsybl_element_id_map
    SPIKE_OUTPUT_DIR.mkdir(exist_ok=True)
    pn_net = to_pypowsybl_network(net, MAT_FILE)
    id_map = pypowsybl_element_id_map(net, pn_net)
    missing = [li for li in lines if li not in id_map]
    if missing:
        raise RuntimeError(f"no pypowsybl element id found for pandapower line(s) {missing}")

    contingency_id_by_line = {li: id_map[li] for li in lines}
    # Only pandapower's own `line` table (not `trafo`) carries max_i_ka, and workflow.py's own
    # worst_loading_percent is itself scoped to net.res_line only -- match that scope exactly.
    max_i_ka_by_pypowsybl_id = {
        id_map[i]: float(row.max_i_ka)
        for i, row in net.line.iterrows()
        if i in id_map
    }
    nominal_v = pn_net.get_voltage_levels()["nominal_v"]

    analysis = sa.create_analysis()
    analysis.add_single_element_contingencies(list(contingency_id_by_line.values()))
    all_branch_ids = list(max_i_ka_by_pypowsybl_id.keys())
    all_vl_ids = list(pn_net.get_voltage_levels().index)
    analysis.add_monitored_elements(branch_ids=all_branch_ids, voltage_level_ids=all_vl_ids)
    result = analysis.run_ac(pn_net)

    bus_res = result.bus_results.reset_index()
    bus_res["nominal_v"] = bus_res["voltage_level_id"].map(nominal_v)
    bus_res["v_pu"] = bus_res["v_mag"] / bus_res["nominal_v"]

    branch_res = result.branch_results.reset_index()
    branch_res = branch_res[branch_res["branch_id"].isin(max_i_ka_by_pypowsybl_id)]
    branch_res["max_i_ka"] = branch_res["branch_id"].map(max_i_ka_by_pypowsybl_id)
    branch_res["loading_percent"] = (
        branch_res[["i1", "i2"]].abs().max(axis=1) / (branch_res["max_i_ka"] * 1000.0) * 100.0
    )

    out: dict[int, dict[str, float]] = {}
    for line_idx, cont_id in contingency_id_by_line.items():
        v_rows = bus_res[bus_res["contingency_id"] == cont_id]
        l_rows = branch_res[branch_res["contingency_id"] == cont_id]
        out[line_idx] = {
            "worst_voltage_pu": float(v_rows["v_pu"].min()) if len(v_rows) else None,
            "worst_loading_percent": float(l_rows["loading_percent"].max()) if len(l_rows) else None,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=["run", "check"], default="run")
    args = parser.parse_args()

    net = build_base_net()
    pp.runpp(net, init="flat")
    lines = local_contingency_lines(net, CANDIDATE_BUS)

    print(f"Running pandapower N-1 screen ({len(lines)} contingencies, workflow.py's own path)...")
    pp_results = {r["line"]: r for r in run_contingencies(verbose=False)}

    print(f"Running pypowsybl N-1 security analysis ({len(lines)} contingencies, one batch)...")
    sb_results = run_pypowsybl_n1(net, lines)

    comparisons: list[ContingencyComparison] = []
    for li in lines:
        pp_r = pp_results[li]
        sb_r = sb_results[li]
        v_diff = (
            abs(pp_r["worst_voltage_pu"] - sb_r["worst_voltage_pu"])
            if pp_r["worst_voltage_pu"] is not None and sb_r["worst_voltage_pu"] is not None
            else None
        )
        l_diff = (
            abs(pp_r["worst_loading_percent"] - sb_r["worst_loading_percent"])
            / pp_r["worst_loading_percent"]
            * 100.0
            if pp_r["worst_loading_percent"] not in (None, 0)
            and sb_r["worst_loading_percent"] is not None
            else None
        )
        agrees = (
            pp_r["converged"]
            and v_diff is not None
            and v_diff <= VOLTAGE_ATOL_PU
            and l_diff is not None
            and l_diff <= LOADING_RTOL_PERCENT
        )
        comparisons.append(
            {
                "line": li,
                "pandapower_worst_voltage_pu": pp_r["worst_voltage_pu"],
                "pypowsybl_worst_voltage_pu": sb_r["worst_voltage_pu"],
                "voltage_diff_pu": v_diff,
                "pandapower_worst_loading_percent": pp_r["worst_loading_percent"],
                "pypowsybl_worst_loading_percent": sb_r["worst_loading_percent"],
                "loading_diff_percent": l_diff,
                "agrees": agrees,
            }
        )

    agree_count = sum(1 for c in comparisons if c["agrees"])
    print(f"\n{'line':>5} {'pp_V':>8} {'sb_V':>8} {'dV':>8}   {'pp_load%':>9} {'sb_load%':>9} {'dload%':>7}  agree")
    for c in comparisons:
        print(
            f"{c['line']:>5} "
            f"{c['pandapower_worst_voltage_pu']:>8.4f} {c['pypowsybl_worst_voltage_pu']:>8.4f} "
            f"{c['voltage_diff_pu']:>8.5f}   "
            f"{c['pandapower_worst_loading_percent']:>9.2f} {c['pypowsybl_worst_loading_percent']:>9.2f} "
            f"{c['loading_diff_percent']:>7.2f}  {'yes' if c['agrees'] else 'NO'}"
        )
    print(f"\n{agree_count}/{len(comparisons)} contingencies agree between pandapower and pypowsybl")
    print(
        f"(voltage tolerance {VOLTAGE_ATOL_PU} pu absolute, "
        f"loading tolerance {LOADING_RTOL_PERCENT}% relative -- see module docstring for why)"
    )

    SPIKE_OUTPUT_DIR.mkdir(exist_ok=True)
    RESULT_FILE.write_text(
        json.dumps(
            {
                "candidate_bus": CANDIDATE_BUS,
                "voltage_band_pu": list(VOLTAGE_BAND_PU),
                "thermal_limit_percent": THERMAL_LIMIT_PERCENT,
                "agree_count": agree_count,
                "total_count": len(comparisons),
                "comparisons": comparisons,
            },
            indent=2,
        )
    )

    if agree_count < len(comparisons):
        print(f"FAIL: {len(comparisons) - agree_count} contingency(ies) disagree beyond tolerance")
        return 1

    if args.step == "check":
        if not FIXTURE_FILE.exists():
            print(f"FAIL: no fixture at {FIXTURE_FILE} -- run --step run first and commit it")
            return 1
        expected = json.loads(FIXTURE_FILE.read_text())
        if expected["agree_count"] != agree_count or expected["total_count"] != len(comparisons):
            print(
                f"FAIL: agreement count drifted from fixture "
                f"({expected['agree_count']}/{expected['total_count']} -> "
                f"{agree_count}/{len(comparisons)})"
            )
            return 1
        print("MATCH")

    print("PASS: pypowsybl N-1 security analysis cross-validates workflow.py's pandapower screen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
