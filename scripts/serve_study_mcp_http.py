#!/usr/bin/env python3
"""Serve the study MCP tools (load_network, run_power_flow, ...) over streamable HTTP so the gateway can front them (demo 1, #98).

The same server object as the stdio endpoint (`src/wattson/inference/mcp_server.py`, `create_server`); only the transport differs.
Stateless sessions, so a restart or a second client needs no session handoff. Binds 127.0.0.1 by default; inside a container pass
--host 0.0.0.0 and publish the port on the host's loopback only.

    python3 scripts/serve_study_mcp_http.py [--port 3200] [--host 127.0.0.1] [--study-tools] [--python-tools]
"""
import argparse
import contextlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import uvicorn  # noqa: E402
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from wattson.inference.mcp_server import create_server  # noqa: E402


def build_app(study_tools: bool = False, python_tools: bool = False) -> Starlette:
    manager = StreamableHTTPSessionManager(app=create_server(study_tools=study_tools, python_tools=python_tools),
                                           stateless=True, json_response=True)

    async def handle(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    # Mounted at "/" so that /mcp is served directly: Mount("/mcp") answers /mcp with a 307 redirect to /mcp/, which a gateway in front
    # does not follow, so the MCP handshake silently never happens.
    return Starlette(routes=[Mount("/", app=handle)], lifespan=lifespan)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3200)
    parser.add_argument("--study-tools", action="store_true")
    parser.add_argument("--python-tools", action="store_true")
    args = parser.parse_args()
    uvicorn.run(build_app(args.study_tools, args.python_tools), host=args.host, port=args.port, log_level="warning")
