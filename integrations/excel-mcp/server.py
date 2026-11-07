"""Run upstream's read-only core tools against one private, session-stable workbook, plus one address-keyed read.

``read_cells`` is ours, not upstream's: upstream's ``read_range`` returns a bare grid, so a caller has to COUNT rows and columns to name
a cell, and language models get that wrong (measured on the ISP workbook: right value, wrong address, nine times in ten). ``read_cells``
returns every non-empty cell with its address, so a cited cell is a copied fact, not a counted one. It reads only the captured
workbook (the ``path`` argument is checked against its name), read-only, cached values, bounded by ``max_cells``.
"""
import os
from pathlib import Path
from excel_mcp.config import Settings
from excel_mcp.server import create_server
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries

from capture import capture_workbook, write_manifest

MAX_CELLS_CEILING = 2000


def add_read_cells(server, workbook_path: Path) -> None:
    """Register ``read_cells`` on the upstream server; the workbook is opened lazily, once per session."""
    cache = {}

    @server.tool()
    def read_cells(path: str, sheet: str, range: str, max_cells: int = 400) -> dict:
        """Read a rectangular range and return every non-empty cell WITH ITS ADDRESS, e.g. [{"cell": "E7", "value": 0.07}].
        Cite the address exactly as returned. Values are the workbook's cached values (formulas are not recalculated)."""
        if Path(path).name != workbook_path.name:
            return {"error": f"unknown workbook {path!r}; the only workbook is {workbook_path.name!r}"}
        try:
            min_col, min_row, max_col, max_row = range_boundaries(range)
        except ValueError:
            return {"error": f"bad range {range!r}; use A1 notation such as A1:J40"}
        if None in (min_col, min_row, max_col, max_row):
            return {"error": "give a bounded rectangle such as A1:J40, not a whole row or column"}
        count = (max_col - min_col + 1) * (max_row - min_row + 1)
        if count > min(max_cells, MAX_CELLS_CEILING):
            return {"error": f"{count} cells requested, limit is {min(max_cells, MAX_CELLS_CEILING)}; narrow the range or raise max_cells (ceiling {MAX_CELLS_CEILING})"}
        if "wb" not in cache:
            cache["wb"] = load_workbook(workbook_path, read_only=True, data_only=True, keep_links=False)
        if sheet not in cache["wb"].sheetnames:
            return {"error": f"no sheet {sheet!r}; sheets: {cache['wb'].sheetnames}"}
        cells = [{"cell": c.coordinate, "value": c.value}
                 for row in cache["wb"][sheet].iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col)
                 for c in row if c.value is not None]
        return {"sheet": sheet, "range": range, "cells": cells}


def serve(source: Path) -> None:
    """Capture before MCP initialization, then serve only the captured directory."""
    with capture_workbook(source) as captured:
        write_manifest(captured)
        server = create_server(Settings(allowed_dirs=[captured.path.parent], read_only=True, tool_areas=()))
        add_read_cells(server, captured.path)
        server.run()


if __name__ == '__main__':
    serve(Path('/input') / os.environ.get('EXCEL_WORKBOOK_NAME', 'workbook.xlsm'))
