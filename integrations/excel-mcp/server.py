"""Run upstream's read-only core tools against one private, session-stable workbook."""
import os
from pathlib import Path
from excel_mcp.config import Settings
from excel_mcp.server import create_server

from capture import capture_workbook, write_manifest


def serve(source: Path) -> None:
    """Capture before MCP initialization, then serve only the captured directory."""
    with capture_workbook(source) as captured:
        write_manifest(captured)
        create_server(Settings(allowed_dirs=[captured.path.parent], read_only=True, tool_areas=())).run()


if __name__ == '__main__':
    serve(Path('/input') / os.environ.get('EXCEL_WORKBOOK_NAME', 'workbook.xlsm'))
