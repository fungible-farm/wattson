"""Small synthetic parser/security regressions; the issue workbook is proved separately."""
from __future__ import annotations

import hashlib
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
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


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
