#!/usr/bin/env python3
"""What the gateway access log records for one LLM call (#33: per-call journal at the gateway).

Measured, not assumed: a throwaway OpenAI-compatible backend behind a throwaway gateway (same pinned image as
`just gateway-up`, 127.0.0.1:14003, scratch copy of gateway/agentgateway.yaml) answers ONE POST
/v1/chat/completions with usage 123/45 and model 'fake-model', sent with header x-wattson-task: canary-task-42.
The stdout record is read as is, and again with accessLog.add task = request.headers["x-wattson-task"].
"""
import os, re, shutil, signal, subprocess, sys, tempfile, time

import yaml

GW_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b"
GW_NAME = "wattson-gw-llmlog"
GW_PORT = 14003
FAKE_PORT = 3203
MODEL, CANARY = "fake-model", "canary-task-42"
URL = f"http://127.0.0.1:{GW_PORT}/v1/chat/completions"
BODY = f'{{"model":"{MODEL}","messages":[{{"role":"user","content":"hi"}}]}}'
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE = '''import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        out = json.dumps({"id": "c1", "object": "chat.completion", "created": 0, "model": "fake-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", %d), H).serve_forever()
''' % FAKE_PORT

fake_pid, scratch = None, None


def fail(msg):
    print(f"FAIL gateway-llm-log: {msg}")
    sys.exit(1)


def cleanup():
    if fake_pid:
        try: os.killpg(fake_pid, signal.SIGTERM)
        except OSError: pass
    subprocess.run(["podman", "rm", "-f", GW_NAME], capture_output=True)
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


def records():
    """The gateway's own access-log records for our chat call (one per POST)."""
    logs = subprocess.run(["podman", "logs", GW_NAME], capture_output=True, text=True)
    return [l for l in (logs.stdout + logs.stderr).splitlines()
            if re.search(r"\srequest\s", l) and "/v1/chat/completions" in l]


def run_gateway(with_add, n):
    """Start a throwaway gateway over the repo config, make the one canary call, return its log record."""
    with open(os.path.join(REPO, "gateway", "agentgateway.yaml")) as f:
        cfg = yaml.safe_load(f)
    cfg.pop("routes", None)  # the extra routes point at other local ports; only the llm path is under test
    for m in cfg["llm"]["models"]:
        m["params"]["baseUrl"] = f"http://169.254.1.2:{FAKE_PORT}/v1"
    al = cfg["frontendPolicies"]["accessLog"]
    if with_add:  # documented CEL form for a request header
        al["add"] = {"task": 'request.headers["x-wattson-task"]'}
    d = os.path.join(scratch, f"cfg{n}")
    os.makedirs(d), os.chmod(d, 0o755)  # the gateway runs as another uid in the container
    out = os.path.join(d, "gateway.yaml")
    with open(out, "w") as f:
        yaml.dump(cfg, f)
    os.chmod(out, 0o644)
    started = subprocess.run(["podman", "run", "-d", "--name", GW_NAME, "--pull=missing",
                              "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
                              "--memory=512m", "--memory-swap=512m", "--cpus=1", "--pids-limit=128",
                              "--network=pasta:--map-host-loopback,169.254.1.2",
                              "-p", f"127.0.0.1:{GW_PORT}:4000", "-v", f"{d}:/etc/agentgateway:ro,Z",
                              GW_IMAGE, "-f", "/etc/agentgateway/gateway.yaml"], capture_output=True, text=True)
    if started.returncode: fail(f"gateway {n} did not start: {started.stderr[-300:]}")
    deadline = time.time() + 40
    while True:  # ready = any HTTP status (pasta accepts TCP before the container listens)
        code = subprocess.run(["curl", "-s", "-o", "/dev/null", "--max-time", "2", "-w", "%{http_code}",
                               f"http://127.0.0.1:{GW_PORT}/v1/models"], capture_output=True, text=True).stdout.strip()
        if code not in ("", "000"):
            break
        if time.time() > deadline: fail(f"gateway {n} did not answer HTTP within 40s")
        time.sleep(0.5)
    code = subprocess.run(["curl", "-sS", "-m", "20", "-X", "POST", "-H", "content-type: application/json",
                           "-H", f"x-wattson-task: {CANARY}", "-d", BODY, "-o", "/dev/null",
                           "-w", "%{http_code}", URL], capture_output=True, text=True).stdout.strip()
    if code != "200": fail(f"call {n} not answered 200 (http={code})")
    time.sleep(1)
    recs = records()
    subprocess.run(["podman", "rm", "-f", GW_NAME], capture_output=True)
    if len(recs) != 1: fail(f"call {n}: expected exactly one access-log record, got {len(recs)}")
    return recs[0]


def probes(rec):
    return {"model name": MODEL in rec,
            "input tokens 123": bool(re.search(r"input_tokens=123(\s|$)", rec)),
            "output tokens 45": bool(re.search(r"output_tokens=45(\s|$)", rec)),
            "duration": bool(re.search(r"\bduration=\d+\w*", rec)),
            "http status": bool(re.search(r"status_code=200(\s|$)", rec)),
            "canary header": CANARY in rec}


def main():
    global fake_pid, scratch
    scratch = tempfile.mkdtemp(prefix="gl-", dir="/tmp")
    os.chmod(scratch, 0o755)  # the gateway runs as another uid in the container
    fake_py = os.path.join(scratch, "fake.py")
    with open(fake_py, "w") as f:
        f.write(FAKE)
    fake_pid = subprocess.Popen([sys.executable, fake_py], start_new_session=True).pid
    time.sleep(1.5)
    default, added = run_gateway(False, 1), run_gateway(True, 2)
    print('add expression tried: task = request.headers["x-wattson-task"]')
    print(f"with-add record: {added.strip()[:260]}")
    d, a = probes(default), probes(added)
    print(f"{'field':<18} | {'default':<7} | with add")
    print("-" * 40)
    for k in d:
        print(f"{k:<18} | {'yes' if d[k] else 'no':<7} | {'yes' if a[k] else 'no'}")
    print("PASS gateway-llm-log: read from the gateway's own stdout record for one canary call")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
