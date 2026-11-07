#!/usr/bin/env python3
"""Demo 1 (#98): an audited grid study. The AI plans; the deterministic solver computes; the gateway journals every call.

What it does, end to end, on loopback (everything is torn down at the end):
  1. serves the study MCP tools (load_network, run_power_flow) over HTTP (scripts/serve_study_mcp_http.py);
  2. starts the pinned gateway in front of it, with the MCP route journalling tool names AND arguments (opt-in access-log field);
  3. configures a private pi (isolated HOME and agent dir) with ONLY the gateway's MCP URL and asks one fixed question about a
     network: the largest line loading and the number of buses outside the voltage limits after an AC power flow;
  4. computes the same two numbers by calling the solver directly, with no model involved (the ground truth);
  5. checks: the gateway journal contains a `run_power_flow` call for that network, and the numbers in pi's final answer equal the
     solver's (line loading to one decimal place, the violation count exactly).
The pass/fail comes from the solver's numbers and the journal, never from a model's say-so.
Usage: python3 scripts/demo_grid_study.py [--network ieee39] [--case-dir DIR]   (--case-dir: where the CSIRO case files are, for snem-sa-2000;
`just demo-grid-study-nem` fetches them with the wattson-csiro container and passes it)
"""
import argparse
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
sys.path.insert(0, os.path.join(REPO, "src"))
GW_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b"
GW_NAME = "wattson-gw-demo1"
GW_PORT = 14003
SERVER_PORT = 3200
URL = f"http://127.0.0.1:{GW_PORT}/mcp"
SYSTEM = ("You are a grid analyst with tools. You have exactly two tools, load_network (argument network_id) and run_power_flow "
          "(arguments network_id and method; use method \"nr\"). Call them as real tool calls, never write a tool call as text. Never "
          "estimate numbers yourself: call load_network, then run_power_flow, then report the maximum line loading percent and the "
          "number of buses outside the voltage limits (voltage_violations), using exactly the numbers run_power_flow returned.")

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
    print(f"FAIL demo-grid-study: {msg}")
    sys.exit(1)


def run_bounded(command, env, cwd, max_turns=8, max_tools=6, seconds=300):
    """Run pi and read its JSON event stream live, killing it at a turn, tool-call or wall-clock limit; return (events, why)."""
    proc = subprocess.Popen(command, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    events, turns, tools, why, deadline = [], 0, 0, "finished", time.time() + seconds
    import selectors

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        while proc.poll() is None or sel.select(0):
            if time.time() > deadline:
                why = f"stopped: {seconds}s wall clock"
                break
            if not sel.select(0.5):
                continue
            line = proc.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event)
            turns += event.get("type") == "turn_start"
            tools += event.get("type") == "tool_execution_start"
            if turns > max_turns or tools > max_tools:
                why = f"stopped: {turns} turns / {tools} tool calls exceeded the limit"
                break
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
    return events, why


def http_code(url):
    return sh(["curl", "-s", "-o", "/dev/null", "--max-time", "2", "-w", "%{http_code}", url]).stdout.strip()


def ground_truth(network):
    """The same solver, called directly through the same function the MCP server uses: the two numbers the answer must match."""
    from wattson.inference.mcp_server import respond
    from wattson.inference.powermcp import PowerMCP

    reply = json.loads(respond(PowerMCP(), "run_power_flow", {"network_id": network, "method": "nr"}))
    data = reply["data"]
    return float(data["max_line_loading_percent"]), int(data["voltage_violations"])


def main():
    global server_pid, scratch
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", default="ieee39")
    parser.add_argument("--case-dir", default=None, help="directory holding the CSIRO case files (sets WATTSON_CASE_DIR for the solver and the server)")
    args = parser.parse_args()
    if args.case_dir:
        os.environ["WATTSON_CASE_DIR"] = os.path.abspath(args.case_dir)  # read lazily by the solver; inherited by the server subprocess
    scratch = tempfile.mkdtemp(prefix="dg-", dir="/tmp")
    os.chmod(scratch, 0o755)
    pi = shutil.which("pi")
    if not pi:
        fail("pi is not installed")

    loading, violations = ground_truth(args.network)
    print(f"ground-truth (solver, no model): network={args.network} max_line_loading_percent={loading:.1f} voltage_violations={violations}")

    server_pid = subprocess.Popen([sys.executable, os.path.join(REPO, "scripts", "serve_study_mcp_http.py"), "--port", str(SERVER_PORT)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True).pid
    for _ in range(60):
        if http_code(f"http://127.0.0.1:{SERVER_PORT}/mcp/") not in ("", "000"):
            break
        time.sleep(0.5)
    else:
        fail("the study MCP server did not start")

    cfg_dir = os.path.join(scratch, "gw")
    os.makedirs(cfg_dir)
    os.chmod(cfg_dir, 0o755)
    cfg = {"frontendPolicies": {"accessLog": {"preset": "otel", "add": {"tool_arguments": "mcp.tool.arguments"}}},
           "mcp": {"port": 3000, "targets": [{"name": "grid", "mcp": {"host": f"http://169.254.1.2:{SERVER_PORT}/mcp"}}]}}
    cfg_path = os.path.join(cfg_dir, "gateway.yaml")
    with open(cfg_path, "w") as f:
        yaml.dump(cfg, f)
    os.chmod(cfg_path, 0o644)
    sh(["podman", "rm", "-f", GW_NAME])
    started = sh(["podman", "run", "-d", "--name", GW_NAME, "--pull=missing", "--network=pasta:--map-host-loopback,169.254.1.2",
                  "-p", f"127.0.0.1:{GW_PORT}:3000", "-v", f"{cfg_dir}:/etc/agentgateway:ro,Z", GW_IMAGE, "-f", "/etc/agentgateway/gateway.yaml"])
    if started.returncode:
        fail(f"gateway did not start: {started.stderr[-300:]}")
    deadline = time.time() + 40
    while http_code(URL) in ("", "000"):
        if time.time() > deadline:
            fail("gateway did not answer HTTP within 40s")
        time.sleep(0.5)

    spec = importlib.util.spec_from_file_location("pi_delegate", os.path.join(REPO, "scripts", "pi_delegate.py"))
    delegate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delegate)
    question = (f"For the network {args.network}: run an AC power flow with the Newton-Raphson method and tell me the maximum line "
                f"loading in percent and how many buses are outside the voltage limits.")
    ns = Namespace(pi=pi, base_url=delegate.DEFAULT_URL, model=delegate.DEFAULT_MODEL, max_tokens=1024, allow_actions=False,
                   directory=Path(scratch), max_turns=8, max_tool_calls=6, timeout=300, prompt=question)
    command, env = delegate.configure(ns, Path(scratch) / "pi-out")
    added = sh([pi, "mcp", "add", "gateway", "--url", URL, "--exposure", "direct"], env=env)
    if added.returncode:
        fail(f"pi mcp add failed: {(added.stdout + added.stderr)[-300:]}")
    command = [c for c in command if c not in ("--no-mcp", "--no-extensions")]
    i = command.index("--tools")
    command[i:i + 2] = ["--exclude-tools", "bash,edit,write,grep,find,ls"]
    command[command.index("--system-prompt") + 1] = SYSTEM
    command[-1] = question
    events, why = run_bounded(command, env, scratch)
    called_names = [str(e.get("toolName")) for e in events if e.get("type") == "tool_execution_start"]
    print(f"pi tool names called: {called_names}")
    print(f"pi run: {why}; events={len(events)}; tool calls seen by pi={sum(e.get('type') == 'tool_execution_start' for e in events)}")
    final = ""
    for e in events:
        if e.get("type") == "turn_end":
            parts = (e.get("message") or {}).get("content") or []
            text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
            if text.strip():
                final = text
    print(f"pi final answer: {final.strip()[:400]!r}")
    added = sorted({str(tool.get("name")) for e in events for m in (e.get("messages") or []) for tool in (m.get("toolsAdded") or [])})
    print(f"pi tools available: {added}")

    time.sleep(1)
    logs = sh(["podman", "logs", GW_NAME])
    records = [line for line in (logs.stdout + logs.stderr).splitlines() if re.search(r"\srequest\s", line) and "mcp.method.name=tools/call" in line]
    journal = [((re.search(r"gen_ai\.tool\.name=(\S+)", r) or [None, "?"])[1], (re.search(r"tool_arguments=(\{.*\})\s*$", r) or [None, ""])[1]) for r in records]
    print("gateway journal (tools/call): " + "; ".join(f"{n} {a}" for n, a in journal))
    called = [a for n, a in journal if n == "run_power_flow" and args.network in a]
    if not called:
        fail("the gateway journal has no run_power_flow call for the network")

    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", final)]
    loading_ok = any(abs(x - loading) < 0.06 for x in numbers)
    violations_ok = any(x == violations for x in numbers)
    print(f"answer vs solver: line_loading_matches={loading_ok} (solver {loading:.1f}) violation_count_matches={violations_ok} (solver {violations})")
    if not (loading_ok and violations_ok):
        fail("the numbers in the answer do not equal the solver's")
    print("PASS demo-grid-study: the AI planned, the solver computed, the gateway journalled the call, and the answer equals the solver's numbers")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
