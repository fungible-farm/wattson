#!/usr/bin/env python3
"""Cross-check VeraGrid's AC power flow against the pandapower path the power tools use (#162).

Usage:
    python3 scripts/veragrid_crosscheck.py --case snemSA.m [--scale 1.0] [--tol 1e-4] [--bus 2008]

Both sides read the same MATPOWER file. The pandapower side is `wattson.gridfit.load_case` plus the `load_scale` operation of
`wattson.inference.studies` (every load's p_mw and q_mvar times the scale) and `pm._runpp_counted`'s Newton-Raphson. The VeraGrid
side opens the file with `VeraGridEngine.IO.FileOpen`, multiplies every load's P and Q by the same scale and runs the
Newton-Raphson `PowerFlowDriver`. Bus voltage magnitudes are matched by position (a count mismatch is an error) and compared by
absolute per-unit difference only; floating-point solvers never agree to the last digit, so the verdict is `max_abs_dv <= tol`.

Prints `PASS|FAIL veragrid-crosscheck: n=<buses> max_abs_dv=<x> mean_abs_dv=<x> worst_bus=<id> pp_converged=<bool>
vg_converged=<bool>` and, with --bus, `bus <ID>: pandapower=<v> veragrid=<v>`. Exit 0 pass, 1 fail, 2 unreadable input or a solver
that errored.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Any, Sequence

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

DEFAULT_TOL = 1e-4  # per-unit, absolute: far above solver noise (1e-8), far below any physical difference worth reporting


class CrosscheckError(Exception):
    """An input could not be read or a solver errored."""


def compare_voltages(a: Sequence[float], b: Sequence[float], tol: float) -> dict[str, Any]:
    """Absolute per-unit differences between two equal-length voltage lists; `passed` means max_abs_diff <= tol."""
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    if not a:
        raise ValueError("no voltages to compare")
    diffs = [abs(x - y) for x, y in zip(a, b)]
    if any(math.isnan(d) for d in diffs):
        worst = next(i for i, d in enumerate(diffs) if math.isnan(d))
        return {"n": len(diffs), "max_abs_diff": math.nan, "mean_abs_diff": math.nan, "worst_index": worst, "passed": False}
    worst = max(range(len(diffs)), key=diffs.__getitem__)
    return {"n": len(diffs), "max_abs_diff": diffs[worst], "mean_abs_diff": sum(diffs) / len(diffs),
            "worst_index": worst, "passed": diffs[worst] <= tol}


def pandapower_side(case: str, scale: float) -> tuple[list[Any], list[float], bool]:
    """Bus ids, voltage magnitudes (pu) and converged flag from the pandapower path the tools use."""
    from wattson.gridfit import load_case
    from wattson.inference import powermcp as pm

    try:
        net, _notes = load_case(case)
        net.load.loc[:, ["p_mw", "q_mvar"]] *= scale
        pm._runpp_counted(net, numba=True, max_iteration=50)
    except Exception as exc:  # solver or parser trouble is an input/solver error, not a verdict
        raise CrosscheckError(f"pandapower: {type(exc).__name__}: {exc}") from exc
    return list(net.bus.index), [float(v) for v in net.res_bus.vm_pu], bool(net.converged)


def veragrid_side(case: str, scale: float) -> tuple[list[float], bool]:
    """Voltage magnitudes (pu) and converged flag from VeraGrid's Newton-Raphson power flow on the same file."""
    try:
        import numpy as np
        from VeraGridEngine.IO.file_open import FileOpen
        from VeraGridEngine.Simulations.PowerFlow.power_flow_driver import PowerFlowDriver
        from VeraGridEngine.Simulations.PowerFlow.power_flow_options import PowerFlowOptions
        from VeraGridEngine.enumerations import SolverType

        grid = FileOpen(case).open()
        for load in grid.loads:
            load.P *= scale
            load.Q *= scale
        driver = PowerFlowDriver(grid, PowerFlowOptions(solver_type=SolverType.NR, retry_with_other_methods=False))
        driver.run()
        results = driver.results
        return [float(v) for v in np.abs(results.voltage)], bool(results.converged)
    except Exception as exc:
        raise CrosscheckError(f"veragrid: {type(exc).__name__}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="veragrid_crosscheck.py", description=__doc__.split("\n")[0])
    parser.add_argument("--case", required=True, metavar="FILE.m")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL)
    parser.add_argument("--bus", metavar="ID", help="also print both solvers' voltage at this pandapower bus id")
    args = parser.parse_args(argv)
    if not os.path.isfile(args.case):
        print(f"ERROR veragrid-crosscheck: cannot read {args.case}", file=sys.stderr)
        return 2
    try:
        bus_ids, pp_vm, pp_ok = pandapower_side(args.case, args.scale)
        vg_vm, vg_ok = veragrid_side(args.case, args.scale)
        if len(pp_vm) != len(vg_vm):
            raise CrosscheckError(f"bus count differs: pandapower {len(pp_vm)}, veragrid {len(vg_vm)}")
        result = compare_voltages(pp_vm, vg_vm, args.tol)
    except CrosscheckError as exc:
        print(f"ERROR veragrid-crosscheck: {exc}", file=sys.stderr)
        return 2
    passed = result["passed"] and pp_ok and vg_ok
    print(f"{'PASS' if passed else 'FAIL'} veragrid-crosscheck: n={result['n']} max_abs_dv={result['max_abs_diff']:.3e} "
          f"mean_abs_dv={result['mean_abs_diff']:.3e} worst_bus={bus_ids[result['worst_index']]} "
          f"pp_converged={pp_ok} vg_converged={vg_ok}")
    if args.bus is not None:
        ids = [str(i) for i in bus_ids]
        if args.bus not in ids:
            print(f"ERROR veragrid-crosscheck: bus {args.bus} is not in the case", file=sys.stderr)
            return 2
        at = ids.index(args.bus)
        print(f"bus {args.bus}: pandapower={pp_vm[at]!r} veragrid={vg_vm[at]!r}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
