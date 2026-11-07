#!/usr/bin/env python3
"""Demo 3 (#100), arm 1: natural-language questions over relational TIME SERIES, plain text-to-SQL (schema in the prompt).

Data: the public AEMO aggregated price-and-demand files for September 2025, five regions, 5-minute intervals (8640 per region), loaded
into DuckDB by `just nem-data` (a container fetches and checksums the CSVs; another builds out/nem/nem.duckdb). Ten questions with
ground truth computed by a deterministic SQL written BEFORE any model runs. This arm gives a model the table definition and its
semantics and asks for ONE DuckDB statement; the statement is executed read-only in the `wattson-nem` container (no network) and
the result is compared with the ground truth (numbers with a tolerance, text exactly). Per question: PASS only if every expected value is
in the returned rows. Arms 2 (Auto Ontology) and 3 (a semantic layer) are #95; the owner sets the PASS thresholds and the question set.

Two questions are traps for LLM SQL: `settlement_ts` is the END of the interval (so "15 September" is the intervals ending 00:05 on the
15th through 00:00 on the 16th), and a 4-hour evening window is 48 intervals ending 17:05 to 21:00.
Usage: python3 scripts/demo_timeseries_qa.py [--only Q1,Q6] [--base-url URL --model NAME]
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "out", "nem")
IMAGE = "localhost/wattson-nem:local"

SCHEMA = """Table price_demand(region VARCHAR, settlement_ts TIMESTAMP, total_demand_mw DOUBLE, rrp DOUBLE, period_type VARCHAR)
- one row per region per 5-minute dispatch interval; regions are NSW1, QLD1, SA1, TAS1, VIC1
- settlement_ts is the END of the interval: 00:05 means the interval 00:00 to 00:05
- total_demand_mw is demand in MW; rrp is the regional reference price in $/MWh (it can be negative)
- the data covers 2025-09-01 00:05:00 to 2025-10-01 00:00:00"""
PROMPT = ("You write DuckDB SQL for this table.\n" + SCHEMA + "\nAnswer the question with exactly ONE SQL statement in a ```sql block and no "
          "explanation. Return the values the question asks for, one column each.\nQuestion: ")

# id, question, ground-truth SQL (a deterministic statement over the same table; its result is the expected answer)
QUESTIONS = [
    ("Q1", "What was the peak total demand in SA1 in September 2025 (MW), and at which settlement time did it occur?",
     "SELECT max(total_demand_mw), arg_max(settlement_ts, total_demand_mw) FROM price_demand WHERE region = 'SA1'"),
    ("Q2", "How many 5-minute intervals in SA1 had a price (rrp) above 300 $/MWh?",
     "SELECT count(*) FROM price_demand WHERE region = 'SA1' AND rrp > 300"),
    ("Q3", "How many 5-minute intervals in NSW1 had a negative price?",
     "SELECT count(*) FROM price_demand WHERE region = 'NSW1' AND rrp < 0"),
    ("Q4", "What was the simple average price (rrp) in VIC1 over September 2025?",
     "SELECT avg(rrp) FROM price_demand WHERE region = 'VIC1'"),
    ("Q5", "What was the demand-weighted average price in QLD1 for September 2025 (sum of price times demand divided by sum of demand)?",
     "SELECT sum(rrp * total_demand_mw) / sum(total_demand_mw) FROM price_demand WHERE region = 'QLD1'"),
    ("Q6", "What was the average total demand in NSW1 over the 48 intervals covering 17:00 to 21:00 on 15 September 2025 (the intervals ending 17:05 up to and including 21:00)?",
     "SELECT avg(total_demand_mw) FROM price_demand WHERE region = 'NSW1' AND settlement_ts > TIMESTAMP '2025-09-15 17:00:00' AND settlement_ts <= TIMESTAMP '2025-09-15 21:00:00'"),
    ("Q7", "Which region had the highest simple average price over September 2025?",
     "SELECT region FROM price_demand GROUP BY region ORDER BY avg(rrp) DESC LIMIT 1"),
    ("Q8", "How many 5-minute intervals in NSW1 had total demand above 8000 MW?",
     "SELECT count(*) FROM price_demand WHERE region = 'NSW1' AND total_demand_mw > 8000"),
    ("Q9", "What was the average total demand in NSW1 on the market day of 15 September 2025 (all 288 intervals of that day: those ending 00:05 on the 15th through 00:00 on the 16th)?",
     "SELECT avg(total_demand_mw) FROM price_demand WHERE region = 'NSW1' AND settlement_ts > TIMESTAMP '2025-09-15 00:00:00' AND settlement_ts <= TIMESTAMP '2025-09-16 00:00:00'"),
    ("Q10", "What was the highest price (rrp) seen in any region in September 2025, in which region, and at which settlement time?",
     "SELECT rrp, region, settlement_ts FROM price_demand ORDER BY rrp DESC, settlement_ts LIMIT 1"),
]


def fail(msg):
    print(f"FAIL demo-timeseries-qa: {msg}")
    sys.exit(1)


def run_sql(sql):
    """Execute ONE read-only statement in the container; return the parsed JSON ({"columns", "rows"} or {"error"})."""
    out = subprocess.run(["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000", "-v", f"{DATA}:/data:ro,z", IMAGE, "query"],
                         input=sql, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stdout + out.stderr)[-300:]}


def ask_model(base_url, model, question):
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 700, "messages": [{"role": "user", "content": PROMPT + question}]}).encode()
    req = urllib.request.Request(f"{base_url}/chat/completions", data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        reply = json.load(r)
    text = reply["choices"][0]["message"].get("content") or ""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    return (m.group(1) if m else text).strip(), text


def flat(rows):
    return [v for row in rows for v in row]


def same(expected, got):
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        return abs(expected - got) <= max(0.005, 1e-6 * abs(expected))
    return str(expected).strip().lower() == str(got).strip().lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--questions", default=None, help="JSON file of {id, question, sql}; default is the built-in set")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    if not os.path.exists(os.path.join(DATA, "nem.duckdb")):
        fail("out/nem/nem.duckdb missing; run `just nem-data` first")
    spec = importlib.util.spec_from_file_location("pi_delegate", os.path.join(REPO, "scripts", "pi_delegate.py"))
    delegate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delegate)
    base_url, model = args.base_url or delegate.DEFAULT_URL, args.model or delegate.DEFAULT_MODEL
    print(f"arm 1 (plain text-to-SQL, schema in the prompt); model under test: {model} at {base_url}")
    pool = QUESTIONS if not args.questions else [(q["id"], q["question"], q["sql"]) for q in json.load(open(args.questions))]
    questions = [q for q in pool if not args.only or q[0] in args.only.split(",")]
    truth = {}
    for qid, _, sql in questions:
        result = run_sql(sql)
        if "error" in result:
            fail(f"ground-truth SQL for {qid} failed: {result['error']}")
        truth[qid] = flat(result["rows"])
    print("ground truth (deterministic SQL, no model): " + "; ".join(f"{q}={truth[q]}" for q in truth))

    print(f"{'id':4} {'verdict':7} question")
    passed = 0
    for qid, text, _ in questions:
        sql, raw = ask_model(base_url, model, text)
        got = run_sql(sql)
        if "error" in got:
            ok, detail = False, f"SQL error: {got['error'][:160]}"
        else:
            values = flat(got["rows"])
            ok = all(any(same(e, g) for g in values) for e in truth[qid])
            detail = f"returned {values[:6]}"
        passed += ok
        print(f"{qid:4} {'PASS' if ok else 'FAIL':7} {text[:78]}")
        if not ok:
            print(f"       expected={truth[qid]} {detail}")
            print(f"       sql={' '.join(sql.split())[:260]}")
    print(f"summary: arm 1 (plain text-to-SQL), {model}: {passed}/{len(questions)} questions match the ground truth")
    if passed != len(questions):
        fail(f"{len(questions) - passed} question(s) did not match; the table above is the evidence (a measurement, not a defect of the harness)")
    print("PASS demo-timeseries-qa: every answer matched the ground truth")


if __name__ == "__main__":
    main()
