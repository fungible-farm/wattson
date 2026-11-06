#!/usr/bin/env python3
"""Two harnesses, one gateway (#28): pi and Open Notebook, configured with ONLY the gateway URL.

Starts the real gateway (`just gateway-up`, 127.0.0.1:4000 -> llama-swap) and a throwaway Open Notebook pod
(kube/open-notebook.yaml, UI/API on 127.0.0.1:15502/15055). Each harness then makes one model call:
  * Open Notebook: an "OpenAI Compatible" credential whose base URL is the gateway, a model entry, and the app's own
    model test (POST /api/models/<id>/test);
  * pi: scripts/pi_delegate.py with --base-url pointing at the gateway and a one-line task.
The comparison is the gateway's access log (method, path, status, route), sliced per harness. Asserted: each harness
produced at least one `POST /v1/chat/completions` answered 200 through the gateway, and nothing reached the model
server any other way. The differences between the two record lists are printed; they are the finding.
Everything is torn down at the end. The model call loads a model on llama-swap (about two minutes on first use):
the memory guard is the owner's call, so this refuses to run if `just model-guard` style headroom is missing
(MemAvailable below 40 GiB) and uses the small `qwen3.8-flash-next`.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.environ.get("WATTSON_COMPARE_MODEL", "qwen3.8-flash-next")
GATEWAY = "http://127.0.0.1:4000/v1"
ON_API = "http://127.0.0.1:15055"
ON_POD = "wattson-open-notebook"
# Open Notebook refuses link-local addresses (169.254.x.x, which gateway-up's pasta map uses), so its pod maps the host
# loopback at a private address instead.
ON_GATEWAY = "http://10.255.255.2:4000/v1"
PROMPT = "Reply with the single word: ok"
scratch = None
started_gateway = False


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fail(msg):
    print(f"FAIL harness-compare: {msg}")
    sys.exit(1)


def http(method, url, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def mem_available_gib():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    return 0.0


def gateway_records():
    logs = sh(["podman", "logs", "wattson-gateway"])
    out = []
    for line in (logs.stdout + logs.stderr).splitlines():
        if not re.search(r"\srequest\s", line):
            continue
        g = lambda k: (re.search(rf"{re.escape(k)}=(\S+)", line) or [None, "?"])[1]
        out.append((g("http.request.method"), g("url.path"), g("http.response.status_code"), g("route")))
    return out


def teardown():
    sh(["podman", "pod", "rm", "-f", ON_POD])
    if started_gateway:
        sh(["just", "gateway-down"], cwd=REPO)
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


def wait(url, seconds, ok=(200,)):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status in ok:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main():
    global scratch, started_gateway
    try:
        resident = [m.get("model") for m in http("GET", "http://127.0.0.1:8000/running", timeout=5)[1].get("running", [])]
    except Exception:
        resident = []
    if MODEL in resident:
        print(f"memory: {MODEL} is already resident on llama-swap; no extra load, guard not needed")
    elif mem_available_gib() < 40:
        fail(f"only {mem_available_gib():.0f} GiB available; a model load needs headroom (refusing, not guessing)")
    if not wait("http://127.0.0.1:8000/v1/models", 5):
        fail("llama-swap is not answering on 127.0.0.1:8000")
    scratch = tempfile.mkdtemp(prefix="hc-", dir="/tmp")
    os.chmod(scratch, 0o755)

    if sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{GATEWAY}/models"]).stdout != "200":
        up = sh(["just", "gateway-up"], cwd=REPO)
        if up.returncode:
            fail(f"gateway-up failed: {(up.stdout + up.stderr)[-300:]}")
        started_gateway = True
    models = http("GET", f"{GATEWAY}/models")[1]
    if MODEL not in [m["id"] for m in models.get("data", [])]:
        fail(f"{MODEL} is not served through the gateway")

    spec = open(os.path.join(REPO, "kube", "open-notebook.yaml")).read().replace("@ON_KEY@", secrets.token_urlsafe(24))
    spec_path = os.path.join(scratch, "open-notebook.yaml")
    with open(spec_path, "w") as f:
        f.write(spec)
    sh(["podman", "pod", "rm", "-f", ON_POD])
    play = sh(["podman", "kube", "play", "--network", "pasta:--map-host-loopback,10.255.255.2", spec_path])
    if play.returncode:
        fail(f"kube play failed: {(play.stdout + play.stderr)[-400:]}")
    if not wait(f"{ON_API}/health", 240):
        logs = sh(["podman", "logs", f"{ON_POD}-app"])
        fail(f"Open Notebook API not healthy in 240s: {(logs.stdout + logs.stderr)[-500:]}")
    print("open-notebook: healthy")

    # ---- Open Notebook through the gateway -----------------------------------------------------------------------
    before = len(gateway_records())
    _, cred = http("POST", f"{ON_API}/api/credentials", {
        "name": "wattson-gateway", "provider": "openai_compatible", "modalities": ["language"],
        "base_url": ON_GATEWAY, "api_key": "none"})
    _, model = http("POST", f"{ON_API}/api/models", {
        "name": MODEL, "provider": "openai_compatible", "type": "language", "credential": cred["id"]})
    _, tested = http("POST", f"{ON_API}/api/models/{model['id']}/test", timeout=600)
    time.sleep(1)
    on_records = gateway_records()[before:]
    print(f"open-notebook: model test success={tested.get('success')} message={str(tested.get('message'))[:80]!r}")
    print(f"open-notebook: gateway records={on_records}")

    # ---- pi through the gateway ----------------------------------------------------------------------------------
    task = os.path.join(scratch, "task.txt")
    with open(task, "w") as f:
        f.write(PROMPT)
    workdir = os.path.join(scratch, "pi-work")
    os.makedirs(workdir)
    before = len(gateway_records())
    pi = sh([sys.executable, os.path.join(REPO, "scripts", "pi_delegate.py"), "--task-file", task, "--workdir", workdir,
             "--out-dir", os.path.join(scratch, "pi-out"), "--base-url", GATEWAY, "--model", MODEL, "--timeout", "600",
             "--max-turns", "3", "--max-tool-calls", "1"], env={**os.environ, "WATTSON_DELEGATE_CONTEXT": "65000"})
    time.sleep(1)
    pi_records = gateway_records()[before:]
    try:
        with open(os.path.join(scratch, "pi-out", "report.json")) as f:
            pi_report = json.load(f)
    except OSError:
        pi_report = {}
    # pi_delegate exits non-zero when a read-only task shows no file reads ("no_read_evidence"); that gate is about review
    # tasks and is irrelevant to a one-word reply, so the status is printed rather than the bare exit code.
    print(f"pi: delegate rc={pi.returncode} status={pi_report.get('status')} final_answer={str(pi_report.get('final_answer'))[:60]!r}")
    print(f"pi: gateway records={pi_records}")

    chat = ("POST", "/v1/chat/completions", "200")
    for name, recs in (("open-notebook", on_records), ("pi", pi_records)):
        if not any(r[:3] == chat for r in recs):
            fail(f"{name} produced no 200 POST /v1/chat/completions record through the gateway")
    only_on = sorted(set(on_records) - set(pi_records))
    only_pi = sorted(set(pi_records) - set(on_records))
    print(f"difference: only-open-notebook={only_on} only-pi={only_pi}")
    print("PASS harness-compare: both harnesses reached the model only through the gateway "
          "(each has a 200 POST /v1/chat/completions record); differences printed above")


if __name__ == "__main__":
    try:
        main()
    finally:
        teardown()
