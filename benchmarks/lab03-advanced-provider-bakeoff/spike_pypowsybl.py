"""Spike: does pypowsybl (RTE's PowSyBl-Java-backed load-flow engine, OpenLoadFlow solver)
solve the same real CSIRO `snem1803.m` case this lab already solves with pandapower, and do
the two engines agree?

This is deliberately **not** a fourth Lab 3 "provider" -- this lab's three providers
(`local-policy-A/B/C`) are deterministic search policies standing in for an LLM's "propose the
next trial value" role, all running the exact same underlying pandapower solver (see
`orchestrator.py`'s own Sandbox notes). Folding a different power-flow *engine* into that same
"provider" column would misrepresent what a provider means there. This script instead answers a
different, solver-level question -- is pypowsybl a viable alternative power-flow engine for this
repo's real NEM data at all -- as a standalone comparison, not wired into the bake-off matrix.

Real finding, not assumed: pypowsybl's MATPOWER importer (`pypowsybl.network.load`, format
"MATPOWER") only accepts the binary MATLAB `.mat` serialization of a case -- confirmed by testing
against both this lab's own `snem1803.m` and a hand-written textbook `case9.m`, both rejected by
`pypowsybl.network.is_loadable()` (verified against
https://powsybl.readthedocs.io/projects/powsybl-core/en/latest/grid_exchange_formats/matpower/,
which documents the same requirement: ".m files have to be converted to .mat files first"). Every
`.m` case file in this repo -- and everything MATPOWER itself ships -- is the human-readable
script format, not `.mat`. No MATLAB/Octave is installed in this environment to do that
conversion the documented way. The fix used here: round-trip the already-loaded pandapower net
through pandapower's own `to_mpc()` (a real, `scipy.io.savemat`-backed MATPOWER `.mat` writer)
before handing it to pypowsybl -- both solvers then run against the exact same in-memory network,
not two independently-parsed copies, which also makes the comparison fairer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Final

import pandapower as pp
import pypowsybl.loadflow as lf

LAB_DIR: Final[Path] = Path(__file__).resolve().parent
from wattson import fetch  # noqa: E402
from wattson.gridfit import load_case, to_pypowsybl_network  # noqa: E402  (needs sys.path insert above)

DATA_FILE: Final[Path] = fetch.case_dir() / "snem1803.m"  # same case orchestrator.py uses
SPIKE_OUTPUT_DIR: Final[Path] = LAB_DIR / "spike_output"
MAT_FILE: Final[Path] = SPIKE_OUTPUT_DIR / "snem1803.mat"
RESULT_FILE: Final[Path] = SPIKE_OUTPUT_DIR / "pypowsybl_comparison.json"
FIXTURE_FILE: Final[Path] = LAB_DIR / "expected_pypowsybl_comparison.json"

# Relative tolerance on total system real-power losses between the two independently-solving
# engines. Observed real discrepancy on snem1803.m (2026-08-20): 1035.932 MW (pandapower) vs
# 1034.136 MW (pypowsybl/OpenLoadFlow) = 0.17% relative -- the line/trafo loss *split* differs
# more than the total (886/150 MW vs 912/122 MW), most likely a transformer tap-model
# convention difference between the two solvers' formulations, not a solver defect; see
# README.md's "pypowsybl spike" section. 1.0% is a >5x margin over that observed gap so a
# genuine regression (a broken .mat round-trip, a real divergence) fails loudly.
LOSS_TOLERANCE_PCT: Final[float] = 1.0
# Drift tolerance for `--step check` against the committed fixture -- looser than a bit-exact
# diff since both solvers' iterative Newton-Raphson paths can shift in the last ULP between
# library versions without indicating a real regression.
FIXTURE_DRIFT_TOLERANCE_PCT: Final[float] = 0.05


def run_pandapower(net: pp.pandapowerNet) -> dict[str, float]:
    """Run pandapower's own AC power flow.

    `init="flat"` matches `orchestrator.py`'s own convention for this exact case -- pandapower's
    default `init="auto"` hits a real `FloatingPointError: divide by zero` in its DC-warm-start
    path on `snem1803.m` (a genuine property of this case's data, not this script's own bug;
    `orchestrator.py` line ~503 already works around it the same way).
    """
    t0 = time.time()
    pp.runpp(net, init="flat")
    elapsed = time.time() - t0
    return {
        "solve_s": elapsed,
        "total_gen_mw": float(net.res_gen.p_mw.sum() + net.res_ext_grid.p_mw.sum()),
        "total_load_mw": float(net.res_load.p_mw.sum()),
        "total_loss_mw": float(net.res_line.pl_mw.sum() + net.res_trafo.pl_mw.sum()),
    }


def run_pypowsybl(pp_net: pp.pandapowerNet, mat_file: Path) -> dict[str, float]:
    """Round-trip `pp_net` to a real `.mat` file (see module docstring and
    `gridfit.to_pypowsybl_network()`) and run pypowsybl's own AC load flow (OpenLoadFlow,
    pypowsybl's bundled default solver)."""
    t0 = time.time()
    net = to_pypowsybl_network(pp_net, mat_file)
    results = lf.run_ac(net)
    elapsed = time.time() - t0
    if results[0].status != lf.ComponentStatus.CONVERGED:
        raise RuntimeError(f"pypowsybl load flow did not converge: {results[0].status}")

    gens = net.get_generators()
    loads = net.get_loads()
    lines = net.get_lines()
    xfmr = net.get_2_windings_transformers()
    return {
        "solve_s": elapsed,
        # pypowsybl reports generator P in load-sign convention (negative = generating) --
        # flip to match pandapower's generation-positive convention for a like-for-like sum.
        "total_gen_mw": float(-gens["p"].sum()),
        "total_load_mw": float(loads["p"].sum()),
        "total_loss_mw": float(
            (lines["p1"] + lines["p2"]).sum() + (xfmr["p1"] + xfmr["p2"]).sum()
        ),
        "slack_mismatch_mw": float(results[0].slack_bus_results[0].active_power_mismatch),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=["run", "check"], default="run")
    args = parser.parse_args()

    if not DATA_FILE.exists():
        print(
            f"[FAIL] {DATA_FILE} not found -- run "
            f"'uv run scripts/fetch_csiro_nem_data.py' first",
            file=sys.stderr,
        )
        return 1

    SPIKE_OUTPUT_DIR.mkdir(exist_ok=True)
    net, warnings = load_case(DATA_FILE)
    if warnings:
        print(f"powerio conversion warnings ({len(warnings)}): {warnings}")

    pp_result = run_pandapower(net)
    sb_result = run_pypowsybl(net, MAT_FILE)

    loss_diff_pct = (
        abs(pp_result["total_loss_mw"] - sb_result["total_loss_mw"])
        / pp_result["total_loss_mw"]
        * 100
    )
    gen_diff_pct = (
        abs(pp_result["total_gen_mw"] - sb_result["total_gen_mw"])
        / pp_result["total_gen_mw"]
        * 100
    )

    comparison = {
        "case": DATA_FILE.name,
        "buses": len(net.bus),
        "branches": len(net.line) + len(net.trafo),
        "pandapower": pp_result,
        "pypowsybl": sb_result,
        "total_loss_diff_pct": loss_diff_pct,
        "total_gen_diff_pct": gen_diff_pct,
    }
    RESULT_FILE.write_text(json.dumps(comparison, indent=2))

    print(f"{DATA_FILE.name}: {len(net.bus)} buses, {len(net.line) + len(net.trafo)} branches")
    print(
        f"pandapower : gen={pp_result['total_gen_mw']:.2f} MW  "
        f"load={pp_result['total_load_mw']:.2f} MW  "
        f"loss={pp_result['total_loss_mw']:.3f} MW  solved in {pp_result['solve_s']:.3f}s"
    )
    print(
        f"pypowsybl  : gen={sb_result['total_gen_mw']:.2f} MW  "
        f"load={sb_result['total_load_mw']:.2f} MW  "
        f"loss={sb_result['total_loss_mw']:.3f} MW  solved in {sb_result['solve_s']:.3f}s "
        f"(slack mismatch {sb_result['slack_mismatch_mw']:.3f} MW)"
    )
    print(f"total loss diff: {loss_diff_pct:.3f}%   total gen diff: {gen_diff_pct:.3f}%")

    if loss_diff_pct > LOSS_TOLERANCE_PCT:
        print(f"FAIL: total loss diff {loss_diff_pct:.3f}% exceeds {LOSS_TOLERANCE_PCT}% tolerance")
        return 1

    if args.step == "check":
        if not FIXTURE_FILE.exists():
            print(f"FAIL: no fixture at {FIXTURE_FILE} -- run --step run first and commit it")
            return 1
        expected = json.loads(FIXTURE_FILE.read_text())
        drift = abs(expected["total_loss_diff_pct"] - loss_diff_pct)
        if drift > FIXTURE_DRIFT_TOLERANCE_PCT:
            print(
                f"FAIL: loss diff drifted from fixture "
                f"({expected['total_loss_diff_pct']:.3f}% -> {loss_diff_pct:.3f}%, "
                f"drift {drift:.3f}% > {FIXTURE_DRIFT_TOLERANCE_PCT}%)"
            )
            return 1
        print("MATCH")

    print(
        f"PASS: pypowsybl and pandapower agree on {DATA_FILE.name} within "
        f"{LOSS_TOLERANCE_PCT}% total-loss tolerance"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
