#!/usr/bin/env python3
"""Fetch the CSIRO Synthetic-NEM-2000-Bus case files (CC BY 4.0). The logic, pinned commit and checksums live in ``wattson.fetch``."""
from __future__ import annotations

import sys

from wattson import fetch, log


def main() -> int:
    log.configure(fmt="text")
    try:
        results = fetch.fetch_all()
    except fetch.ChecksumMismatch as error:
        print(f"[FAIL] checksum mismatch: {error}", file=sys.stderr)
        return 1
    for name, outcome in results.items():
        print(f"[{'skip' if outcome == 'skipped' else 'ok'}] {name} {'already present and checksum-verified' if outcome == 'skipped' else 'written, sha256 verified'}")
    print(f"PASS: CSIRO NEM case files present and checksum-verified in {fetch.case_dir()}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
