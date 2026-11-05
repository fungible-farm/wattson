"""Exercise the real upstream stdio MCP tools against a read-only SQLite fixture."""
import asyncio
import json
import os
import sys

import pyodbc
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    """Verify discovery, real SQL results, tool rejection and driver write denial."""
    parameters = StdioServerParameters(command=sys.executable, args=["/app/server.py"], env=dict(os.environ))
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            assert "ReadData" in names and "ListTables" in names, names
            result = await session.call_tool("ReadData", {"query": "SELECT name, mw FROM measurements ORDER BY name"})
            assert not result.isError, result
            payload = json.loads(result.content[0].text)
            assert payload["data"] == [{"name": "fixture-a", "mw": "42.5"}], payload
            rejected = await session.call_tool("ReadData", {"query": "DELETE FROM measurements"})
            rejection = json.loads(rejected.content[0].text)
            assert rejection.get("error") == "SECURITY_ERROR", rejected
    # Bypass upstream's keyword filtering: verify actual storage/driver policy.
    connection = pyodbc.connect("DSN=fixture", readonly=True)
    try:
        connection.execute("INSERT INTO measurements VALUES ('forbidden', 99)")
    except pyodbc.Error as error:
        assert "readonly" in str(error).lower() or "read-only" in str(error).lower(), error
        storage_denied = True
    else:
        raise AssertionError("ODBC unexpectedly allowed a write")
    finally:
        connection.close()
    print(json.dumps({"upstream": "pyodbc-mcp-server==0.4.0", "transport": "stdio", "tools": names,
                      "query_rows": payload["data"], "mcp_write_rejected": rejection["error"] == "SECURITY_ERROR",
                      "direct_odbc_write_denied": storage_denied, "remote_sql_tested": False}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
