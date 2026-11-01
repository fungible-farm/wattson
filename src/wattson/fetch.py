"""Fetch the CSIRO Synthetic-NEM-2000-Bus MATPOWER case files the labs and tools use.

Idempotent: a file whose SHA-256 already matches is skipped. Pinned to a specific upstream commit, not ``master``, so the
checksums below stay correct. Source: https://github.com/csiro-energy-systems/Synthetic-NEM-2000bus-Data (CC BY 4.0, CSIRO).
The files are downloaded, never redistributed in this repository or in the container image; ``WATTSON_CASE_DIR`` chooses where
they go (the container points it at a volume so the download happens once).
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path

from wattson import log

REPO: str = "csiro-energy-systems/Synthetic-NEM-2000bus-Data"
COMMIT: str = "9d7bf9ac80e705a147568af851bd37f2b8132e9e"
RAW_BASE: str = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}"
CASE_DIR_ENV: str = "WATTSON_CASE_DIR"
FETCH_TIMEOUT_S: float = 30.0
CHECKSUMS: dict[str, str] = {
    "snemSA.m": "731d5a9fd21118df122dc5107b887e9f91a259b14470a48538af8037bebf8f2a",
    "snem1803.m": "e2b2e718b3bd3e363b644b5873273725bdc6e708a0b80d4035378640f4c15f41",
}
_LOG = log.get("fetch")


class ChecksumMismatch(RuntimeError):
    """The downloaded bytes are not the pinned file; nothing is written."""


def case_dir() -> Path:
    override = os.environ.get(CASE_DIR_ENV)
    return Path(override) if override else Path(__file__).resolve().parents[2] / "data"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status() -> dict[str, str]:
    """For each pinned file: ``ok`` (present, checksum matches), ``wrong`` (present, different) or ``missing``."""
    out: dict[str, str] = {}
    for name, expected in CHECKSUMS.items():
        path = case_dir() / name
        out[name] = "missing" if not path.exists() else ("ok" if sha256_of(path) == expected else "wrong")
    return out


def fetch_one(filename: str, expected: str, directory: Path) -> str:
    """Download one file if it is not already correct; return ``skipped`` or ``fetched``."""
    dest = directory / filename
    if dest.exists() and sha256_of(dest) == expected:
        _LOG.info("case.present", extra={"file": filename, "path": str(dest)})
        return "skipped"
    url = f"{RAW_BASE}/{filename}"
    _LOG.info("case.fetch", extra={"file": filename, "url": url})
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ChecksumMismatch(f"{filename}: expected {expected}, got {actual}")
    directory.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    _LOG.info("case.written", extra={"file": filename, "path": str(dest), "bytes": len(data)})
    return "fetched"


def fetch_all() -> dict[str, str]:
    directory = case_dir()
    return {name: fetch_one(name, expected, directory) for name, expected in CHECKSUMS.items()}


def main(argv: list[str] | None = None) -> int:
    """``python -m wattson.fetch [status]``: the entry point of the ``wattson-csiro`` image (and of ``wattson fetch-data``)."""
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="wattson-csiro", description=__doc__)
    parser.add_argument("action", nargs="?", choices=["fetch", "status"], default="fetch")
    arguments = parser.parse_args(argv)
    log.configure()
    if arguments.action == "status":
        state = status()
        print(json.dumps(state))
        return 0 if all(v == "ok" for v in state.values()) else 1
    try:
        print(json.dumps(fetch_all()))
    except ChecksumMismatch as error:
        _LOG.error("fetch.failed", extra={"error": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
