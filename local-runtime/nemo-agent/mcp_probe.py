"""Verify upstream just-mcp with the official MCP client SDK (developer check only)."""

from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe() -> None:
    """Initialize, discover the real task schema, and execute its reviewed example."""
    parameters = StdioServerParameters(command="podman", args=[
        "run", "--rm", "-i", "--pull=never", "--network=none", "--read-only",
        "--tmpfs", "/tmp", "--cap-drop=all", "--security-opt=no-new-privileges",
        "localhost/wattson-nemo:example",
        "--log-level", os.environ.get("WATTSON_MCP_LOG_LEVEL", "info"),
    ])
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            tools = (await session.list_tools()).tools
            print(json.dumps({"server": initialized.serverInfo.model_dump(), "tools": [tool.model_dump() for tool in tools]}, indent=2))
            tool = next(tool for tool in tools if "gencost" in tool.name and "capital" in tool.name)
            result = await session.call_tool(tool.name, {"technology": "PV1Axis", "year": "2030", "scenario": "CP"})
            if result.isError:
                raise AssertionError(result)
            print(result.model_dump_json(indent=2))
            if "1123" not in result.model_dump_json():
                raise AssertionError("reviewed source value missing from actual MCP response")
            print("PASS: upstream just-mcp initialize + discover + real NEMO task call")


if __name__ == "__main__":
    asyncio.run(probe())
