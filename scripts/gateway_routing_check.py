#!/usr/bin/env python3
"""Model-name routing through the REAL gateway config (#24): `candle-qwen3-0.6b` -> Candle, any other name -> llama-swap.

Uses `just candle-up` and `just gateway-up` (the real recipes and gateway/agentgateway.yaml), sends one chat completion for
each model name and reads the gateway's access log: the record's `endpoint=` says which backend answered. Asserted:
  candle-qwen3-0.6b  -> endpoint 169.254.1.2:3301 (Candle), status 200, a completion with token usage
  qwen3.8-flash-next -> endpoint 169.254.1.2:8000 (llama-swap), status 200
llama-swap's model must already be resident (it is not loaded here: a large load is the owner's call), otherwise the second
request is skipped and reported as such, not counted as a pass. Stops only what this script started.
"""
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = "http://127.0.0.1:4000/v1/chat/completions"
LLAMA_MODEL = os.environ.get("WATTSON_ROUTING_MODEL", "qwen3.8-flash-next")
started = []


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, **kw)


def up(name):
    return sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3", name]).stdout.strip() == "200"


def fail(msg):
    print(f"FAIL gateway-routing: {msg}")
    sys.exit(1)


def records():
    logs = sh(["podman", "logs", "wattson-gateway"])
    return [l for l in (logs.stdout + logs.stderr).splitlines() if re.search(r"\srequest\s", l) and "/v1/chat/completions" in l]


def ask(model):
    before = len(records())
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply with the single word: ok"}], "max_tokens": 8})
    out = sh(["curl", "-sS", "-m", "300", "-X", "POST", "-H", "content-type: application/json", "-d", body,
              "-w", "\n%{http_code}", GATEWAY])
    text, _, code = out.stdout.rpartition("\n")
    new = records()[before:]
    endpoint = re.search(r"endpoint=(\S+)", new[-1]).group(1) if new else None
    try:
        tokens = json.loads(text).get("usage", {}).get("completion_tokens")
    except ValueError:
        tokens = None
    return code.strip(), endpoint, tokens


def main():
    try:
        if not up("http://127.0.0.1:3301/v1/models"):
            r = sh(["just", "candle-up"])
            if r.returncode:
                fail(f"candle-up failed: {(r.stdout + r.stderr)[-300:]}")
            started.append("candle-down")
        if not up("http://127.0.0.1:4000/v1/models"):
            r = sh(["just", "gateway-up"])
            if r.returncode:
                fail(f"gateway-up failed: {(r.stdout + r.stderr)[-300:]}")
            started.append("gateway-down")

        code, endpoint, tokens = ask("candle-qwen3-0.6b")
        print(f"route candle-qwen3-0.6b: http={code} endpoint={endpoint} completion_tokens={tokens}")
        if code != "200" or endpoint != "169.254.1.2:3301" or not tokens:
            fail("candle-qwen3-0.6b did not reach Candle")

        resident = []
        try:
            resident = [m["model"] for m in json.loads(sh(["curl", "-s", "-m", "5", "http://127.0.0.1:8000/running"]).stdout)["running"]]
        except (ValueError, KeyError):
            pass
        if LLAMA_MODEL not in resident:
            print(f"route {LLAMA_MODEL}: SKIPPED (not resident on llama-swap; loading it is the owner's call)")
            print("PASS gateway-routing (partial): candle-qwen3-0.6b reached Candle; the llama-swap leg was skipped, not passed")
            return
        code, endpoint, tokens = ask(LLAMA_MODEL)
        print(f"route {LLAMA_MODEL}: http={code} endpoint={endpoint} completion_tokens={tokens}")
        if code != "200" or endpoint != "169.254.1.2:8000":
            fail(f"{LLAMA_MODEL} did not reach llama-swap")
        print("PASS gateway-routing: candle-qwen3-0.6b -> Candle (3301); other model names -> llama-swap (8000)")
    finally:
        for recipe in reversed(started):
            sh(["just", recipe])


if __name__ == "__main__":
    main()
