"""Candidate D adapter: Malloy 0.0.436 behind Malloy Publisher 0.12.5 (REST), in a rootless Podman container with --network=none.
The model sees the Malloy model description generated from Publisher's model endpoint (modelDef) and writes a Malloy QUERY (not SQL),
or {"cannot_answer": reason}. The query is compiled and executed by Publisher's POST .../models/nem.malloy/query; the generated SQL
Publisher returns is recorded in `query`. run_spec(spec) sends gold Malloy text through the same path with no model call.
Env: EVAL_BASE_URL, EVAL_MODEL, EVAL_SEED."""
import json
import os
import re
import subprocess
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(REPO, "benchmarks", "ontology", "malloy")
BASE = os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:8000/v1")
MODEL = os.environ.get("EVAL_MODEL", "qwen3.8-flash-next")
IMAGE = "localhost/wattson-malloy:local"
NAME = "malloy-eval-publisher"
PKG = "/environments/local/packages/nem"
SCHEMA = open(os.path.join(REPO, "configs", "nem-multitable-schema.v2.txt")).read()

PRIMER = """Malloy query syntax (a query, not SQL). Use ONLY the sources, dimensions, measures and joined fields listed in the model; do not use raw column math when a measure exists.
  run: <source> -> { where: <filter>  group_by: <dimensions>  aggregate: <name> is <measure or expression>  having: <filter on aggregates>  order_by: <name> desc, <dim>  limit: N }
A measure is used by name in aggregate:, e.g. `aggregate: top is max_price`; give each output a name with `is`. Joined fields are reached with a dot (`unit.region`).
Pipelines chain stages with `->`; the next stage reads the previous stage's output columns, and `calculate: r is row_number()` numbers rows in the stage's order_by. Filtered aggregates: `x.sum() { where: r <= 3 }`.
A source can be extended inside the query: `source: s is <source> extend { join_one: j is (<other source> -> { ...one-row query... }) on <dim> = j.<dim> }` then `run: s -> { where: j.<dim> is not null ... }`.
Example: `run: region_totals -> { group_by: region aggregate: peak is max_demand_mw order_by: peak desc, region limit: 2 }`.
"""
RULES = ("You answer questions about electricity-market data by writing ONE Malloy query against the model below, in a ```malloy block (one or more `source:` definitions followed by one `run:`). "
         "If the question cannot be answered from this model, is ambiguous, or asks you to change data, reply exactly: {\"cannot_answer\": \"<reason>\"}.\n")


def _exec(args, inp=None, timeout=120):
    return subprocess.run(["podman", "exec", "-i", NAME, *args], input=inp, capture_output=True, text=True, timeout=timeout)


def _api(method, path, body=None):
    out = _exec(["node", "/opt/server_root/client.mjs", method, path, "-"] if body is not None else ["node", "/opt/server_root/client.mjs", method, path], inp=json.dumps(body) if body is not None else None)
    o = json.loads(out.stdout.strip().splitlines()[-1])
    return o["status"], json.loads(o["body"]) if o["body"] else {}


def setup():
    subprocess.run(["podman", "rm", "-f", NAME], capture_output=True)
    work = os.path.join(REPO, "out", "malloy-work")
    subprocess.run(["rm", "-rf", work])
    os.makedirs(work, exist_ok=True)
    subprocess.run(["podman", "run", "-d", "--name", NAME, "--network=none", f"--userns=keep-id:uid={os.getuid()},gid={os.getgid()}",
                    "-v", f"{DIR}:/opt/server_root:ro,z", "-v", f"{os.path.join(REPO, 'out', 'nemosis')}:/nemosis:ro,z", "-v", f"{work}:/tmp/pubwork:z",
                    "--entrypoint", "sh", IMAGE, "-c",
                    "cp -r /opt/server_root /tmp/pubwork/root; cd /tmp/pubwork/root && HOME=/tmp/pubwork exec node /usr/local/lib/node_modules/@malloy-publisher/server/dist/server.mjs --server_root /tmp/pubwork/root --port 4000 --mcp_port 4040 --init"],
                   check=True, capture_output=True)
    for _ in range(90):
        try:
            if _api("GET", "/status")[1].get("operationalState") == "serving" and _api("GET", PKG + "/models")[0] == 200:
                return
        except Exception:  # noqa: BLE001 - still starting
            pass
        time.sleep(1)
    raise RuntimeError("Publisher did not reach serving")


def teardown():
    subprocess.run(["podman", "rm", "-f", NAME], capture_output=True)


def describe_model():
    """Model description generated from Publisher's model endpoint (modelDef): sources with dimensions, measures, joins; raw columns by type."""
    st, b = _api("GET", PKG + "/models/nem.malloy")
    md = json.loads(b["modelDef"])
    lines = []
    for name in md["exports"]:
        s = md["contents"][name]
        cols, dims, meas, joins = [], [], [], []
        for f in s["fields"]:
            if "fields" in f and f.get("join"):
                jf = [x["name"] + (":" + x["type"] if x.get("expressionType") != "aggregate" else "(measure)") for x in f["fields"] if x.get("expressionType") or x["name"] not in ("DUID",)]
                joins.append(f"{f.get('as', f['name'])} ({f['join']}-join; reach fields as {f.get('as', f['name'])}.<field>): {', '.join(jf)}")
            elif f.get("expressionType") == "aggregate":
                meas.append(f["name"])
            elif f.get("expressionType") == "scalar":
                dims.append(f"{f['name']}:{f['type']}")
            elif "type" in f and f["type"] != "table":
                cols.append(f"{f['name']}:{f['type']}")
        filt = "; ".join(x.get("code", "") for x in s.get("filterList", []) or [])
        lines.append(f"source {name}" + (f"  [always filtered: {filt}]" if filt else "") +
                     f"\n  columns: {', '.join(cols)}\n  dimensions: {', '.join(dims)}\n  measures: {', '.join(meas)}" + ("\n  joins:\n    " + "\n    ".join(joins) if joins else ""))
    return "\n".join(lines)


_DESC = None


def prompt_prefix():
    global _DESC
    if _DESC is None:
        _DESC = describe_model()
    return RULES + "\nData conventions:\n" + SCHEMA + "\nMalloy model (generated from the published model):\n" + _DESC + "\n\n" + PRIMER + "\nQuestion: "


PROMPT = RULES + "<conventions><generated model description>" + PRIMER


def _run(query):
    st, b = _api("POST", PKG + "/models/nem.malloy/query", {"query": query})
    if st == 200:
        r = json.loads(b["result"])
        rows = [[c.get("number_value", c.get("string_value", c.get("timestamp_value"))) if isinstance(c, dict) else c for c in rec["record_value"]]
                for rec in r["data"].get("array_value", [])]
        return dict(rows=rows, query=r["sql"], note="", stage=None)
    stage = "compile" if (b.get("problems") or st == 400) else "execute"
    return dict(rows=None, query=query, note=f"{stage} error {st}: {b.get('message', '')}"[:400], stage=stage, error=True)


def run_spec(spec):
    out = _run(spec)
    out["calls"] = 0
    if out.pop("error", False):
        out["rows"] = None
    return out


def answer(question):
    body = json.dumps({"model": MODEL, "temperature": 0, "seed": int(os.environ.get("EVAL_SEED", "0")), "max_tokens": 8000,
                       "messages": [{"role": "user", "content": prompt_prefix() + question}]}).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body, headers={"content-type": "application/json"})
    choice = json.load(urllib.request.urlopen(req, timeout=280))["choices"][0]
    text = choice["message"].get("content") or ""
    if not text.strip():
        return dict(rows=None, query="", calls=1, note=f"empty content, finish_reason={choice.get('finish_reason')} (reasoning budget exhausted)", stage="plan")
    if "cannot_answer" in text:
        return dict(rows=None, query="", calls=1, note=text.strip()[:300], stage="plan")
    m = re.search(r"```(?:malloy)?\s*(.*?)```", text, re.S | re.I)
    if not m:
        return dict(rows=None, query=text[:500], calls=1, note="no malloy block in reply", stage="plan")
    out = _run(m.group(1).strip())
    out["calls"] = 1
    out.pop("error", None)
    if out["stage"] in ("compile", "execute"):
        out["rows"] = None
    return out
