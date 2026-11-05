"""Exercise upstream stdio tools and verify fresh, isolated DuckDB build artifacts."""

import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
from uuid import uuid4

import duckdb
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS: set[str] = {"parse", "list", "compile", "build"}
EXPECTED_ROWS: list[tuple[str, int]] = [("sample_a", 25), ("sample_b", 7)]
# Exact nodes/statuses from the committed seed, model and three schema tests.
EXPECTED_RESULTS: dict[str, str] = {
    "seed.wattson_dbt_sample.example_loads": "success",
    "model.wattson_dbt_sample.region_loads": "success",
    "test.wattson_dbt_sample.not_null_region_loads_region.15b99200cc": "pass",
    "test.wattson_dbt_sample.not_null_region_loads_total_mw.78a4459236": "pass",
    "test.wattson_dbt_sample.unique_region_loads_region.5e07a1bcb2": "pass",
}
OUTPUT: Path = Path("/output")


async def main() -> None:
    """Require this invocation's build/test evidence, never a prior or compile result."""
    # A failed new attempt must invalidate any previous success marker immediately.
    (OUTPUT / "mcp-proof.json").unlink(missing_ok=True)
    run_dir = OUTPUT / f"proof-{uuid4()}"
    run_dir.mkdir()
    target = run_dir / "target"
    environment = dict(os.environ)
    environment.update({
        "DBT_TARGET_PATH": str(target),
        "DBT_LOG_PATH": str(run_dir / "logs"),
        "DBT_DUCKDB_PATH": str(run_dir / "sample.duckdb"),
    })
    parameters = StdioServerParameters(command="dbt-mcp", env=environment)
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert tools == EXPECTED_TOOLS, tools
            calls = []
            compile_invocation: str | None = None
            build_started = datetime.now(timezone.utc)
            for name, arguments in (
                ("parse", {}),
                ("list", {"resource_type": ["model"]}),
                ("compile", {}),
                ("build", {}),
            ):
                if name == "build":
                    compile_results = json.loads((target / "run_results.json").read_text())
                    compile_invocation = compile_results["metadata"]["invocation_id"]
                    # dbt compile also creates run_results.json. It is not build evidence.
                    (target / "run_results.json").unlink()
                    build_started = datetime.now(timezone.utc)
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                text = "\n".join(
                    item.text for item in result.content if item.type == "text"
                )
                if name == "list":
                    assert "region_loads" in text, text
                calls.append({"tool": name, "result": text})
    manifest = json.loads((target / "manifest.json").read_text())
    assert "model.wattson_dbt_sample.region_loads" in manifest["nodes"]
    # Upstream can return CLI failure text with isError=False; fresh artifacts are authoritative.
    run_results = json.loads((target / "run_results.json").read_text())
    assert run_results["args"]["which"] == "build", run_results["args"]
    invocation = run_results["metadata"]["invocation_id"]
    assert invocation and invocation != compile_invocation, run_results["metadata"]
    generated = datetime.fromisoformat(run_results["metadata"]["generated_at"].replace("Z", "+00:00"))
    assert generated >= build_started, run_results["metadata"]
    actual_results = {row["unique_id"]: row["status"] for row in run_results["results"]}
    assert len(run_results["results"]) == len(EXPECTED_RESULTS), run_results["results"]
    assert actual_results == EXPECTED_RESULTS, actual_results
    with duckdb.connect(str(run_dir / "sample.duckdb"), read_only=True) as connection:
        rows = connection.execute(
            "select region, total_mw from region_loads order by region"
        ).fetchall()
    assert rows == EXPECTED_ROWS, rows
    report = {
        "server": initialized.serverInfo.model_dump(),
        "packages": {name: version(name) for name in ("dbt-mcp", "dbt-core", "dbt-duckdb", "duckdb", "mcp")},
        "output_directory": str(run_dir),
        "build_invocation_id": invocation,
        "tools": sorted(tools),
        "calls": calls,
        "build_results": actual_results,
        "rows": rows,
        "fixture": "illustrative deterministic sample; not NEM data",
    }
    (OUTPUT / "mcp-proof.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
