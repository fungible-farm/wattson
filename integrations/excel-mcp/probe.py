"""Diagnose the original workbook bytes without edits or macro execution."""
import json
import os
import traceback
from pathlib import Path
from zipfile import ZipFile
import openpyxl

if __name__ == "__main__":
    source = Path("/input") / os.environ.get("EXCEL_WORKBOOK_NAME", "workbook.xlsm")
    with ZipFile(source) as archive:
        print(json.dumps({"bytes": source.stat().st_size, "zip_members":len(archive.infolist()),
                          "zip_crc_error":archive.testzip()}),flush=True)
    try:
        book = openpyxl.load_workbook(source,read_only=True,data_only=False,keep_links=False)
        print(json.dumps({"openpyxl_version":openpyxl.__version__,"sheet_count":len(book.sheetnames),"sheet_names_sample":book.sheetnames[:3]}),flush=True)
        print(list(book.worksheets[0].iter_rows(min_row=1,max_row=3,min_col=1,max_col=3,values_only=True)),flush=True)
        book.close()
    except Exception:
        traceback.print_exc()
        raise
