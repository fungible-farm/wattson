"""Exercise the upstream stdio server and verify its real DuckDB build artifacts."""

import asyncio
import json
from importlib.metadata import version
import os
from pathlib import Path

import duckdb
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS: set[str] = {"parse", "list", "compile", "build"}
EXPECTED_ROWS: list[tuple[str, int]] = [("sample_a", 25), ("sample_b", 7)]
OUTPUT: Path = Path("/output")


async def main() -> None:
    """Initialize MCP, call local tools, and check artifacts rather than quiet output."""
    # Remove prior evidence so a failed CLI call cannot pass using stale artifacts.
    for artifact in ("target/manifest.json", "target/run_results.json"):
        (OUTPUT / artifact).unlink(missing_ok=True)
    parameters = StdioServerParameters(command="dbt-mcp", env=dict(os.environ))
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert tools == EXPECTED_TOOLS, tools
            calls = []
            for name, arguments in (
                ("parse", {}),
                ("list", {"resource_type": ["model"]}),
                ("compile", {}),
                ("build", {}),
            ):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                text = "\n".join(
                    item.text for item in result.content if item.type == "text"
                )
                if name == "list":
                    assert "region_loads" in text, text
                calls.append({"tool": name, "result": text})
    manifest = json.loads((OUTPUT / "target/manifest.json").read_text())
    assert "model.wattson_dbt_sample.region_loads" in manifest["nodes"]
    run_results = json.loads((OUTPUT / "target/run_results.json").read_text())
    assert run_results["results"], run_results
    assert all(row["status"] in {"success", "pass"} for row in run_results["results"]), run_results
    with duckdb.connect(str(OUTPUT / "sample.duckdb"), read_only=True) as connection:
        rows = connection.execute(
            "select region, total_mw from region_loads order by region"
        ).fetchall()
    assert rows == EXPECTED_ROWS, rows
    report = {
        "server": initialized.serverInfo.model_dump(),
        "packages": {name: version(name) for name in ("dbt-mcp", "dbt-core", "dbt-duckdb", "duckdb", "mcp")},
        "tools": sorted(tools),
        "calls": calls,
        "build_results": [
            {"unique_id": row["unique_id"], "status": row["status"]}
            for row in run_results["results"]
        ],
        "rows": rows,
        "fixture": "illustrative deterministic sample; not NEM data",
    }
    (OUTPUT / "mcp-proof.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
