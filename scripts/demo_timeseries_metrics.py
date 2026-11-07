#!/usr/bin/env python3
"""Demo 3, arm 3 (#95): the model PLANS by choosing a governed metric and arguments; fixed, whitelisted SQL templates COMPUTE.

Questions no metric covers must be answered {"metric": null}; that is scored as a failure (the coverage cost of a governed catalogue).
Usage: python3 scripts/demo_timeseries_metrics.py [--questions FILE] [--only Q1,Q2]   (default: the ten built-in questions)
"""
import argparse
import importlib.util
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = json.load(open(os.path.join(REPO, "configs", "nem-metrics.json")))


def load_arm1():
    spec = importlib.util.spec_from_file_location("arm1", os.path.join(REPO, "scripts", "demo_timeseries_qa.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(choice):
    """Return SQL for a model choice, or raise ValueError. Every argument is checked against a whitelist or a strict pattern."""
    name = choice.get("metric")
    if name not in CAT["metrics"]:
        raise ValueError(f"no such metric: {name!r}")
    a = choice.get("args") or {}
    col = CAT["columns"].get(a.get("column"))
    region = a.get("region")
    fields = {}
    if name in ("peak", "count_where", "average", "top_region_by_average"):
        if col is None:
            raise ValueError("column must be demand or price")
        fields["col"] = col
    if name in ("count_where", "average", "weighted_average_price") or (name == "peak" and region != "ALL"):
        if region not in CAT["regions"]:
            raise ValueError("region must be one of " + ", ".join(CAT["regions"]))
        fields["region"] = region
    if name == "peak":
        fields["where"] = "" if region == "ALL" else f"WHERE region = '{region}'"
    if name == "count_where":
        if a.get("op") not in ("above", "below"):
            raise ValueError("op must be above or below")
        fields["op"] = ">" if a["op"] == "above" else "<"
        if not isinstance(a.get("threshold"), (int, float)) or isinstance(a.get("threshold"), bool):
            raise ValueError("threshold must be a number")
        fields["threshold"] = repr(float(a["threshold"]))
    if name == "average":
        window = ""
        for key, op in (("start", ">"), ("end", "<=")):
            if a.get(key):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(a[key])):
                    raise ValueError(f"{key} must be 'YYYY-MM-DD HH:MM:SS'")
                window += f" AND settlement_ts {op} TIMESTAMP '{a[key]}'"
        fields["window"] = window
    return CAT["metrics"][name]["sql"].format(**fields)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--questions", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    arm1 = load_arm1()
    if not os.path.exists(os.path.join(arm1.DATA, "nem.duckdb")):
        print("FAIL demo-timeseries-metrics: out/nem/nem.duckdb missing; run `just nem-data` first")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("pi_delegate", os.path.join(REPO, "scripts", "pi_delegate.py"))
    delegate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delegate)
    base_url, model = args.base_url or delegate.DEFAULT_URL, args.model or delegate.DEFAULT_MODEL
    pool = arm1.QUESTIONS if not args.questions else [(q["id"], q["question"], q["sql"]) for q in json.load(open(args.questions))]
    questions = [q for q in pool if not args.only or q[0] in args.only.split(",")]
    menu = "\n".join(f"- {k}: {v['description']}" for k, v in CAT["metrics"].items())
    prompt = ("You plan; you do not compute. Choose ONE governed metric that answers the question and give its arguments as JSON only, "
              "like {\"metric\": \"count_where\", \"args\": {\"region\": \"SA1\", \"column\": \"price\", \"op\": \"above\", \"threshold\": 300}}.\n"
              "Regions: NSW1, QLD1, SA1, TAS1, VIC1. Data: 5-minute intervals, Sept 2025; settlement time is the END of the interval.\n"
              "Metrics:\n" + menu + "\nIf no metric answers the question, reply {\"metric\": null}.\nQuestion: ")
    print(f"arm 3 (governed metric catalogue); model under test: {model} at {base_url}")
    truth = {}
    for qid, _, sql in questions:
        r = arm1.run_sql(sql)
        if "error" in r:
            print(f"FAIL demo-timeseries-metrics: ground truth for {qid} failed: {r['error']}")
            sys.exit(1)
        truth[qid] = arm1.flat(r["rows"])
    print(f"{'id':4} {'verdict':7} question")
    passed = 0
    for qid, text, _ in questions:
        body = json.dumps({"model": model, "temperature": 0, "max_tokens": 400, "messages": [{"role": "user", "content": prompt + text}]}).encode()
        req = arm1.urllib.request.Request(f"{base_url}/chat/completions", data=body, headers={"content-type": "application/json"})
        with arm1.urllib.request.urlopen(req, timeout=300) as resp:
            reply = json.load(resp)["choices"][0]["message"].get("content") or ""
        m = re.search(r"\{.*\}", reply, re.S)
        ok, detail = False, ""
        try:
            choice = json.loads(m.group(0))
            sql = render(choice)
            got = arm1.run_sql(sql)
            if "error" in got:
                detail = f"SQL error {got['error'][:120]}"
            else:
                values = arm1.flat(got["rows"])
                ok = all(any(arm1.same(e, g) for g in values) for e in truth[qid])
                detail = f"{choice.get('metric')} -> {values[:5]}"
        except (ValueError, AttributeError, TypeError) as error:
            detail = f"no governed answer ({error}); reply={reply[:100]!r}"
        passed += ok
        print(f"{qid:4} {'PASS' if ok else 'FAIL':7} {text[:70]}")
        if not ok:
            print(f"       expected={truth[qid]} {detail}")
    print(f"summary: arm 3 (governed metric catalogue), {model}: {passed}/{len(questions)} questions match the ground truth")
    sys.exit(0 if passed == len(questions) else 1)


if __name__ == "__main__":
    main()
