#!/usr/bin/env python3
"""Solve an OpenDSS circuit and write bus voltages as JSON (#162). Runs inside containers/opendss; nothing else imports OpenDSSDirect.

Usage: opendss_run.py [--dss FILE] [--out DIR]

Without --dss, a small built-in radial feeder is solved (source, one line, one 100 kW 0.95 pf load). Writes DIR/opendss_solve.json:
{"opendssdirect_version", "source", "converged", "bus": [...], "vmag_pu": [...]} and prints
`PASS|FAIL opendss-solve: n=<buses> converged=<bool>`. Exit 0 converged, 1 not converged, 2 input or OpenDSS error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BUILTIN = """clear
new circuit.feeder basekv=11 pu=1.0 phases=3 bus1=src
new line.l1 bus1=src bus2=load phases=3 length=2 units=km r1=0.2 x1=0.3 r0=0.6 x0=0.9
new load.ld1 bus1=load phases=3 kv=11 kw=100 pf=0.95 model=1
set voltagebases=[11]
calcvoltagebases
solve
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opendss_run.py", description="OpenDSS solve to JSON")
    parser.add_argument("--dss", metavar="FILE")
    parser.add_argument("--out", default="/out", metavar="DIR")
    args = parser.parse_args(argv)
    try:
        import opendssdirect as dss

        text = Path(args.dss).read_text(encoding="utf-8") if args.dss else BUILTIN
        dss.Basic.ClearAll()
        for line in text.splitlines():
            if line.strip():
                dss.Text.Command(line.strip())
        converged = bool(dss.Solution.Converged())
        record = {
            "opendssdirect_version": dss.__version__, "source": Path(args.dss).name if args.dss else "builtin",
            "converged": converged, "bus": list(dss.Circuit.AllBusNames()),
            "vmag_pu": [float(v) for v in dss.Circuit.AllBusMagPu()],
        }
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "opendss_solve.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"ERROR opendss-solve: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"{'PASS' if converged else 'FAIL'} opendss-solve: n={len(record['bus'])} converged={converged}")
    return 0 if converged else 1


if __name__ == "__main__":
    sys.exit(main())
