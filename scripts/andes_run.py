#!/usr/bin/env python3
"""Run an ANDES power flow and write bus voltages as JSON (#162). Runs inside containers/andes; the host never imports ANDES.

Usage: andes_run.py [--case FILE] [--out DIR]

Without --case, ANDES's own bundled IEEE 14-bus case is used (`ieee14/ieee14.raw`). Writes DIR/andes_pflow.json:
{"andes_version", "case", "converged", "bus": [...], "v_pu": [...], "angle_rad": [...], "sha256_of_case"} and prints one line
`PASS|FAIL andes-pflow: n=<buses> converged=<bool>`. Exit 0 converged, 1 not converged, 2 input or ANDES error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="andes_run.py", description="ANDES power flow to JSON")
    parser.add_argument("--case", metavar="FILE")
    parser.add_argument("--out", default="/out", metavar="DIR")
    args = parser.parse_args(argv)
    try:
        import andes

        case = args.case or andes.get_case("ieee14/ieee14.raw")
        ss = andes.run(case, routine="PFlow", no_output=True, default_config=True, verbose=40)
        if ss is None:
            raise RuntimeError("andes.run returned no system")
        converged = bool(ss.PFlow.converged)
        record = {
            "andes_version": andes.__version__, "case": Path(case).name, "converged": converged,
            "bus": [int(i) for i in ss.Bus.idx.v], "v_pu": [float(v) for v in ss.Bus.v.v],
            "angle_rad": [float(a) for a in ss.Bus.a.v],
            "sha256_of_case": hashlib.sha256(Path(case).read_bytes()).hexdigest(),
        }
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "andes_pflow.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"ERROR andes-pflow: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"{'PASS' if converged else 'FAIL'} andes-pflow: n={len(record['bus'])} converged={converged}")
    return 0 if converged else 1


if __name__ == "__main__":
    sys.exit(main())
