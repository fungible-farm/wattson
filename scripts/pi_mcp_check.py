#!/usr/bin/env python3
"""pi calls a tool over the gateway's MCP route, and the gateway's journal agrees with pi's own record (#28).

pi (>= 1.3, MCP built in) is configured with ONLY the gateway's MCP URL, using pi's own `pi mcp add`, inside a private
agent directory (isolated HOME, no inherited extensions/skills/context). The task asks it to call one tool with a canary
argument. Two independent records of that call are compared:
  * pi's JSON event stream: tool name and arguments as pi sent them, and the tool result it received;
  * the gateway access log: `gen_ai.tool.name`, `mcp.target` and (opt-in `accessLog.add`) the arguments.
Asserted: pi lists the gateway's tool, pi called it with the canary argument, pi received the tool's reply, and the gateway
logged one matching `tools/call` with the same arguments. Model: the same local endpoint pi_delegate uses (sm3lly vLLM).
Always tears down. Needs pi >= 1.3 and the pinned gateway image.
"""
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from argparse import Namespace
from pathlib import Path

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GW_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b"
GW_NAME = "wattson-gw-pimcp"
GW_PORT = 14002
SERVER_PORT = 3101
CANARY = "canary-5d21ac"
TOOL = "alpha_ping"
URL = f"http://127.0.0.1:{GW_PORT}/mcp"
SYSTEM = ("You are a tool-using assistant. Call exactly the tool you are asked to call, with exactly the arguments given, "
          "then report the tool's reply verbatim. Do not call any other tool.")
TASK = f'Call the tool named {TOOL} with the argument message set to "{CANARY}". Then reply with exactly what the tool returned.'

SERVER = f'''import asyncio
from mcp.server.fastmcp import FastMCP
app = FastMCP("a")
@app.tool()
def {TOOL}(message: str) -> str:
    "Ping: returns pong: followed by the message."
    return "pong:" + message
app.settings.port = {SERVER_PORT}
asyncio.run(app.run_streamable_http_async())
'''

server_pid = None
scratch = None


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def cleanup():
    if server_pid:
        try:
            os.killpg(server_pid, signal.SIGTERM)
        except OSError:
            pass
    sh(["podman", "rm", "-f", GW_NAME])
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


def fail(msg):
    print(f"FAIL pi-mcp: {msg}")
    sys.exit(1)


def http_code(url):
    return sh(["curl", "-s", "-o", "/dev/null", "--max-time", "2", "-w", "%{http_code}", url]).stdout.strip()


def load_pi_delegate():
    spec = importlib.util.spec_from_file_location("pi_delegate", os.path.join(REPO, "scripts", "pi_delegate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    global server_pid, scratch
    scratch = tempfile.mkdtemp(prefix="pm-", dir="/tmp")
    os.chmod(scratch, 0o755)
    pi = shutil.which("pi")
    if not pi:
        fail("pi is not installed")
    version = sh([pi, "--version"]).stdout.strip()
    print(f"pi: version {version}")

    server_file = os.path.join(scratch, "server.py")
    with open(server_file, "w") as f:
        f.write(SERVER)
    server_pid = subprocess.Popen([sys.executable, server_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True).pid
    for _ in range(40):
        if http_code(f"http://127.0.0.1:{SERVER_PORT}/mcp") not in ("", "000"):
            break
        time.sleep(0.5)
    else:
        fail("the MCP server did not start")

    cfg_dir = os.path.join(scratch, "gw")
    os.makedirs(cfg_dir)
    os.chmod(cfg_dir, 0o755)
    cfg = {"frontendPolicies": {"accessLog": {"preset": "otel", "add": {"tool_arguments": "mcp.tool.arguments"}}},
           "mcp": {"port": 3000, "targets": [{"name": "a", "mcp": {"host": f"http://169.254.1.2:{SERVER_PORT}/mcp"}}]}}
    cfg_path = os.path.join(cfg_dir, "gateway.yaml")
    with open(cfg_path, "w") as f:
        yaml.dump(cfg, f)
    os.chmod(cfg_path, 0o644)
    sh(["podman", "rm", "-f", GW_NAME])
    started = sh(["podman", "run", "-d", "--name", GW_NAME, "--pull=missing", "--network=pasta:--map-host-loopback,169.254.1.2",
                  "-p", f"127.0.0.1:{GW_PORT}:3000", "-v", f"{cfg_dir}:/etc/agentgateway:ro,Z", GW_IMAGE,
                  "-f", "/etc/agentgateway/gateway.yaml"])
    if started.returncode:
        fail(f"gateway did not start: {started.stderr[-300:]}")
    deadline = time.time() + 40
    while http_code(URL) in ("", "000"):
        if time.time() > deadline:
            fail("gateway did not answer HTTP within 40s")
        time.sleep(0.5)

    # A private pi: isolated HOME and agent dir from pi_delegate.configure, then pi's own `mcp add` for the gateway URL.
    delegate = load_pi_delegate()
    args = Namespace(pi=pi, base_url=delegate.DEFAULT_URL, model=delegate.DEFAULT_MODEL, max_tokens=1024, allow_actions=False,
                     directory=Path(scratch), max_turns=6, max_tool_calls=4, timeout=300, prompt=TASK)
    command, env = delegate.configure(args, Path(scratch) / "pi-out")
    # --exposure direct: pi's default (codemode) hides MCP tools behind a script runner; direct registers each as a normal tool.
    added = sh([pi, "mcp", "add", "gateway", "--url", URL, "--exposure", "direct"], env=env)
    if added.returncode:
        fail(f"pi mcp add failed: {(added.stdout + added.stderr)[-300:]}")
    listed = sh([pi, "mcp", "list", "--json"], env=env)
    try:
        listing = json.loads(listed.stdout)
    except ValueError:
        listing = None
    mentions = TOOL in listed.stdout
    print(f"pi mcp list: rc={listed.returncode} json={'yes' if listing is not None else 'no'} lists_{TOOL}={mentions}")
    if listed.returncode or not mentions:
        fail(f"pi does not list the gateway's {TOOL} tool; `pi mcp list --json` said: {(listed.stdout + listed.stderr)[:700]}")

    # Run pi with MCP enabled: drop --no-mcp, keep a one-tool built-in allowlist (MCP tools are kept), our own system prompt.
    # `--no-extensions` also disables pi's built-in extensions, and MCP is one of them (measured: with it, pi only had `read`).
    # Discovery looks in the private agent dir, which has no extensions, so dropping the flag adds nothing but MCP.
    command = [c for c in command if c not in ("--no-mcp", "--no-extensions")]
    # An allowlist (`--tools read`) left pi with only `read` (measured), so use a denylist for the built-ins instead; MCP tools stay.
    i = command.index("--tools")
    command[i:i + 2] = ["--exclude-tools", "bash,edit,write,grep,find,ls"]
    command[command.index("--system-prompt") + 1] = SYSTEM
    command[-1] = TASK
    run = sh(command, env=env, cwd=scratch, timeout=400)
    events = []
    for line in run.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    calls = [e for e in events if e.get("type") == "tool_execution_start" and TOOL in str(e.get("toolName"))]
    results = [e for e in events if e.get("type") == "tool_execution_end" and TOOL in str(e.get("toolName"))]
    sent = (calls[0].get("args") or {}) if calls else {}
    reply = json.dumps(results[0].get("result")) if results else ""
    print(f"pi: tool_calls={len(calls)} args={sent} received_pong={'pong:' + CANARY in reply}")
    if len(calls) != 1 or sent.get("message") != CANARY or f"pong:{CANARY}" not in reply:
        kinds = {}
        for e in events:
            kinds[e.get("type")] = kinds.get(e.get("type"), 0) + 1
        seen = sorted({str(e.get("toolName")) for e in events if e.get("toolName")})
        print(f"diagnostic: event_types={kinds} tool_names_seen={seen}")
        added = []
        for e in events:
            for m in (e.get("messages") or []):
                for tool in (m.get("toolsAdded") or []):
                    added.append(tool.get("name"))
        print(f"diagnostic: tools_added={sorted(set(map(str, added)))}")
        texts = [json.dumps(e)[:400] for e in events if e.get("type") in ("message_end", "agent_end", "turn_end")][-2:]
        print(f"diagnostic: last_events={texts}")
        fail(f"pi did not make exactly one correct {TOOL} call (rc={run.returncode}): {run.stderr[-300:]}")

    time.sleep(1)
    logs = sh(["podman", "logs", GW_NAME])
    records = [l for l in (logs.stdout + logs.stderr).splitlines()
               if re.search(r"\srequest\s", l) and "mcp.method.name=tools/call" in l]
    logged = [(re.search(r"gen_ai\.tool\.name=(\S+)", r) or [None, None])[1] for r in records]
    arguments = [(re.search(r"tool_arguments=(\{.*\})\s*$", r) or [None, None])[1] for r in records]
    print(f"gateway: tools/call records={len(records)} tool={logged} arguments={arguments}")
    if len(records) != 1 or logged != [TOOL] or not arguments[0] or CANARY not in arguments[0]:
        fail("the gateway journal does not match pi's call")
    print("PASS pi-mcp: pi called the gateway tool with the canary argument and received its reply; the gateway journal "
          "recorded the same call (tool name and arguments)")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
