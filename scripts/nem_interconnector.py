#!/usr/bin/env python3
"""Interconnector flow and congestion from NEMOSIS DISPATCHINTERCONNECTORRES parquet (`just nemosis-fetch DISPATCHINTERCONNECTORRES ...`).

For each interconnector: intervals, mean and peak flow each way, and the number of intervals AT a limit (|flow| within 0.5 MW of EXPORTLIMIT for flow>0, or of IMPORTLIMIT for flow<0).
All numbers come from one deterministic SQL statement run read-only in the wattson-nem container (no network, no model). Prints
`nem-interconnector: interconnectors=N intervals=M` and PASS when the table is non-empty and every interconnector has the same interval count.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = "read_parquet('/nemosis/DISPATCHINTERCONNECTORRES.parquet')"


def sql(statement):
    out = subprocess.run(["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000",
                          "-v", f"{REPO}/out/nem:/data:ro,z", "-v", f"{REPO}/out/nemosis:/nemosis:ro,z", "localhost/wattson-nem:local", "query"],
                         input=statement, capture_output=True, text=True, timeout=300)
    try:
        result = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stdout + out.stderr)[-300:]}
    return result


def main():
    cols = sql(f"SELECT column_name FROM (DESCRIBE SELECT * FROM {SRC})")
    if "error" in cols:
        print(f"FAIL nem-interconnector: {cols['error']}")
        sys.exit(1)
    names = {r[0] for r in cols["rows"]}
    need = {"INTERCONNECTORID", "SETTLEMENTDATE", "MWFLOW", "EXPORTLIMIT", "IMPORTLIMIT"}
    if need - names:
        print(f"FAIL nem-interconnector: parquet lacks {sorted(need - names)}; has {sorted(names)}")
        sys.exit(1)
    flt = "WHERE INTERVENTION = 0" if "INTERVENTION" in names else ""
    result = sql(f"""SELECT INTERCONNECTORID AS interconnector, count(*) AS intervals,
        round(avg(MWFLOW), 1) AS mean_mw, round(max(MWFLOW), 1) AS max_forward_mw, round(min(MWFLOW), 1) AS max_reverse_mw,
        sum(CASE WHEN MWFLOW > 0 AND MWFLOW >= EXPORTLIMIT - 0.5 THEN 1 ELSE 0 END) AS at_forward_limit,
        sum(CASE WHEN MWFLOW < 0 AND MWFLOW <= -IMPORTLIMIT + 0.5 THEN 1 ELSE 0 END) AS at_reverse_limit
        FROM {SRC} {flt} GROUP BY 1 ORDER BY 1""")
    if "error" in result:
        print(f"FAIL nem-interconnector: {result['error']}")
        sys.exit(1)
    print(" | ".join(result["columns"]))
    for row in result["rows"]:
        print(" | ".join(str(v) for v in row))
    counts = {r[1] for r in result["rows"]}
    ok = bool(result["rows"]) and len(counts) == 1
    print(f"nem-interconnector: interconnectors={len(result['rows'])} intervals={sorted(counts)}")
    print(f"{'PASS' if ok else 'FAIL'} nem-interconnector: {'one consistent interval count per interconnector' if ok else 'empty table or unequal interval counts'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
