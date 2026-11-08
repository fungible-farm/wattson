#!/usr/bin/env python3
"""Inside the wattson-nem container: build the DuckDB file from the AEMO price-and-demand CSVs, and run one read-only SQL statement.

  nem_sql.py load  [--data /data]        read /data/PRICE_AND_DEMAND_*.csv, write /data/nem.duckdb, print row counts per region
  nem_sql.py export [--src /nemosis] [--out /nemosis/csv]   write the five multi-table NEMOSIS parquet files as CSV (selected columns) for loading into Postgres
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


EXPORT = {
    "dispatchload": ["SETTLEMENTDATE", "DUID", "INTERVENTION", "TOTALCLEARED", "AVAILABILITY"],
    "dudetailsummary": ["DUID", "START_DATE", "END_DATE", "REGIONID", "STATIONID", "PARTICIPANTID", "DISPATCHTYPE", "SCHEDULE_TYPE"],
    "dispatchprice": ["SETTLEMENTDATE", "REGIONID", "INTERVENTION", "RRP"],
    "dispatchregionsum": ["SETTLEMENTDATE", "REGIONID", "INTERVENTION", "TOTALDEMAND", "NETINTERCHANGE"],
    "dispatchinterconnectorres": ["SETTLEMENTDATE", "INTERCONNECTORID", "INTERVENTION", "MWFLOW", "MWLOSSES"],
}


def export(src, out):
    """parquet -> CSV with ISO timestamps and the selected columns; prints `<table> rows=N` so the loader can check counts."""
    os.makedirs(out, exist_ok=True)
    con = duckdb.connect()
    for table, cols in EXPORT.items():
        path = os.path.join(src, table.upper() + ".parquet")
        select = ", ".join(f"strftime({c}, '%Y-%m-%d %H:%M:%S') AS {c}" if c in ("SETTLEMENTDATE", "START_DATE", "END_DATE") else c for c in cols)
        n = con.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
        con.execute(f"COPY (SELECT {select} FROM read_parquet('{path}')) TO '{os.path.join(out, table + '.csv')}' (HEADER, DELIMITER ',')")
        print(f"{table} rows={n}")


def query(data):
    sql = sys.stdin.read().strip().rstrip(";")
    if ";" in sql:
        sys.exit(json.dumps({"error": "one statement only"}))
    con = duckdb.connect(os.path.join(data, "nem.duckdb"), read_only=True)
    # Any NEMOSIS parquet mounted at /nemosis is exposed as a view named after the file (DISPATCHLOAD.parquet -> dispatchload), for multi-table questions.
    for path in sorted(glob.glob("/nemosis/*.parquet")):
        con.execute(f"CREATE TEMP VIEW {os.path.basename(path)[:-8].lower()} AS SELECT * FROM read_parquet('{path}')")
    try:
        con.execute(sql)
        columns = [d[0] for d in con.description]
        rows = [[(v.isoformat(sep=" ") if hasattr(v, "isoformat") else v) for v in row] for row in con.fetchmany(200)]
        print(json.dumps({"columns": columns, "rows": rows}, default=str))
    except Exception as error:  # noqa: BLE001 - the caller scores the error text
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("load", "query", "export"))
    parser.add_argument("--data", default="/data")
    parser.add_argument("--src", default="/nemosis")
    parser.add_argument("--out", default="/nemosis/csv")
    args = parser.parse_args()
    {"load": lambda: load(args.data), "query": lambda: query(args.data), "export": lambda: export(args.src, args.out)}[args.command]()
