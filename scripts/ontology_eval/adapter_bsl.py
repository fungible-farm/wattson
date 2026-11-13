"""Candidate C adapter: Boring Semantic Layer 0.3.18 (Ibis + DuckDB) in a no-network container.
The model sees a description generated from the BSL model objects (runner.py `describe`) and replies with a JSON query spec
{model, dimensions, measures, filters, order_by, limit} or {"cannot_answer": reason}. The runner validates every name against the model and
executes through BSL's query API; `query` records the spec plus the compiled Ibis/DuckDB SQL. One repair call is allowed when validation fails.
Env: EVAL_BASE_URL, EVAL_MODEL, BSL_IMAGE (default localhost/wattson-bsl:local)."""
import json
import os
import re
import subprocess
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:8000/v1")
MODEL = os.environ.get("EVAL_MODEL", "qwen3.8-flash-next")
IMAGE = os.environ.get("BSL_IMAGE", "localhost/wattson-bsl:local")
RUN = ["podman", "run", "--rm", "-i", "--network=none", "--userns=keep-id:uid=1000,gid=1000",
       "-v", f"{os.path.join(REPO, 'out', 'nemosis')}:/nemosis:ro,z", IMAGE]
DESC = None
LAST_FINISH = None
PROMPT = ""  # fixed prompt = HEAD + generated model description; set by setup()

# Model-level descriptions come from the BSL objects; this text only explains the reply format.
HEAD = """You query a semantic layer over electricity-market data (AEMO NEM, one day: 2025-09-01). You cannot write SQL. Pick ONE model and reply with ONLY a JSON object:
{"model": <model>, "dimensions": [..], "measures": [..], "filters": [..], "order_by": [[field, "asc"|"desc"], ..], "limit": <int>}
- dimensions are group-by fields, measures are aggregates; use the exact names listed for that model (joined models use prefixed names).
- filters: {"field": f, "operator": one of = != > >= < <= like in "not in" "is null" "is not null", "value": v} or {"field": f, "operator": "in", "values": [..]};
  combine with {"operator": "AND"|"OR", "conditions": [..]}. A filter field may be a dimension, a measure (applied after aggregation) or a filter-only column (applied before aggregation). Timestamps are strings like "2025-09-01 12:00:00".
- Everything is a single query: no sub-queries, no ratios of separate queries, no joins you define yourself.
If the question cannot be answered with these models, is ambiguous, or asks to change data, reply exactly {"cannot_answer": "<reason>"}.
"""


def _podman(args, stdin=None):
    out = subprocess.run(RUN + args, input=stdin, capture_output=True, text=True, timeout=250)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else json.dumps({"error": out.stderr[-300:]})


def setup():
    global DESC, PROMPT
    import sys
    sys.path.insert(0, os.path.join(REPO, "benchmarks", "ontology", "bsl"))
    descr = json.loads(_podman(["describe"]))
    notes = {"unit_dispatch": "Dispatch per unit (DUID) per 5-minute interval (TOTALCLEARED MW; negative = charging; energy MWh = MW*5/60) joined to unit details (region, station, participant). INTERVENTION=0 only.",
             "unit_details": "Unit details: each DUID's region, station and participant. One row per DUID.",
             "regional_price_demand": "Regional price (RRP $/MWh, FCAS prices) joined to regional demand (MW; NETINTERCHANGE positive = exporting) per region per interval. INTERVENTION=0 only. Regions: NSW1 QLD1 SA1 TAS1 VIC1.",
             "interconnector_flow": "Interconnector flow per interconnector per interval (MWFLOW positive = forward direction, e.g. VIC1-NSW1 from VIC1 to NSW1; MWLOSSES). INTERVENTION=0 only."}
    lines = []
    for m in descr:
        lines.append(f"## model {m['model']}: {notes.get(m['model'], '')}")
        lines.append("dimensions: " + ", ".join(f"{k}:{v}" for k, v in m["dimensions"].items()))
        lines.append("measures: " + ", ".join(f"{k}:{v}" for k, v in m["measures"].items()))
        lines.append("filter-only columns: " + ", ".join(f"{k}:{v}" for k, v in m["filter_only_columns"].items()))
    DESC = "\n".join(lines)
    PROMPT = HEAD + "\n" + DESC


def _ask(messages):
    body = json.dumps({"model": MODEL, "temperature": 0, "seed": int(os.environ.get("EVAL_SEED", "0")), "max_tokens": 8000, "messages": messages}).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body, headers={"content-type": "application/json"})
    choice = json.load(urllib.request.urlopen(req, timeout=280))["choices"][0]
    global LAST_FINISH
    LAST_FINISH = choice.get("finish_reason")
    return choice["message"].get("content") or ""


def _parse(text):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    return json.loads(m.group(1)) if m else None


def run_spec(spec):
    """Path B: run an exact BSL query spec through the same validator/compiler/executor, no model call."""
    if isinstance(spec, dict) and "inexpressible" in spec:
        return dict(rows=None, query="", calls=0, note="inexpressible: " + spec["inexpressible"], stage="represent")
    res = json.loads(_podman(["query"], json.dumps(spec)))
    if "error" in res:
        return dict(rows=None, query=json.dumps(spec), calls=0, note=res["error"][:300], stage=res.get("stage", "execute"))
    return dict(rows=res["rows"], query=json.dumps(spec) + "\n-- compiled --\n" + res["sql"], calls=0, note="", stage=None)


def answer(question):
    if DESC is None:
        setup()
    messages = [{"role": "user", "content": PROMPT + "\n\nQuestion: " + question}]
    calls = 0
    for attempt in range(2):
        text = _ask(messages)
        calls += 1
        try:
            spec = _parse(text)
        except ValueError:
            spec = None
        if not isinstance(spec, dict):
            return dict(rows=None, query="", calls=calls, note=f"unparseable/empty reply (finish_reason={LAST_FINISH}): " + text.strip()[:200])
        if "cannot_answer" in spec:
            return dict(rows=None, query=json.dumps(spec), calls=calls, note="cannot_answer: " + str(spec["cannot_answer"])[:300])
        res = json.loads(_podman(["query"], json.dumps(spec)))
        if "error" not in res:
            return dict(rows=res["rows"], query=json.dumps(spec) + "\n-- compiled --\n" + res["sql"], calls=calls, note="")
        if attempt == 0:
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": "The semantic layer rejected that: " + res["error"] + "\nReply with a corrected JSON object, or {\"cannot_answer\": reason}."}]
    return dict(rows=None, query=json.dumps(spec), calls=calls, note="rejected by semantic layer: " + res["error"][:300], stage=res.get("stage"))


setup()  # build PROMPT at import so the harness can hash it
