"""Run only upstream's three read-only core tools, confined to /input."""
from pathlib import Path
from excel_mcp.config import Settings
from excel_mcp.server import create_server

if __name__ == "__main__":
    create_server(Settings(allowed_dirs=[Path("/input")], read_only=True, tool_areas=())).run()
