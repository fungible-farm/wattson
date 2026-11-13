"""NVIDIA Auto Ontology adapter for scripts/ontology_eval.py.
Backend: POST http://127.0.0.1:13001/api/chat/completions (SSE). Rows come from answer['sql_response_from_db'] (AO's own execution path).
`calls` = model calls AO made for the question, counted from the llama-swap-bound requests in the backend container log (see count_calls).
Setup (pod, data, semantic compile) is `just` recipes; see docs/ontology-eval-ao.md. setup() only compiles the semantic layer and waits."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import demo_timeseries_ao as ao  # noqa: E402

PROMPT = "(none: Auto Ontology builds its own prompts server-side; AO pinned 2a24f5d; EVAL_SEED cannot be sent, ChatNVIDIA calls are made inside the backend)"
BACKEND = ao.BACKEND
CALL_RE = re.compile(r"POST /v1/chat/completions|chat/completions")


def setup():
    ao.call("POST", f"{ao.INGEST}/semantic/compile")
    deadline = time.time() + 45 * 60
    while True:
        s = ao.call("GET", f"{ao.INGEST}/semantic/status")
        if s.get("running") is False and (s.get("last_success_at") or s.get("last_failure_at")):
            print(f"semantic-status: {s}", flush=True)
            return
        if time.time() > deadline:
            raise RuntimeError("semantic layer did not finish in time")
        time.sleep(10)


def backend_log(since):
    return subprocess.run(["podman", "logs", "--since", since, "auto-ontology-backend"], capture_output=True, text=True)


def count_calls(log):
    # one "Retryable/Unexpected" or successful model request is not logged per call by AO; count agent-node LLM invocations instead
    return len(re.findall(r"Executing agent: (?:question_intent|question_extraction|candidate_preparation|evidence_refinement|sql_from_semantic|sql_validation|sql_repair)\b", log))


def stage_of(log, ok):
    """Failing AO stage read from the backend log: plan = intent/extraction, represent = no semantic candidates, compile = SQL generation, execute = DB error."""
    if ok:
        return None
    if re.search(r"Question extraction (returned None|failed)|LLM returned None for Question", log):
        return "plan"
    if re.search(r"sql_from_semantic.*(LLM returned None|structured output failed)|unconstructable", log):
        return "compile"
    if re.search(r"No candidates|0 candidate|no tables", log, re.I):
        return "represent"
    if re.search(r"execution (failed|error)|psycopg.errors|SQL error", log, re.I):
        return "execute"
    return "compile"


def rows_from(answer):
    rows = []
    for row in answer.get("sql_response_from_db") or []:
        row = json.loads(row) if isinstance(row, str) else row
        for item in (row if isinstance(row, list) else [row]):
            rows.append(list(item.values()) if isinstance(item, dict) else ([item] if not isinstance(item, list) else item))
    return rows


def answer(question):
    started = time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(started)) + "Z"
    req = urllib.request.Request(f"{BACKEND}/api/chat/completions", data=json.dumps({"question": question}).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    result, errors, steps = None, [], 0
    try:
        with urllib.request.urlopen(req, timeout=290) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                steps += event.get("type") == "step"
                if event.get("type") == "error":
                    errors.append(str(event.get("message")))
                elif event.get("type") == "result":
                    result = event
    except (urllib.error.URLError, OSError) as e:
        errors.append(f"{type(e).__name__}: {e}")
    log = backend_log(since); log = log.stdout + log.stderr
    calls = count_calls(log)
    if result is None:
        return dict(rows=None, query="", calls=calls, stage=stage_of(log, False), note=f"steps={steps} " + ("; ".join(errors) or "no result event"))
    a = result.get("answer", result)
    rows = rows_from(a)
    return dict(rows=rows, query=a.get("sql_code") or "", calls=calls, stage=stage_of(log, bool(rows)),
                note=f"steps={steps} " + " ".join(str(a.get(k))[:200] for k in ("clarification", "message", "error") if a.get(k)))
