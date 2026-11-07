#!/usr/bin/env python3
"""Demo 3 (#100, #95), arm 2: the same ten questions asked of Auto Ontology over the SAME data (loaded into the pod's Postgres).

Preconditions (the check starts and stops nothing; scripts and recipes do): the Auto Ontology pod is up with a 2048-dim embedder,
`just ao-load-nem` has loaded the five regions into `demo.price_demand` (same columns as the DuckDB table arm 1 queries), semantic
compilation is enabled, ingestion has run. Steps: compile the semantic layer, then for each question POST the backend's
/api/chat/completions, read the server-sent events, and compare the executed rows with the ground truth computed by the SAME
deterministic SQL arm 1 uses (run in the no-network DuckDB container). Per question: PASS only if every expected value is in the
returned rows. Also recorded per question: agent steps and wall time, because cost is part of the #95 comparison.
Usage: python3 scripts/demo_timeseries_ao.py [--only Q1,Q6] [--compile-timeout-minutes 30]
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INGEST, BACKEND = "http://127.0.0.1:13002", "http://127.0.0.1:13001"


def load_arm1():
    spec = importlib.util.spec_from_file_location("demo_timeseries_qa", os.path.join(REPO, "scripts", "demo_timeseries_qa.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def ask(question, timeout):
    """POST one question; return (steps, sql, values, error, seconds)."""
    started = time.time()
    req = urllib.request.Request(f"{BACKEND}/api/chat/completions", data=json.dumps({"question": question}).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    steps, result, errors = 0, None, []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                kind = event.get("type")
                steps += kind == "step"
                if kind == "error":
                    errors.append(event.get("message"))
                elif kind == "result":
                    result = event
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        errors.append(f"{type(error).__name__}: {error}")
    seconds = time.time() - started
    if result is None:
        return steps, None, [], "; ".join(map(str, errors)) or "no result event", seconds
    answer = result.get("answer", result)
    values = []
    for row in answer.get("sql_response_from_db") or []:
        row = json.loads(row) if isinstance(row, str) else row
        for item in (row if isinstance(row, list) else [row]):
            values.extend(item.values() if isinstance(item, dict) else [item])
    return steps, answer.get("sql_code"), values, None, seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--questions", default=None, help="JSON file of {id, question, sql}; default is the built-in set")
    parser.add_argument("--compile-timeout-minutes", type=int, default=30)
    args = parser.parse_args()
    arm1 = load_arm1()
    pool = arm1.QUESTIONS if not args.questions else [(q["id"], q["question"], q["sql"]) for q in json.load(open(args.questions))]
    questions = [q for q in pool if not args.only or q[0] in args.only.split(",")]
    truth = {}
    for qid, _, sql in questions:
        result = arm1.run_sql(sql)
        if "error" in result:
            print(f"FAIL demo-timeseries-ao: ground truth for {qid} failed: {result['error']}")
            sys.exit(1)
        truth[qid] = arm1.flat(result["rows"])
    print("ground truth (deterministic SQL, no model): " + "; ".join(f"{q}={truth[q]}" for q in truth))

    call("POST", f"{INGEST}/semantic/compile")
    deadline = time.time() + args.compile_timeout_minutes * 60
    while True:
        status = call("GET", f"{INGEST}/semantic/status")
        if status.get("running") is False and (status.get("last_success_at") or status.get("last_failure_at")):
            break
        if time.time() > deadline:
            print("FAIL demo-timeseries-ao: the semantic layer did not finish in time")
            sys.exit(1)
        time.sleep(10)
    print(f"semantic-status: {status}")

    print(f"{'id':4} {'verdict':7} {'steps':>5} {'sec':>6}  question")
    passed, total_steps, total_seconds = 0, 0, 0.0
    for qid, text, _ in questions:
        steps, sql, values, error, seconds = ask(text, timeout=900)
        ok = error is None and all(any(arm1.same(e, g) for g in values) for e in truth[qid])
        passed += ok
        total_steps += steps
        total_seconds += seconds
        print(f"{qid:4} {'PASS' if ok else 'FAIL':7} {steps:>5} {seconds:>6.0f}  {text[:70]}")
        if not ok:
            print(f"       expected={truth[qid]} returned={values[:6]} error={error}")
            print(f"       sql={' '.join((sql or '').split())[:260]}")
    print(f"summary: arm 2 (Auto Ontology): {passed}/{len(questions)} questions match the ground truth; {total_steps} agent steps, {total_seconds:.0f} s in total")
    if passed != len(questions):
        print("FAIL demo-timeseries-ao: at least one question did not match (a measurement; the table above is the evidence)")
        sys.exit(1)
    print("PASS demo-timeseries-ao: every answer matched the ground truth")


if __name__ == "__main__":
    main()
