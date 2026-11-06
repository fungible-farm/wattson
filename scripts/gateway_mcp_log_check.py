#!/usr/bin/env python3
"""What the gateway's access log records for an MCP tool call (#28: "the same tool names and arguments").

Measured, not assumed. A throwaway FastMCP server and two throwaway gateways (same pinned image as `just gateway-up`,
127.0.0.1:14002) make one `tools/call` carrying a canary argument:
  1. default access log (`preset: otel`): the record names the tool and its target (`gen_ai.tool.name`, `mcp.target`)
     and does NOT contain the argument (privacy by default);
  2. with `accessLog.add: {tool_arguments: mcp.tool.arguments}`: the record also carries the arguments as JSON.
So tool names are observable out of the box and arguments are an explicit opt-in. Always tears down.
"""
import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

GW_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b"
GW_NAME = "wattson-gw-mcplog"
GW_PORT = 14002
SERVER_PORT = 3101
CANARY = "canary-7f3a91"
TOOL = "alpha_ping"
URL = f"http://127.0.0.1:{GW_PORT}/mcp"

SERVER = f'''import asyncio
from mcp.server.fastmcp import FastMCP
app = FastMCP("a")
@app.tool()
def {TOOL}(message: str) -> str:
    "Ping."
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
    print(f"FAIL gateway-mcp-log: {msg}")
    sys.exit(1)


async def call_tool():
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(TOOL, {"message": CANARY})
            return result.content[0].text


def run_gateway(with_arguments):
    """Start a throwaway gateway, make the call, return the `tools/call` access-log record."""
    cfg = {"frontendPolicies": {"accessLog": {"preset": "otel"}},
           "mcp": {"port": 3000, "targets": [{"name": "a", "mcp": {"host": f"http://169.254.1.2:{SERVER_PORT}/mcp"}}]}}
    if with_arguments:
        cfg["frontendPolicies"]["accessLog"]["add"] = {"tool_arguments": "mcp.tool.arguments"}
    cfg_dir = os.path.join(scratch, "arguments" if with_arguments else "default")
    os.makedirs(cfg_dir)
    os.chmod(cfg_dir, 0o755)
    path = os.path.join(cfg_dir, "gateway.yaml")
    with open(path, "w") as f:
        yaml.dump(cfg, f)
    os.chmod(path, 0o644)
    sh(["podman", "rm", "-f", GW_NAME])
    started = sh(["podman", "run", "-d", "--name", GW_NAME, "--pull=missing",
                  "--network=pasta:--map-host-loopback,169.254.1.2", "-p", f"127.0.0.1:{GW_PORT}:3000",
                  "-v", f"{cfg_dir}:/etc/agentgateway:ro,Z", GW_IMAGE, "-f", "/etc/agentgateway/gateway.yaml"])
    if started.returncode:
        fail(f"gateway did not start: {started.stderr[-300:]}")
    deadline = time.time() + 40
    while True:  # ready = any HTTP status (pasta accepts TCP before the container listens)
        code = sh(["curl", "-s", "-o", "/dev/null", "--max-time", "2", "-w", "%{http_code}", URL]).stdout.strip()
        if code not in ("", "000"):
            break
        if time.time() > deadline:
            fail("gateway did not answer HTTP within 40s")
        time.sleep(0.5)
    answer = asyncio.run(call_tool())
    if answer != f"pong:{CANARY}":
        fail(f"the tool call through the gateway returned {answer!r}")
    time.sleep(1)
    logs = sh(["podman", "logs", GW_NAME])
    records = [l for l in (logs.stdout + logs.stderr).splitlines()
               if re.search(r"\srequest\s", l) and "mcp.method.name=tools/call" in l]
    sh(["podman", "rm", "-f", GW_NAME])
    if len(records) != 1:
        fail(f"expected exactly one tools/call record, got {len(records)}")
    return records[0]


def main():
    global server_pid, scratch
    scratch = tempfile.mkdtemp(prefix="ml-", dir="/tmp")
    os.chmod(scratch, 0o755)
    server_file = os.path.join(scratch, "server.py")
    with open(server_file, "w") as f:
        f.write(SERVER)
    server_pid = subprocess.Popen([sys.executable, server_file], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True).pid
    for _ in range(40):
        if sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{SERVER_PORT}/mcp"]).stdout.strip() not in ("", "000"):
            break
        time.sleep(0.5)
    else:
        fail("the MCP server did not start")

    default = run_gateway(with_arguments=False)
    has_name = f"gen_ai.tool.name={TOOL}" in default and "mcp.target=a" in default
    has_args = CANARY in default
    print(f"default-record: tool_name_and_target={has_name} arguments_present={has_args}")
    if not has_name:
        fail("the default record does not name the tool and its target")
    if has_args:
        fail("the default record contains the argument (expected privacy by default)")

    opt_in = run_gateway(with_arguments=True)
    m = re.search(r'tool_arguments=(\{.*\})\s*$', opt_in)
    print(f"opt-in-record: tool_arguments={m.group(1) if m else None}")
    if not m or CANARY not in m.group(1):
        fail("the opt-in record does not carry the tool arguments")
    print("PASS gateway-mcp-log: tool name and target are logged by default; arguments only with "
          "accessLog.add tool_arguments=mcp.tool.arguments")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
