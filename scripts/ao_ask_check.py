#!/usr/bin/env python3
"""Auto Ontology answers a question over the demo database end to end (#20): semantic layer compiled, SQL generated, answer correct.

Preconditions (the check does NOT start or stop anything; see the recipe line in the justfile): the AO pod is up with a 2048-dim
embedder, `just ao-fixture` has seeded demo.orders (5 rows) and ingestion has embedded it. Steps:
  1. POST the ingestion service's /semantic/compile and poll /semantic/status until the run is finished (the semantic layer is
     built by the chat model: this is the slow, model-heavy step);
  2. POST the backend's /api/chat/completions with a question whose answer is a known count and read the server-sent events;
  3. assert the `result` event carries the executed SQL and rows containing the count 5.
Deterministic observation: the row value returned by the database through the SQL the model wrote, compared with the known 5.
Usage: python3 scripts/ao_ask_check.py [--timeout-minutes 30]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

INGEST = "http://127.0.0.1:13002"
BACKEND = "http://127.0.0.1:13001"
QUESTION = "How many orders are there in total?"
EXPECTED = 5


def call(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def fail(msg):
    print(f"FAIL ao-ask: {msg}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-minutes", type=int, default=30)
    args = parser.parse_args()
    deadline = time.time() + args.timeout_minutes * 60

    try:
        status, _ = call("POST", f"{INGEST}/semantic/compile")
    except urllib.error.HTTPError as e:
        fail(f"POST /semantic/compile -> HTTP {e.code}: {e.read()[:200]!r}")
    print(f"semantic-compile: accepted http={status}")
    last = None
    while True:
        _, st = call("GET", f"{INGEST}/semantic/status")
        running = st.get("running") if isinstance(st, dict) else None
        if running != last:
            print(f"semantic-status: {st}")
            last = running
        if running is False:
            break
        if time.time() > deadline:
            fail("the semantic layer did not finish within the timeout")
        time.sleep(10)

    body = json.dumps({"question": QUESTION}).encode()
    req = urllib.request.Request(f"{BACKEND}/api/chat/completions", data=body, method="POST",
                                 headers={"content-type": "application/json"})
    result, errors, steps = None, [], 0
    try:
        with urllib.request.urlopen(req, timeout=max(60, deadline - time.time())) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                if event.get("type") == "step":
                    steps += 1
                elif event.get("type") == "error":
                    errors.append(event.get("message"))
                elif event.get("type") == "result":
                    result = event
    except urllib.error.HTTPError as e:
        fail(f"POST /api/chat/completions -> HTTP {e.code}: {e.read()[:300]!r}")
    print(f"chat: steps={steps} errors={errors} got_result={result is not None}")
    if errors or result is None:
        fail(f"no result: {errors}")
    answer = result.get("answer", result)
    sql = answer.get("sql_code")
    rows = answer.get("sql_response_from_db")
    print(f"sql: {sql}")
    print(f"rows: {json.dumps(rows)[:300]}")
    flat = json.dumps(rows)
    if not sql or str(EXPECTED) not in flat:
        fail(f"the executed rows do not contain the known count {EXPECTED}")
    print(f"PASS ao-ask: the model wrote SQL, the database returned {EXPECTED} for '{QUESTION}'")


if __name__ == "__main__":
    main()
