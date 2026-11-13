#!/usr/bin/env python3
"""Build (or check) the governed views of the decomposition candidate (#128).

Nothing runs on the host: the CREATE VIEW statements in benchmarks/ontology/decomp/views.sql are executed through
arm1.run_sql, i.e. inside the same no-network container (localhost/wattson-nem:local) the harness uses for every query,
against out/nem/nem.duckdb. Inside that container the five parquet files are exposed as TEMP views named after the raw
tables, so each statement here is prefixed with those temp-view definitions and the file paths stay container-side.

    python3 scripts/ontology_eval/decomp_setup.py           # create/replace the governed views
    python3 scripts/ontology_eval/decomp_setup.py --check    # report which governed views exist
"""
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import demo_timeseries_qa as arm1  # noqa: E402

VIEWS_SQL = os.path.join(REPO, "benchmarks", "ontology", "decomp", "views.sql")
IMAGE = os.environ.get("NEM_IMAGE", arm1.IMAGE)
TABLES = ["dispatchload", "dudetailsummary", "dispatchprice", "dispatchregionsum", "dispatchinterconnectorres"]
VIEW_NAMES = ["interval_energy", "unit_dim", "unit_region", "price_interval", "interchange_interval", "region_reserve",
              "day_interval"]
TEMP_VIEWS = "; ".join(f'CREATE OR REPLACE TEMP VIEW "{t}" AS SELECT * FROM read_parquet(\'/nemosis/{t.upper()}.parquet\')'
                       for t in TABLES)


def statements():
    """views.sql split into statements on ';' outside quotes, with line comments removed."""
    text = re.sub(r"(?m)^\s*--.*$", "", open(VIEWS_SQL).read())
    out, buf, quote = [], [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def build():
    """Run views.sql inside the query container (write mode) via the image's own python+duckdb; the host never opens the parquet.

    arm1.run_sql attaches out/nem/nem.duckdb read-only and takes exactly one statement, so the governed views cannot be
    created through it; this is the one write path, the same image and the same file the harness reads.
    """
    prog = ("import duckdb, json\n"
            "con = duckdb.connect('/data/nem.duckdb')\n"
            + "".join(f"con.execute('CREATE OR REPLACE TEMP VIEW \"{t}\" AS SELECT * FROM "
                      f"read_parquet(\\\"/nemosis/{t.upper()}.parquet\\\")')\n" for t in TABLES)
            + f"stmts = {statements()!r}\n"
            "bad = []\n"
            "for i, s in enumerate(stmts):\n"
            "    try:\n        con.execute(s)\n"
            "    except Exception as e:\n        bad.append((i, s[:60], str(e).splitlines()[0][:200]))\n"
            "got = [r[0] for r in con.execute(\"select table_name from information_schema.tables where "
            "table_schema='main' order by 1\").fetchall()]\n"
            "print(json.dumps({'bad': bad, 'views': got}))\n")
    cmd = ["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000",
           "-v", f"{arm1.DATA}:/data:z", "-v", f"{os.path.join(REPO, 'out', 'nemosis')}:/nemosis:ro,z",
           "--entrypoint", "python", IMAGE, "-c", prog]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    line = [l for l in (out.stdout or "").strip().splitlines() if l.startswith("{")]
    if not line:
        return {"error": (out.stdout + out.stderr)[-400:]}
    return json.loads(line[-1])


def main():
    check = "--check" in sys.argv
    if not check:
        r = build()
        print(json.dumps(r, indent=1)[:4000])
        return 1 if "error" in r or r.get("bad") else 0
    if check:
        r = arm1.run_sql("select table_name from information_schema.tables where table_schema='main' and "
                         "table_name in (" + ",".join("'" + v + "'" for v in VIEW_NAMES) + ") order by 1")
        have = [x[0] for x in r.get("rows") or []] if "error" not in r else []
        print(json.dumps({"views_present": have, "views_missing": [v for v in VIEW_NAMES if v not in have],
                          "error": r.get("error", "")}, indent=1))
        return 0 if len(have) == len(VIEW_NAMES) else 1
    bad = []
    for i, stmt in enumerate(statements()):
        r = arm1.run_sql(TEMP_VIEWS + "; " + stmt)
        if "error" in r:
            bad.append((i, stmt[:60], r["error"][-300:]))
    r = arm1.run_sql("select table_name, count(*) from (select table_name from information_schema.tables "
                     "where table_schema='main') group by 1 order by 1")
    print("built:", not bad, "| in catalog:", json.dumps(r.get("rows")))
    for b in bad:
        print("FAIL", b[0], b[1], "|", b[2])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
