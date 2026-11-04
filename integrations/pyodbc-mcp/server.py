"""Run the pinned upstream tools with an explicit unixODBC DSN connection adapter.

Upstream 0.4.0 hardcodes Windows SQL Server authentication and its health check.
This adapter replaces only its connection factory; SQL and MCP tools are upstream.
The ODBC read-only request is defense in depth, never database authorization.
"""
import os
import re

import pyodbc
from mssql_mcp_server import server
from mssql_mcp_server.config import ServerConfig


def connect() -> pyodbc.Connection:
    """Connect to the configured DSN, requesting ODBC read-only access."""
    connection = pyodbc.connect(f"DSN={DSN}", readonly=True, timeout=30)
    connection.timeout = 30
    return connection


DSN: str = os.environ["WATTSON_ODBC_DSN"]
if not re.fullmatch(r"[A-Za-z0-9_-]+", DSN):
    raise ValueError("DSN must contain only letters, digits, underscores or hyphens")
if os.environ.get("WATTSON_DATABASE_READONLY_CONFIRMED") != "yes":
    raise ValueError("Require a SELECT-only database principal or read-only database; see README")
server.set_config(ServerConfig(server="DSN", database=DSN, driver="unixODBC", max_retries=0))
server.create_connection = connect
if __name__ == "__main__":
    # Avoid upstream SQL Server-only startup health queries; connections are lazy.
    server.mcp.run(transport="stdio")
