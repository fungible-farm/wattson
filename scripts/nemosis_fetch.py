#!/usr/bin/env python3
"""Inside the wattson-nemosis container: fetch ONE AEMO MMS table for a time range with NEMOSIS and write parquet plus a provenance record.

  nemosis_fetch.py fetch --table DISPATCHPRICE --start "2025/09/01 00:00:00" --end "2025/09/02 00:00:00" [--filter REGIONID=SA1,NSW1] [--out /data] [--cache /cache]
  nemosis_fetch.py tables     list the table names NEMOSIS knows (dynamic and static)

Output: <out>/<TABLE>.parquet and <out>/<TABLE>.provenance.json {table, start, end, filter, nemosis_version, rows, sha256}. No inference anywhere.
"""
import argparse
import hashlib
import json
import os
import sys


def tables():
    from nemosis import defaults
    print(json.dumps({"dynamic": sorted(defaults.dynamic_tables), "static": sorted(defaults.static_tables)}))


def fetch(args):
    import importlib.metadata as md
    from nemosis import dynamic_data_compiler
    kwargs = {}
    if args.filter:
        col, _, vals = args.filter.partition("=")
        kwargs = {"filter_cols": [col], "filter_values": ([v for v in vals.split(",") if v],)}
    os.makedirs(args.cache, exist_ok=True)
    df = dynamic_data_compiler(args.start, args.end, args.table, args.cache, **kwargs)
    path = os.path.join(args.out, f"{args.table}.parquet")
    df.to_parquet(path, index=False)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    record = {"table": args.table, "start": args.start, "end": args.end, "filter": args.filter, "nemosis_version": md.version("nemosis"),
              "rows": int(len(df)), "columns": list(map(str, df.columns)), "sha256": digest}
    json.dump(record, open(os.path.join(args.out, f"{args.table}.provenance.json"), "w"), indent=1)
    print(f"nemosis-fetch: {args.table} rows={len(df)} sha256={digest[:16]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tables")
    f = sub.add_parser("fetch")
    f.add_argument("--table", required=True)
    f.add_argument("--start", required=True)
    f.add_argument("--end", required=True)
    f.add_argument("--filter", default=None, help="COL=V1,V2")
    f.add_argument("--out", default="/data")
    f.add_argument("--cache", default="/cache")
    a = parser.parse_args()
    tables() if a.cmd == "tables" else fetch(a)
