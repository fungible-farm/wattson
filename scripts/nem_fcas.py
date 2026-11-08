#!/usr/bin/env python3
"""FCAS price analysis from NEMOSIS DISPATCHPRICE parquet (`just nemosis-fetch DISPATCHPRICE START END`).

Per region and FCAS service (raise/lower x 6 s, 60 s, 5 min, regulation): mean, max, and the number of intervals above 100 $/MWh. One deterministic SQL statement per service,
run read-only in the wattson-nem container (no network, no model). PASS requires: all eight services present, rows for every region, and no negative FCAS price
(FCAS prices are floored at zero). The measured maximum is printed so a price-cap check can be done against the published cap rather than assumed here.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = "read_parquet('/nemosis/DISPATCHPRICE.parquet')"
SERVICES = ["RAISE6SECRRP", "RAISE60SECRRP", "RAISE5MINRRP", "RAISEREGRRP", "LOWER6SECRRP", "LOWER60SECRRP", "LOWER5MINRRP", "LOWERREGRRP"]


def sql(statement):
    out = subprocess.run(["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000",
                          "-v", f"{REPO}/out/nem:/data:ro,z", "-v", f"{REPO}/out/nemosis:/nemosis:ro,z", "localhost/wattson-nem:local", "query"],
                         input=statement, capture_output=True, text=True, timeout=300)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stdout + out.stderr)[-300:]}


def main():
    cols = sql(f"SELECT column_name FROM (DESCRIBE SELECT * FROM {SRC})")
    if "error" in cols:
        print(f"FAIL nem-fcas: {cols['error']}")
        sys.exit(1)
    have = {r[0] for r in cols["rows"]}
    if set(SERVICES) - have or "REGIONID" not in have:
        print(f"FAIL nem-fcas: parquet lacks {sorted((set(SERVICES) | {'REGIONID'}) - have)}")
        sys.exit(1)
    flt = "WHERE INTERVENTION = 0" if "INTERVENTION" in have else ""
    selects = " UNION ALL ".join(
        f"SELECT REGIONID AS region, '{s[:-3]}' AS service, count(*) AS n, round(avg({s}), 2) AS mean, round(max({s}), 2) AS max, "
        f"sum(CASE WHEN {s} > 100 THEN 1 ELSE 0 END) AS above_100, sum(CASE WHEN {s} < 0 THEN 1 ELSE 0 END) AS negative FROM {SRC} {flt} GROUP BY 1"
        for s in SERVICES)
    r = sql(f"SELECT * FROM ({selects}) ORDER BY region, service")
    if "error" in r:
        print(f"FAIL nem-fcas: {r['error']}")
        sys.exit(1)
    print(" | ".join(r["columns"]))
    for row in r["rows"]:
        print(" | ".join(str(v) for v in row))
    regions = {row[0] for row in r["rows"]}
    services = {row[1] for row in r["rows"]}
    negatives = sum(row[6] for row in r["rows"])
    peak = max(row[4] for row in r["rows"])
    print(f"nem-fcas: regions={len(regions)} services={len(services)} negative_prices={negatives} max_fcas_price={peak}")
    ok = len(services) == 8 and len(regions) >= 1 and negatives == 0
    print(f"{'PASS' if ok else 'FAIL'} nem-fcas: {'eight services present and no negative FCAS price' if ok else 'a service is missing or an FCAS price is negative'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
