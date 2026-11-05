"""Small synthetic parser/security regressions; the issue workbook is proved separately."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

try:
    from excel_mcp.config import Limits
    from excel_mcp.errors import PathNotAllowedError, WorkbookError
    from excel_mcp.operations.cells import read_range
    from excel_mcp.paths import PathPolicy
    from excel_mcp.workspace import Workspace
    from openpyxl import Workbook
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

SERVER_DIRECTORY = Path(__file__).resolve().parent
if not (SERVER_DIRECTORY / "server.py").is_file():
    SERVER_DIRECTORY = Path(__file__).resolve().parents[1] / "integrations" / "excel-mcp"


@unittest.skipUnless(AVAILABLE, "optional integration dependencies; run integrations/excel-mcp/run.sh test")
class ExcelReadOnlyTests(unittest.TestCase):
    """Exercise actual upstream operations on disposable, clearly synthetic inputs."""

    def test_formula_text_and_numeric_row_order_preserve_original(self) -> None:
        """Ranges crossing row 9 must retain numeric order; reads never save the workbook."""
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "synthetic.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet["A9"] = "nine"
            sheet["A10"] = "=1+2"
            sheet["A11"] = "eleven"
            workbook.save(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            workspace = Workspace(PathPolicy([directory]), Limits(), False)
            with workspace.stream(path.name) as loaded:
                result = read_range(loaded.active, "A9:A11", 3)
                self.assertEqual(result.values, [["nine"], ["=1+2"], ["eleven"]])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_outside_path_and_symlink_escape_are_refused(self) -> None:
        """The upstream path policy must not follow a workbook symlink outside /input."""
        with TemporaryDirectory() as allowed, TemporaryDirectory() as outside:
            foreign = Path(outside) / "foreign.xlsx"
            Workbook().save(foreign)
            alias = Path(allowed) / "escape.xlsx"
            alias.symlink_to(foreign)
            workspace = Workspace(PathPolicy([Path(allowed)]), Limits(), False)
            for candidate in (str(foreign), str(alias)):
                with self.assertRaises(PathNotAllowedError):
                    workspace.resolve_existing(candidate)

    def test_mcp_reads_same_capture_after_parent_replaces_original(self) -> None:
        """A later host/parent edit must not alter cells served by an initialized session."""
        async def exercise() -> None:
            with TemporaryDirectory() as temporary:
                directory = Path(temporary)
                original = directory / "synthetic.xlsx"
                workbook = Workbook()
                workbook.active["A1"] = "captured value"
                workbook.save(original)
                expected = hashlib.sha256(original.read_bytes()).hexdigest()
                manifest = directory / "manifest.json"
                environment = dict(os.environ, EXCEL_MCP_CAPTURE_MANIFEST=str(manifest))
                code = "from pathlib import Path; from server import serve; import sys; serve(Path(sys.argv[1]))"
                parameters = StdioServerParameters(command=sys.executable,
                    args=["-c", code, str(original)], env=environment, cwd=str(SERVER_DIRECTORY))
                async with stdio_client(parameters) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        captured = json.loads(manifest.read_text())
                        self.assertEqual(captured["snapshot_sha256"], expected)
                        snapshot = Path(captured["snapshot_path"])
                        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o400)
                        self.assertEqual(snapshot.parent.stat().st_mode & 0o777, 0o700)
                        first = (await session.call_tool("read_range", {
                            "path":original.name,"sheet":"Sheet","range":"A1:A1",
                            "mode":"formulas","max_cells":1})).model_dump(by_alias=True)
                        self.assertEqual(first["structuredContent"]["values"], [["captured value"]])
                        # In-place replacement, not a rename: same source path and inode.
                        original_inode = original.stat().st_ino
                        workbook.active["A1"] = "host replaced value"
                        workbook.save(original)
                        self.assertEqual(original.stat().st_ino, original_inode)
                        self.assertNotEqual(hashlib.sha256(original.read_bytes()).hexdigest(), expected)
                        result = (await session.call_tool("read_range", {
                            "path":original.name,"sheet":"Sheet","range":"A1:A1",
                            "mode":"formulas","max_cells":1})).model_dump(by_alias=True)
                        self.assertFalse(result.get("isError"), result)
                        self.assertEqual(result["structuredContent"]["values"], [["captured value"]])
                        refused = (await session.call_tool("describe_workbook", {
                            "path":str(original)})).model_dump(by_alias=True)
                        self.assertTrue(refused.get("isError"), refused)
                        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), expected)
        asyncio.run(exercise())

    def test_malformed_zip_is_not_reported_as_a_workbook(self) -> None:
        """A filename suffix must not convert invalid bytes into successful cell data."""
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "broken.xlsx"
            path.write_bytes(b"synthetic invalid workbook bytes")
            workspace = Workspace(PathPolicy([Path(temporary)]), Limits(), False)
            with self.assertRaises(WorkbookError):
                with workspace.stream(path.name):
                    pass


if __name__ == "__main__":
    unittest.main()
