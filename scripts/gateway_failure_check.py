#!/usr/bin/env python3
"""Failure behaviour of the model gateway (#24): a request is logged, a stopped backend is an error record.

Starts a fake OpenAI-compatible backend and a throwaway agentgateway (same pinned image and flags as
`just gateway-up`) on 127.0.0.1:14001, using a scratch copy of gateway/agentgateway.yaml whose only change
is the llm baseUrl. Asserts: (1) a request is answered 200 and adds exactly one 200 request record;
(2) after the backend is killed the same request gets a 5xx, curl -f exits non-zero, and only error
records are logged; (3) nothing answered 200 after the kill (no silent failover). Always tears down.
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import yaml

GW_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b"
GW_NAME = "wattson-gw-failure"
GW_PORT = 14001
FAKE_PORT = 3201
URL = f"http://127.0.0.1:{GW_PORT}/v1/chat/completions"
BODY = '{"model":"x","messages":[{"role":"user","content":"hi"}]}'
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE = '''import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        out = json.dumps({"id": "c1", "object": "chat.completion", "created": 0, "model": "x",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", %d), H).serve_forever()
''' % FAKE_PORT

fake_pid = None
scratch = None


def cleanup():
    if fake_pid:
        try:
            os.killpg(fake_pid, signal.SIGTERM)
        except OSError:
            pass
    subprocess.run(["podman", "rm", "-f", GW_NAME], capture_output=True)
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


def post():
    """Return (http_code, curl_rc): one call for the status code, one with -f for the exit status."""
    base = ["curl", "-sS", "-m", "20", "-X", "POST", "-H", "content-type: application/json", "-d", BODY]
    code = subprocess.run(base + ["-o", "/dev/null", "-w", "%{http_code}", URL],
                          capture_output=True, text=True).stdout.strip()
    rc = subprocess.run(base + ["-f", "-o", "/dev/null", URL], capture_output=True).returncode
    return code, rc


def request_records():
    logs = subprocess.run(["podman", "logs", GW_NAME], capture_output=True, text=True)
    return [line for line in (logs.stdout + logs.stderr).splitlines() if re.search(r"\srequest\s", line)]


def status_of(line):
    m = re.search(r"http\.response\.status_code=(\d+)", line)
    return int(m.group(1)) if m else None


def fail(msg):
    print(f"FAIL gateway-failure: {msg}")
    sys.exit(1)


def main():
    global fake_pid, scratch
    scratch = tempfile.mkdtemp(prefix="gf-", dir="/tmp")
    os.chmod(scratch, 0o755)  # the gateway runs as another uid in the container
    fake = os.path.join(scratch, "fake.py")
    with open(fake, "w") as f:
        f.write(FAKE)
    fake_pid = subprocess.Popen([sys.executable, fake], start_new_session=True).pid
    with open(os.path.join(REPO, "gateway", "agentgateway.yaml")) as f:
        cfg = yaml.safe_load(f)
    cfg.pop("routes", None)  # the extra routes point at other local ports; only the llm path is under test
    for m in cfg["llm"]["models"]:
        m["params"]["baseUrl"] = f"http://169.254.1.2:{FAKE_PORT}/v1"
    out = os.path.join(scratch, "gateway.yaml")
    with open(out, "w") as f:
        yaml.dump(cfg, f)
    os.chmod(out, 0o644)
    subprocess.run(["podman", "rm", "-f", GW_NAME], capture_output=True)
    subprocess.run(["podman", "run", "-d", "--name", GW_NAME, "--pull=missing",
                    "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges", "--memory=512m",
                    "--memory-swap=512m", "--cpus=1", "--pids-limit=128",
                    "--network=pasta:--map-host-loopback,169.254.1.2", "-p", f"127.0.0.1:{GW_PORT}:4000",
                    "-v", f"{scratch}:/etc/agentgateway:ro,Z", GW_IMAGE, "-f", "/etc/agentgateway/gateway.yaml"],
                   check=True, capture_output=True)
    deadline = time.time() + 40
    while True:  # ready = the gateway answers HTTP with any status (pasta accepts TCP before the container listens)
        code = subprocess.run(["curl", "-s", "-o", "/dev/null", "--max-time", "2", "-w", "%{http_code}",
                               f"http://127.0.0.1:{GW_PORT}/v1/models"], capture_output=True, text=True).stdout.strip()
        if code not in ("", "000"):
            break
        if time.time() > deadline:
            fail("gateway did not answer HTTP within 40s")
        time.sleep(0.5)
    time.sleep(1)

    before = len(request_records())
    code, rc = post()
    time.sleep(1)
    recs = request_records()[before:]
    print(f"request-ok: http={code} curl_rc={rc} new_records={len(recs)} statuses={[status_of(r) for r in recs]}")
    if code != "200" or rc != 0:
        fail(f"healthy request not answered 200 (http={code}, rc={rc})")
    if len(recs) != 2 or any(status_of(r) != 200 for r in recs):
        # post() sends the request twice (code probe + curl -f probe), so two records are expected
        fail(f"expected two 200 request records, got {[status_of(r) for r in recs]}")

    os.killpg(fake_pid, signal.SIGTERM)
    time.sleep(1)
    before = len(request_records())
    code, rc = post()
    time.sleep(1)
    recs = request_records()[before:]
    statuses = [status_of(r) for r in recs]
    print(f"request-failed: http={code} curl_rc={rc} record_statuses={statuses}")
    if not (code.startswith("5") and rc != 0):
        fail(f"stopped backend should give a 5xx and a non-zero curl exit (http={code}, rc={rc})")
    if not recs or any(s is None or s < 500 for s in statuses):
        fail(f"expected only error (5xx) request records after the kill, got {statuses}")
    print("PASS gateway-failure: request logged 200; stopped backend -> 5xx, non-zero client exit, "
          "error records only, no 200 after the kill")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
