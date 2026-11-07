#!/usr/bin/env python3
"""Reconcile NEMOSIS DISPATCHPRICE (parquet from `just nemosis-fetch`) against the AEMO price-and-demand CSVs already loaded in out/nem/nem.duckdb (demo 3).

The AEMO CSVs round rrp to cents, so a half-cent difference (0.005, which floating point can make 0.00500000000000078) is rounding, not disagreement: the tolerance is 0.0051.
Two independent routes to the same published prices must agree. Prints `nemosis-reconcile: region=SA1 joined=N differing=D` and PASS only when N>0 and D==0.
Runs the SQL inside the wattson-nem container (read-only mounts, no network).
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL = ("SELECT count(*) AS joined, coalesce(sum(CASE WHEN abs(a.rrp - b.RRP) > 0.0051 THEN 1 ELSE 0 END), 0) AS differing "
       "FROM price_demand a JOIN read_parquet('/nemosis/DISPATCHPRICE.parquet') b "
       "ON a.region = b.REGIONID AND a.settlement_ts = b.SETTLEMENTDATE WHERE b.INTERVENTION = 0")
out = subprocess.run(["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000",
                      "-v", f"{REPO}/out/nem:/data:ro,z", "-v", f"{REPO}/out/nemosis:/nemosis:ro,z", "localhost/wattson-nem:local", "query"],
                     input=SQL, capture_output=True, text=True, timeout=300)
try:
    result = json.loads(out.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    print(f"FAIL nemosis-reconcile: {(out.stdout + out.stderr)[-300:]}")
    sys.exit(1)
if "error" in result:
    print(f"FAIL nemosis-reconcile: {result['error']}")
    sys.exit(1)
joined, differing = result["rows"][0]
print(f"nemosis-reconcile: joined={joined} differing={differing}")
ok = joined > 0 and differing == 0
print(f"{'PASS' if ok else 'FAIL'} nemosis-reconcile: NEMOSIS and the AEMO CSVs {'agree' if ok else 'do not agree'} on rrp")
sys.exit(0 if ok else 1)
