#!/usr/bin/env python3
"""Bid stack (supply curve) for one region at its highest-price interval, from NEMOSIS BIDDAYOFFER_D (price bands), BIDPEROFFER_D (band availability),
DISPATCHPRICE, DUDETAILSUMMARY and DISPATCHREGIONSUM parquet (all `just nemosis-fetch TABLE START END`).

Usage: python3 scripts/nem_bidstack.py [REGION]   (default SA1)

Energy offers only (BIDTYPE = 'ENERGY'). A unit's price bands come from its latest offer (latest OFFERDATE) for the market day; availability comes from the interval row.
Prints, for the interval with the region's highest RRP: total offered MW, MW offered at or below the RRP, MW offered at or below 0, and the region's DISPATCHABLEGENERATION for reference.
PASS requires a non-empty stack and NO unit whose price bands are not ascending (AEMO's offer rules require ascending bands: a deterministic data invariant, checked across the whole day).
The offered-versus-dispatched ratio is reported, not gated (ramp limits and constraints legitimately keep dispatch below offers). No model, no network.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sql(statement):
    out = subprocess.run(["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000",
                          "-v", f"{REPO}/out/nem:/data:ro,z", "-v", f"{REPO}/out/nemosis:/nemosis:ro,z", "localhost/wattson-nem:local", "query"],
                         input=statement, capture_output=True, text=True, timeout=900)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stdout + out.stderr)[-400:]}


def main():
    region = sys.argv[1] if len(sys.argv) > 1 else "SA1"
    if region not in ("NSW1", "QLD1", "SA1", "TAS1", "VIC1"):
        print(f"FAIL nem-bidstack: unknown region {region}")
        sys.exit(1)
    bands = " UNION ALL ".join(f"SELECT DUID, {i} AS band, PRICEBAND{i} AS price FROM latest" for i in range(1, 11))
    avail = " UNION ALL ".join(f"SELECT DUID, {i} AS band, BANDAVAIL{i} AS mw FROM at_t" for i in range(1, 11))
    common = f"""
      WITH u AS (SELECT DUID, arg_max(REGIONID, START_DATE) AS region FROM dudetailsummary GROUP BY DUID),
      peak AS (SELECT SETTLEMENTDATE AS t, RRP AS rrp FROM dispatchprice WHERE REGIONID = '{region}' AND INTERVENTION = 0 ORDER BY RRP DESC, SETTLEMENTDATE LIMIT 1),
      latest AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY DUID, SETTLEMENTDATE ORDER BY OFFERDATE DESC, VERSIONNO DESC) AS rn
                                FROM biddayoffer_d WHERE BIDTYPE = 'ENERGY') WHERE rn = 1),
      at_t AS (SELECT b.* FROM bidperoffer_d b JOIN peak ON b.INTERVAL_DATETIME = peak.t JOIN u ON u.DUID = b.DUID WHERE b.BIDTYPE = 'ENERGY' AND u.region = '{region}'),
      stack AS (SELECT p.DUID, p.band, p.price, a.mw FROM ({bands}) p JOIN ({avail}) a USING (DUID, band))"""
    r = sql(common + f""" SELECT peak.t, peak.rrp, count(DISTINCT stack.DUID) AS units, round(sum(stack.mw), 1) AS offered_mw,
        round(sum(CASE WHEN stack.price <= peak.rrp THEN stack.mw ELSE 0 END), 1) AS offered_at_or_below_rrp,
        round(sum(CASE WHEN stack.price <= 0 THEN stack.mw ELSE 0 END), 1) AS offered_at_or_below_zero,
        (SELECT DISPATCHABLEGENERATION FROM dispatchregionsum WHERE REGIONID = '{region}' AND INTERVENTION = 0 AND SETTLEMENTDATE = peak.t) AS dispatchable_generation_mw
        FROM stack, peak GROUP BY peak.t, peak.rrp""")
    if "error" in r:
        print(f"FAIL nem-bidstack: {r['error']}")
        sys.exit(1)
    if not r["rows"]:
        print("FAIL nem-bidstack: empty bid stack for the peak interval")
        sys.exit(1)
    print(" | ".join(r["columns"]))
    row = r["rows"][0]
    print(" | ".join(str(v) for v in row))
    ratio = (row[4] / row[6]) if row[6] else float("nan")
    # Ascending-band invariant, whole day, every region.
    inv = sql("""WITH latest AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY DUID, SETTLEMENTDATE ORDER BY OFFERDATE DESC, VERSIONNO DESC) AS rn
                                FROM biddayoffer_d WHERE BIDTYPE = 'ENERGY') WHERE rn = 1)
        SELECT count(*) AS offers, sum(CASE WHEN PRICEBAND1 > PRICEBAND2 OR PRICEBAND2 > PRICEBAND3 OR PRICEBAND3 > PRICEBAND4 OR PRICEBAND4 > PRICEBAND5 OR PRICEBAND5 > PRICEBAND6
          OR PRICEBAND6 > PRICEBAND7 OR PRICEBAND7 > PRICEBAND8 OR PRICEBAND8 > PRICEBAND9 OR PRICEBAND9 > PRICEBAND10 THEN 1 ELSE 0 END) AS non_ascending FROM latest""")
    if "error" in inv:
        print(f"FAIL nem-bidstack: {inv['error']}")
        sys.exit(1)
    offers, bad = inv["rows"][0]
    print(f"nem-bidstack: region={region} interval={row[0]} rrp={row[1]} offered_at_or_below_rrp/dispatchable_generation={ratio:.3f} offers_checked={offers} non_ascending={bad}")
    ok = bad == 0 and offers > 0
    print(f"{'PASS' if ok else 'FAIL'} nem-bidstack: {'every energy offer has ascending price bands' if ok else f'{bad} of {offers} offers have non-ascending price bands'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
