"""Exercise real stdio MCP calls against saved solver fixtures, without an LLM."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from wattson.inference.powermcp import PowerMCP

ROOT: Path = Path(__file__).resolve().parents[2]
REPLAY_FIXTURE: Path = ROOT / "src/wattson/distillation/fixtures/expected_mcp_replays.json"


def test_real_canonical_mcp_transport() -> None:
    """Discover schemas and compare actual RPC results with real replay fixtures."""
    async def check() -> None:
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "wattson.inference.mcp_server"],
            cwd=str(ROOT),
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            actual = {tool.name: tool.inputSchema for tool in tools}
            expected = {
                schema["function"]["name"]: schema["function"]["parameters"]
                for schema in PowerMCP().get_tool_schemas()
            }
            assert actual == expected
            for replay in json.loads(REPLAY_FIXTURE.read_text())["replays"]:
                for step in replay["answer"]["trajectory"]:
                    response = await session.call_tool(step["tool"], step["args"])
                    if replay["name"] == "unknown_case":
                        # MCP rejects IDs outside the published enum before
                        # calling the backend's unknown-ID failure branch.
                        assert response.isError
                        continue
                    assert not response.isError
                    result = json.loads(response.content[0].text)
                    assert result["status"] == step["status"]
                    payload = result["data"] or {"error": result["error"]}
                    assert payload == step["result"]
                    if step["evidence"] is not None:
                        assert result["evidence"]["result_hash"] == step["evidence"]["result_hash"]

    asyncio.run(check())
