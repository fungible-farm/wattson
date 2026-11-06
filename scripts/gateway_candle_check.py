#!/usr/bin/env python3
"""A Candle-based CPU backend behind the gateway (#24: "llama-swap, Candle, a hosted one: one request yields a log record;
a stopped backend yields an error record and a non-zero client exit").

Backend: mistral.rs (Candle) `cpu-latest`, pinned by index digest, serving Qwen/Qwen3-0.6B on the CPU (about 3 GB, about 10
tokens/s measured). Gateway: a throwaway one on 127.0.0.1:14001 with the repo's gateway/agentgateway.yaml, only the llm
baseUrl pointed at the backend. Asserts: (1) a chat completion through the gateway is answered 200 with token usage and
adds 200 request records; (2) after the backend container is stopped the same request gets a 5xx, `curl -f` exits non-zero
and only error records follow (no silent failover, no 200). The model is cached under .models/hf-cache (gitignored) so
only the first run downloads about 1.2 GB. Always tears down.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import yaml

GW_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b"
GW_NAME = "wattson-gw-candle"
GW_PORT = 14001
BACKEND = "wattson-candle-backend"
BACKEND_IMAGE = "ghcr.io/ericlbuehler/mistral.rs:cpu-latest@sha256:aec36c75207e094d6ab6a86d35697268141b96ffcbd0fb980440fd3001d8e0e6"
BACKEND_PORT = 3301
MODEL = "Qwen/Qwen3-0.6B"
URL = f"http://127.0.0.1:{GW_PORT}/v1/chat/completions"
BODY = '{"model":"default","messages":[{"role":"user","content":"Reply with the single word: ok"}],"max_tokens":8}'
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, ".models", "hf-cache")
scratch = None


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def cleanup():
    sh(["podman", "rm", "-f", GW_NAME])
    sh(["podman", "rm", "-f", BACKEND])
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


def fail(msg):
    print(f"FAIL gateway-candle: {msg}")
    sys.exit(1)


def post():
    """Return (http_code, body, curl_rc): one call for status and body, one with -f for the exit status."""
    base = ["curl", "-sS", "-m", "300", "-X", "POST", "-H", "content-type: application/json", "-d", BODY]
    first = sh(base + ["-w", "\n%{http_code}", URL])
    body, _, code = first.stdout.rpartition("\n")
    rc = sh(base + ["-f", "-o", "/dev/null", URL]).returncode
    return code.strip(), body, rc


def request_records():
    logs = sh(["podman", "logs", GW_NAME])
    return [line for line in (logs.stdout + logs.stderr).splitlines() if re.search(r"\srequest\s", line)]


def status_of(line):
    m = re.search(r"http\.response\.status_code=(\d+)", line)
    return int(m.group(1)) if m else None


def wait_http(url, seconds, what):
    deadline = time.time() + seconds
    while time.time() < deadline:
        code = sh(["curl", "-s", "-o", "/dev/null", "--max-time", "2", "-w", "%{http_code}", url]).stdout.strip()
        if code not in ("", "000"):
            return code
        time.sleep(1)
    fail(f"{what} did not answer HTTP within {seconds}s")


def main():
    global scratch
    scratch = tempfile.mkdtemp(prefix="gc-", dir="/tmp")
    os.chmod(scratch, 0o755)
    os.makedirs(CACHE, exist_ok=True)
    os.chmod(CACHE, 0o777)  # the container's user must be able to write the model cache
    sh(["podman", "rm", "-f", BACKEND])
    started = sh(["podman", "run", "-d", "--name", BACKEND, "--pull=missing", "-p", f"127.0.0.1:{BACKEND_PORT}:1234",
                  "-e", "HF_HOME=/cache", "-v", f"{CACHE}:/cache:z", BACKEND_IMAGE,
                  "serve", "--cpu", "--host", "0.0.0.0", "-p", "1234", "-m", MODEL])
    if started.returncode:
        fail(f"backend did not start: {started.stderr[-300:]}")
    deadline = time.time() + 900  # first run downloads about 1.2 GB
    while sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "2",
              f"http://127.0.0.1:{BACKEND_PORT}/v1/models"]).stdout.strip() != "200":
        if time.time() > deadline:
            fail("the Candle backend did not become ready in 900s")
        time.sleep(3)
    print(f"backend: mistral.rs (Candle) cpu serving {MODEL}")

    with open(os.path.join(REPO, "gateway", "agentgateway.yaml")) as f:
        cfg = yaml.safe_load(f)
    cfg.pop("routes", None)  # only the llm path is under test
    for m in cfg["llm"]["models"]:
        m["params"]["baseUrl"] = f"http://169.254.1.2:{BACKEND_PORT}/v1"
    path = os.path.join(scratch, "gateway.yaml")
    with open(path, "w") as f:
        yaml.dump(cfg, f)
    os.chmod(path, 0o644)
    sh(["podman", "rm", "-f", GW_NAME])
    gw = sh(["podman", "run", "-d", "--name", GW_NAME, "--pull=missing", "--read-only", "--cap-drop=all",
             "--security-opt=no-new-privileges", "--memory=512m", "--memory-swap=512m", "--cpus=1", "--pids-limit=128",
             "--network=pasta:--map-host-loopback,169.254.1.2", "-p", f"127.0.0.1:{GW_PORT}:4000",
             "-v", f"{scratch}:/etc/agentgateway:ro,Z", GW_IMAGE, "-f", "/etc/agentgateway/gateway.yaml"])
    if gw.returncode:
        fail(f"gateway did not start: {gw.stderr[-300:]}")
    wait_http(f"http://127.0.0.1:{GW_PORT}/v1/models", 40, "gateway")
    time.sleep(1)

    before = len(request_records())
    code, body, rc = post()
    time.sleep(1)
    recs = request_records()[before:]
    try:
        usage = json.loads(body).get("usage", {})
    except ValueError:
        usage = {}
    print(f"request-ok: http={code} curl_rc={rc} completion_tokens={usage.get('completion_tokens')} "
          f"record_statuses={[status_of(r) for r in recs]}")
    if code != "200" or rc != 0 or not usage.get("completion_tokens"):
        fail(f"healthy request not answered with a completion (http={code}, rc={rc}, usage={usage})")
    if not recs or any(status_of(r) != 200 for r in recs):
        fail(f"expected only 200 request records, got {[status_of(r) for r in recs]}")

    sh(["podman", "stop", "-t", "2", BACKEND])
    before = len(request_records())
    code, _, rc = post()
    time.sleep(1)
    statuses = [status_of(r) for r in request_records()[before:]]
    print(f"request-failed: http={code} curl_rc={rc} record_statuses={statuses}")
    if not (code.startswith("5") and rc != 0):
        fail(f"stopped backend should give a 5xx and a non-zero curl exit (http={code}, rc={rc})")
    if not statuses or any(s is None or s < 500 for s in statuses):
        fail(f"expected only error (5xx) request records after the stop, got {statuses}")
    print("PASS gateway-candle: a Candle (mistral.rs CPU) backend answered through the gateway and was logged; "
          "stopped -> 5xx, non-zero client exit, error records only")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
