"""Baseline adapter: plain text-to-SQL with the verified schema (v2), same model/scorer as every candidate.
Env: EVAL_BASE_URL (default http://127.0.0.1:8000/v1), EVAL_MODEL (default qwen3.8-flash-next)."""
import json
import os
import re
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import demo_timeseries_qa as arm1  # noqa: E402

BASE = os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:8000/v1")
MODEL = os.environ.get("EVAL_MODEL", "qwen3.8-flash-next")
SCHEMA = open(os.path.join(REPO, "configs", "nem-multitable-schema.v2.txt")).read()
PROMPT = ("You answer questions about electricity-market data by writing ONE read-only DuckDB SQL query.\n" + SCHEMA +
          "\nReply with only the SQL in a ```sql block. If the question cannot be answered from these tables, is ambiguous, or asks you to "
          "change data, reply exactly: CANNOT_ANSWER: <reason>.\n\nQuestion: ")


def answer(question):
    body = json.dumps({"model": MODEL, "temperature": 0, "seed": int(os.environ.get("EVAL_SEED", "0")), "max_tokens": 8000, "messages": [{"role": "user", "content": PROMPT + question}]}).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body, headers={"content-type": "application/json"})
    text = json.load(urllib.request.urlopen(req, timeout=290))["choices"][0]["message"].get("content") or ""
    if "CANNOT_ANSWER" in text:
        return dict(rows=None, query="", calls=1, note=text.strip()[:300])
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    sql = (m.group(1) if m else text).strip().rstrip(";")
    if not re.match(r"(?is)^\s*(with|select)\b", sql):
        return dict(rows=None, query=sql, calls=1, note="not a SELECT; refused to execute")
    r = arm1.run_sql(sql)
    return dict(rows=r.get("rows"), query=sql, calls=1, note=r.get("error", ""))


def run_spec(sql):
    """Path B for plain text-to-SQL: the gold SQL goes straight to the read-only executor (checks the harness and executor, no model)."""
    r = arm1.run_sql(sql)
    return dict(rows=r.get("rows"), query=sql, calls=0, note=r.get("error", ""), stage="execute" if "error" in r else None)
