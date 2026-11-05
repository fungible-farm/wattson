#!/usr/bin/env python3
"""Live half of #27: hot-reload MCP tool list through agentgateway.

Starts two FastMCP servers, a scratch registry listing only the first,
and a throwaway agentgateway container. Lists tools, appends the second
target, re-renders, and polls the same client URL until the list changes.
Asserts the container did not restart. Tears everything down at exit.
"""
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

GW_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.7.0@sha256:60216aafb03d37f94e952ff3d51d2f630a65b10ecaebdc0a864882f92610c45b"
GW_NAME = "wattson-gw-reload"
GW_PORT = 14000
GW_URL = f"http://127.0.0.1:{GW_PORT}/mcp"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

servers = []
scratch = None


def cleanup():
    for pid in servers:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
    subprocess.run(["podman", "rm", "-f", GW_NAME], capture_output=True)
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


SERVER_TEMPLATE = """import asyncio
from mcp.server.fastmcp import FastMCP

app = FastMCP("{name}", version="1.0.0")

@app.tool()
def {tool}(message: str) -> str:
    "Ping server {name}."
    return f"{name}: {{message}}"

if __name__ == "__main__":
    app.settings.port = {port}
    asyncio.run(app.run_streamable_http_async())
"""


def write_server(path, name, port, tool):
    with open(path, "w") as f:
        f.write(SERVER_TEMPLATE.format(name=name, port=port, tool=tool))


def start_server(path):
    # Same interpreter as this script (the recipe runs it under `uv run`, which provides mcp).
    p = subprocess.Popen([sys.executable, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    servers.append(p.pid)
    return p.pid


def render_gateway(tools, out_path):
    targets = [{"name": n, "mcp": {"host": u}} for n, u in tools]
    config = {
        "config": {"readinessAddr": "0.0.0.0:19001", "statsAddr": "0.0.0.0:19002"},
        "gateways": {"default": {"port": 3000}},
        "mcp": {"targets": targets},
    }
    with open(out_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


async def list_tools():
    async with streamablehttp_client(GW_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [t.name for t in result.tools]


def get_started_at():
    out = subprocess.run(
        ["podman", "inspect", GW_NAME, "--format", "{{.State.StartedAt}}"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def main():
    global scratch
    scratch = tempfile.mkdtemp(prefix="rl-", dir="/tmp")
    os.chmod(scratch, 0o755)  # mkdtemp is 0700; the gateway runs as another uid inside the container and must read the config
    try:
        # 1. Write servers
        sa = os.path.join(scratch, "server_a.py")
        sb = os.path.join(scratch, "server_b.py")
        write_server(sa, "a", 3101, "alpha_ping")
        write_server(sb, "b", 3102, "beta_ping")
        start_server(sa)
        start_server(sb)
        time.sleep(3)

        # 2. Scratch registry with only tool a
        reg_dir = os.path.join(scratch, "registries")
        schema_dir = os.path.join(reg_dir, "schema")
        os.makedirs(schema_dir)
        for f in os.listdir(os.path.join(REPO, "registries", "schema")):
            shutil.copy(os.path.join(REPO, "registries", "schema", f), schema_dir)
        for f in ["backends.yaml", "skills.yaml", "prompts.yaml"]:
            shutil.copy(os.path.join(REPO, "registries", f), reg_dir)
        tools_yaml = os.path.join(reg_dir, "tools.yaml")
        with open(tools_yaml, "w") as f:
            yaml.dump({
                "version": 1,
                "entries": [{"name": "a", "mcp_url": "http://169.254.1.2:3101/mcp",
                             "writable_by": ["operator"], "description": "Test A"}],
            }, f)

        gw_cfg = os.path.join(scratch, "gateway.yaml")
        render_gateway([("a", "http://169.254.1.2:3101/mcp")], gw_cfg)

        # Start gateway
        subprocess.run(["podman", "rm", "-f", GW_NAME], capture_output=True)
        subprocess.run([
            "podman", "run", "-d", "--name", GW_NAME, "--pull=missing",
            "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
            "--memory=512m", "--memory-swap=512m", "--cpus=1", "--pids-limit=128",
            "--network=pasta:--map-host-loopback,169.254.1.2",
            "-p", f"127.0.0.1:{GW_PORT}:3000",
            "-v", f"{scratch}:/etc/agentgateway:ro,Z",
            GW_IMAGE, "-f", "/etc/agentgateway/gateway.yaml",
        ], check=True, capture_output=True)
        deadline = time.time() + 40
        while True:  # ready = the gateway answers HTTP with any status (pasta accepts TCP before the container listens, and GET /mcp is not a 2xx)
            code = subprocess.run(["curl", "-s", "-o", "/dev/null", "--max-time", "2", "-w", "%{http_code}", GW_URL],
                                  capture_output=True, text=True).stdout.strip()
            if code not in ("", "000"):
                break
            if time.time() > deadline:
                logs = subprocess.run(["podman", "logs", GW_NAME], capture_output=True, text=True)
                print("FAIL gateway-reload: gateway did not answer HTTP within 40s")
                print((logs.stdout + logs.stderr)[-1500:])
                sys.exit(1)
            time.sleep(0.5)

        started_at = get_started_at()

        # 3. List before
        before = asyncio.run(list_tools())
        print(f"tools-before: {before}")

        # 4. Add tool b, re-render
        with open(tools_yaml, "w") as f:
            yaml.dump({
                "version": 1,
                "entries": [
                    {"name": "a", "mcp_url": "http://169.254.1.2:3101/mcp",
                     "writable_by": ["operator"], "description": "Test A"},
                    {"name": "b", "mcp_url": "http://169.254.1.2:3102/mcp",
                     "writable_by": ["operator"], "description": "Test B"},
                ],
            }, f)
        render_gateway([("a", "http://169.254.1.2:3101/mcp"),
                        ("b", "http://169.254.1.2:3102/mcp")], gw_cfg)

        # Poll for change
        after = before
        for i in range(30):
            time.sleep(1)
            try:
                after = asyncio.run(list_tools())
            except Exception:
                continue
            if after != before:
                break

        print(f"tools-after: {after}")

        # 5. Assert
        if after == before:
            print("FAIL gateway-reload: tool list did not change within 30s")
            sys.exit(1)
        if not any("beta_ping" in t for t in after):
            print("FAIL gateway-reload: beta_ping not in tool list")
            sys.exit(1)
        started_at2 = get_started_at()
        if started_at2 != started_at:
            print("FAIL gateway-reload: container restarted")
            sys.exit(1)
        print("PASS gateway-reload: live tool list changed without restart")

    finally:
        cleanup()


if __name__ == "__main__":
    main()
