#!/usr/bin/env python3
"""Unit lookup: map dispatch units (DUID) to region and station with NEMOSIS DUDETAILSUMMARY, and check the mapping covers the units that actually dispatched.

Inputs (parquet in out/nemosis): DISPATCHLOAD.parquet (`just nemosis-fetch DISPATCHLOAD START END`) and DUDETAILSUMMARY.parquet (`just nemosis-fetch DUDETAILSUMMARY START END`).
One deterministic SQL statement per figure, run read-only in the wattson-nem container (no network, no model). Prints per-region unit counts and cleared energy, then
`nem-unit-lookup: dispatched_duids=N mapped=M coverage=C` and PASS when coverage >= 0.99 (a tripwire for a broken join; the measured value is printed either way).
Cleared energy per interval is TOTALCLEARED (MW) * 5/60 (MWh). As an independent cross-check the AEMO price-and-demand CSVs (out/nem, the demo 3 data) give each region's demand energy for
the SAME window; the ratio cleared/demand is printed per region (generation also covers losses and interchange, so it is reported, not gated).
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOAD = "read_parquet('/nemosis/DISPATCHLOAD.parquet')"
DUD = "read_parquet('/nemosis/DUDETAILSUMMARY.parquet')"


def sql(statement):
    out = subprocess.run(["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000",
                          "-v", f"{REPO}/out/nem:/data:ro,z", "-v", f"{REPO}/out/nemosis:/nemosis:ro,z", "localhost/wattson-nem:local", "query"],
                         input=statement, capture_output=True, text=True, timeout=600)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stdout + out.stderr)[-300:]}


def columns(src):
    r = sql(f"SELECT column_name FROM (DESCRIBE SELECT * FROM {src})")
    return {row[0] for row in r["rows"]} if "error" not in r else r


def main():
    for src, need in ((LOAD, {"DUID", "TOTALCLEARED", "INTERVENTION"}), (DUD, {"DUID", "REGIONID", "STATIONID"})):
        cols = columns(src)
        if isinstance(cols, dict):
            print(f"FAIL nem-unit-lookup: {cols['error']}")
            sys.exit(1)
        if need - cols:
            print(f"FAIL nem-unit-lookup: {src} lacks {sorted(need - cols)}; has {sorted(cols)}")
            sys.exit(1)
    # DUDETAILSUMMARY keeps several rows per DUID over time (effective dates); take the latest per DUID.
    latest = (f"(SELECT DUID, arg_max(REGIONID, START_DATE) AS region, arg_max(STATIONID, START_DATE) AS station FROM {DUD} GROUP BY DUID)"
              if "START_DATE" in columns(DUD) else f"(SELECT DISTINCT DUID, REGIONID AS region, STATIONID AS station FROM {DUD})")
    by_region = sql(f"""SELECT coalesce(d.region, 'UNMAPPED') AS region, count(DISTINCT l.DUID) AS units, round(sum(l.TOTALCLEARED) * 5 / 60, 1) AS cleared_mwh
        FROM {LOAD} l LEFT JOIN {latest} d USING (DUID) WHERE l.INTERVENTION = 0 GROUP BY 1 ORDER BY 1""")
    cover = sql(f"""SELECT count(DISTINCT l.DUID) AS dispatched, count(DISTINCT d.DUID) AS mapped
        FROM {LOAD} l LEFT JOIN {latest} d USING (DUID) WHERE l.INTERVENTION = 0""")
    window = f"SELECT min(SETTLEMENTDATE) AS a, max(SETTLEMENTDATE) AS b FROM {LOAD}"
    w = sql(window)
    demand = {}
    if "error" not in w and w["rows"][0][0]:
        a, b = w["rows"][0]
        d = sql(f"SELECT region, round(sum(total_demand_mw) * 5 / 60, 1) FROM price_demand WHERE settlement_ts >= TIMESTAMP '{a}' AND settlement_ts <= TIMESTAMP '{b}' GROUP BY 1")
        demand = {r[0]: r[1] for r in d.get("rows", [])}
    for r in (by_region, cover):
        if "error" in r:
            print(f"FAIL nem-unit-lookup: {r['error']}")
            sys.exit(1)
    print(" | ".join(by_region["columns"]))
    for row in by_region["rows"]:
        extra = f" | demand_mwh={demand[row[0]]} ratio={row[2] / demand[row[0]]:.3f}" if demand.get(row[0]) else ""
        print(" | ".join(str(v) for v in row) + extra)
    dispatched, mapped = cover["rows"][0]
    coverage = mapped / dispatched if dispatched else 0.0
    print(f"nem-unit-lookup: dispatched_duids={dispatched} mapped={mapped} coverage={coverage:.4f}")
    ok = dispatched > 0 and coverage >= 0.99
    print(f"{'PASS' if ok else 'FAIL'} nem-unit-lookup: {'the unit-to-region mapping covers the dispatched units' if ok else 'the mapping misses more than 1% of dispatched units'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
