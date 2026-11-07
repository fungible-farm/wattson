#!/usr/bin/env python3
"""Inside the wattson-nem container: build the DuckDB file from the AEMO price-and-demand CSVs, and run one read-only SQL statement.

  nem_sql.py load  [--data /data]        read /data/PRICE_AND_DEMAND_*.csv, write /data/nem.duckdb, print row counts per region
  nem_sql.py query [--data /data]        read ONE SQL statement from stdin, run it read-only, print {"columns": [...], "rows": [...]} as JSON

Table `price_demand(region, settlement_ts, total_demand_mw, rrp, period_type)`: one row per region per 5-minute dispatch interval.
`settlement_ts` is the END of the interval (the file says 00:05 for the interval 00:00-00:05); `total_demand_mw` is in MW, `rrp` in $/MWh.
"""
import argparse
import glob
import json
import os
import sys

import duckdb


def load(data):
    files = sorted(glob.glob(os.path.join(data, "PRICE_AND_DEMAND_*.csv")))
    if not files:
        sys.exit(f"no PRICE_AND_DEMAND_*.csv in {data}")
    path = os.path.join(data, "nem.duckdb")
    if os.path.exists(path):
        os.remove(path)
    con = duckdb.connect(path)
    con.execute("""
        CREATE TABLE price_demand (
            region VARCHAR, settlement_ts TIMESTAMP, total_demand_mw DOUBLE, rrp DOUBLE, period_type VARCHAR)""")
    for f in files:
        con.execute(f"""INSERT INTO price_demand SELECT REGION, strptime(SETTLEMENTDATE, '%Y/%m/%d %H:%M:%S'),
                        TOTALDEMAND, RRP, PERIODTYPE FROM read_csv('{f}', header=true, all_varchar=true)""")
    for region, n, first, last in con.execute("SELECT region, count(*), min(settlement_ts), max(settlement_ts) FROM price_demand GROUP BY 1 ORDER BY 1").fetchall():
        print(f"{region}: {n} intervals, {first} .. {last}")
    con.close()


def query(data):
    sql = sys.stdin.read().strip().rstrip(";")
    if ";" in sql:
        sys.exit(json.dumps({"error": "one statement only"}))
    con = duckdb.connect(os.path.join(data, "nem.duckdb"), read_only=True)
    try:
        con.execute(sql)
        columns = [d[0] for d in con.description]
        rows = [[(v.isoformat(sep=" ") if hasattr(v, "isoformat") else v) for v in row] for row in con.fetchmany(200)]
        print(json.dumps({"columns": columns, "rows": rows}, default=str))
    except Exception as error:  # noqa: BLE001 - the caller scores the error text
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("load", "query"))
    parser.add_argument("--data", default="/data")
    args = parser.parse_args()
    {"load": load, "query": query}[args.command](args.data)
