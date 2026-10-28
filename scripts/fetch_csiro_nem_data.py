#!/usr/bin/env python3
"""Fetch the CSIRO Synthetic-NEM-2000-Bus MATPOWER case files used by Labs 1-3.

Idempotent: skips a file whose sha256 already matches. Pinned to a specific
upstream commit (not `master`) so the checksums in CHECKSUMS below stay
correct forever, per docs/DEFINITION_OF_DONE.md's "verifies a checksum, and
is idempotent" requirement.

Source: https://github.com/csiro-energy-systems/Synthetic-NEM-2000bus-Data
(CC-BY 4.0). Only the two case files Labs 1-3 actually use are fetched --
snem2000.m/snem2000_acdc.m/etc. are Lab 4/5 territory, out of scope here.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "csiro-energy-systems/Synthetic-NEM-2000bus-Data"
COMMIT = "9d7bf9ac80e705a147568af851bd37f2b8132e9e"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CHECKSUMS = {
    "snemSA.m": "731d5a9fd21118df122dc5107b887e9f91a259b14470a48538af8037bebf8f2a",
    "snem1803.m": "e2b2e718b3bd3e363b644b5873273725bdc6e708a0b80d4035378640f4c15f41",
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def fetch_one(filename: str, expected_sha256: str) -> None:
    dest = DATA_DIR / filename
    if dest.exists() and sha256_of(dest) == expected_sha256:
        print(f"[skip] {filename} already present and checksum-verified")
        return

    url = f"{RAW_BASE}/{filename}"
    print(f"[fetch] {filename} <- {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read()

    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        print(
            f"[FAIL] checksum mismatch for {filename}: "
            f"expected {expected_sha256}, got {actual}",
            file=sys.stderr,
        )
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(f"[ok] {filename} written to {dest} ({len(data)} bytes, sha256 verified)")


def main() -> None:
    for filename, expected in CHECKSUMS.items():
        fetch_one(filename, expected)
    print("PASS: CSIRO NEM case files present and checksum-verified in data/")


if __name__ == "__main__":
    main()
