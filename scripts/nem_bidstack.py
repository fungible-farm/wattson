#!/usr/bin/env python3
"""Bid stack (supply curve) for one region at its highest-price interval, from NEMOSIS BIDDAYOFFER_D (price bands), BIDPEROFFER_D (band availability),
DISPATCHPRICE, DUDETAILSUMMARY and DISPATCHREGIONSUM parquet (all `just nemosis-fetch TABLE START END`).

Usage: python3 scripts/nem_bidstack.py [REGION]   (default SA1)

Energy generation offers only (BIDTYPE = 'ENERGY', DIRECTION = 'GEN': batteries also bid a LOAD direction). The market day runs 04:00 to 04:00, so the interval belongs to the day of (interval - 4 h - 1 min).
A unit's price bands come from its latest offer (latest OFFERDATE, VERSIONNO) for that market day; availability comes from the interval row, capped by MAXAVAIL cumulatively through the bands (bids may sum above MAXAVAIL, which is what is actually available). The join is on (DUID, band): `dup_keys` counts keys with more than one row and must be 0.
Prints, for the interval with the region's highest RRP: total offered MW, MW offered at or below the RRP, MW offered at or below 0, and the region's DISPATCHABLEGENERATION for reference.
PASS requires a non-empty stack, one row per (unit, band), and NO unit whose price bands are not ascending (AEMO's offer rules require ascending bands: a deterministic data invariant, checked across the whole day).
The offered-versus-dispatched and offered-versus-AVAILABLEGENERATION (DISPATCHREGIONSUM, an independent figure) ratios are reported, not gated (ramp limits and constraints legitimately keep dispatch below offers). No model, no network.
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
    avail = " UNION ALL ".join(f"SELECT DUID, {i} AS band, BANDAVAIL{i} AS mw, MAXAVAIL AS maxavail FROM at_t" for i in range(1, 11))
    common = f"""
      WITH u AS (SELECT DUID, arg_max(REGIONID, START_DATE) AS region FROM dudetailsummary GROUP BY DUID),
      peak AS (SELECT SETTLEMENTDATE AS t, RRP AS rrp FROM dispatchprice WHERE REGIONID = '{region}' AND INTERVENTION = 0 ORDER BY RRP DESC, SETTLEMENTDATE LIMIT 1),
      md AS (SELECT date_trunc('day', t - INTERVAL 4 HOUR - INTERVAL 1 MINUTE) AS day FROM peak),
      latest AS (SELECT * FROM (SELECT d.*, row_number() OVER (PARTITION BY d.DUID ORDER BY d.OFFERDATE DESC, d.VERSIONNO DESC) AS rn
                                FROM biddayoffer_d d, md WHERE d.BIDTYPE = 'ENERGY' AND d.DIRECTION = 'GEN' AND d.SETTLEMENTDATE = md.day) WHERE rn = 1),
      at_t AS (SELECT b.* FROM bidperoffer_d b JOIN peak ON b.INTERVAL_DATETIME = peak.t JOIN md ON b.SETTLEMENTDATE = md.day JOIN u ON u.DUID = b.DUID
               WHERE b.BIDTYPE = 'ENERGY' AND b.DIRECTION = 'GEN' AND u.region = '{region}'),
      raw AS (SELECT p.DUID, p.band, p.price, a.mw AS bid_mw, a.maxavail FROM ({bands}) p JOIN ({avail}) a USING (DUID, band)),
      stack AS (SELECT DUID, band, price, bid_mw, greatest(0, least(bid_mw, maxavail - coalesce(sum(bid_mw) OVER (PARTITION BY DUID ORDER BY band ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0))) AS mw FROM raw)"""
    r = sql(common + f""" SELECT peak.t, peak.rrp, count(DISTINCT stack.DUID) AS units, round(sum(stack.mw), 1) AS offered_mw,
        round(sum(CASE WHEN stack.price <= peak.rrp THEN stack.mw ELSE 0 END), 1) AS offered_at_or_below_rrp,
        round(sum(CASE WHEN stack.price <= 0 THEN stack.mw ELSE 0 END), 1) AS offered_at_or_below_zero,
        (SELECT DISPATCHABLEGENERATION FROM dispatchregionsum WHERE REGIONID = '{region}' AND INTERVENTION = 0 AND SETTLEMENTDATE = peak.t) AS dispatchable_generation_mw,
        (SELECT AVAILABLEGENERATION FROM dispatchregionsum WHERE REGIONID = '{region}' AND INTERVENTION = 0 AND SETTLEMENTDATE = peak.t) AS available_generation_mw
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
    avail_ratio = (row[3] / row[7]) if row[7] else float("nan")
    dup = sql(common + " SELECT count(*) FROM (SELECT DUID, band FROM stack GROUP BY 1, 2 HAVING count(*) > 1)")
    if "error" in dup:
        print(f"FAIL nem-bidstack: {dup['error']}")
        sys.exit(1)
    dup_keys = dup["rows"][0][0]
    by_type = sql(common + f""" SELECT coalesce(d.SCHEDULE_TYPE, 'UNKNOWN') AS schedule_type, count(DISTINCT stack.DUID) AS units, round(sum(stack.mw), 1) AS offered_mw
        FROM stack LEFT JOIN (SELECT DUID, arg_max(SCHEDULE_TYPE, START_DATE) AS SCHEDULE_TYPE FROM dudetailsummary GROUP BY DUID) d USING (DUID) GROUP BY 1 ORDER BY 1""")
    if "error" not in by_type:
        print("offered MW by schedule type: " + "; ".join(f"{r[0]}: {r[1]} units {r[2]} MW" for r in by_type["rows"]))
    # Ascending-band invariant, whole day, every region.
    inv = sql("""WITH latest AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY DUID, SETTLEMENTDATE ORDER BY OFFERDATE DESC, VERSIONNO DESC) AS rn
                                FROM biddayoffer_d WHERE BIDTYPE = 'ENERGY') WHERE rn = 1)
        SELECT count(*) AS offers, sum(CASE WHEN PRICEBAND1 > PRICEBAND2 OR PRICEBAND2 > PRICEBAND3 OR PRICEBAND3 > PRICEBAND4 OR PRICEBAND4 > PRICEBAND5 OR PRICEBAND5 > PRICEBAND6
          OR PRICEBAND6 > PRICEBAND7 OR PRICEBAND7 > PRICEBAND8 OR PRICEBAND8 > PRICEBAND9 OR PRICEBAND9 > PRICEBAND10 THEN 1 ELSE 0 END) AS non_ascending FROM latest""")
    if "error" in inv:
        print(f"FAIL nem-bidstack: {inv['error']}")
        sys.exit(1)
    offers, bad = inv["rows"][0]
    print(f"nem-bidstack: region={region} interval={row[0]} rrp={row[1]} offered_at_or_below_rrp/dispatchable_generation={ratio:.3f} offered/available_generation={avail_ratio:.3f} offers_checked={offers} non_ascending={bad} dup_keys={dup_keys}")
    ok = bad == 0 and offers > 0 and dup_keys == 0
    print(f"{'PASS' if ok else 'FAIL'} nem-bidstack: {'every energy offer has ascending price bands and the stack has one row per unit and band' if ok else f'{bad} of {offers} offers non-ascending, {dup_keys} duplicated unit-band keys'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
