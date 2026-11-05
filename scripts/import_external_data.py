"""Import explicitly listed, checksum-verified operator data into this checkout.

This is a one-time offline transfer, not a runtime link to the supplying directory.
It neither downloads from NEMWeb nor changes published task/trajectory manifests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT: Path = Path(__file__).resolve().parents[1]
MANIFEST: Path = ROOT / "configs" / "external-data.json"


def digest(path: Path) -> str:
    """Hash an input with bounded memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def transfer(source_root: Path) -> int:
    """Copy only approved files; preserve an existing destination if its identity differs."""
    entries = json.loads(MANIFEST.read_text())["artifacts"]
    for entry in entries:
        source = source_root / entry["source_path"]
        destination = ROOT / entry["destination_path"]
        if digest(source) != entry["sha256"]:
            raise ValueError(f"Input does not match the approved hash: {source}")
        if destination.exists():
            if digest(destination) != entry["sha256"]:
                raise ValueError(f"Refusing to overwrite a changed artifact: {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        shutil.copyfile(source, temporary)
        if digest(temporary) != entry["sha256"]:
            raise ValueError(f"Input changed during transfer: {source}")
        temporary.replace(destination)
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    arguments = parser.parse_args()
    print(f"PASS: {transfer(arguments.source_root)} independent operator artifacts verified")
