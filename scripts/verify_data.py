"""Check data/ against data/MANIFEST.json (SHA-256 of every file) and the row counts the dataset manifests record.

    uv run scripts/verify_data.py            # verify
    uv run scripts/verify_data.py --write    # regenerate MANIFEST.json after an intended change

Why: the published results were measured on exactly these tasks and trajectories; a silent edit would make them unreproducible.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

DATA: Path = Path(__file__).resolve().parents[1] / "data"
MANIFEST: Path = DATA / "MANIFEST.json"
SELF: str = "MANIFEST.json"
# The CSIRO case files are downloaded by scripts/fetch_csiro_nem_data.py and checksum-verified there, not shipped.
FETCHED_SUFFIX: str = ".m"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def files() -> list[Path]:
    return sorted(p for p in DATA.rglob("*") if p.is_file() and p.name != SELF and p.suffix != FETCHED_SUFFIX)


def build() -> dict[str, str]:
    return {str(p.relative_to(DATA)): digest(p) for p in files()}


def row_count_problems() -> list[str]:
    """Trajectory manifests record how many rows they kept; the gzip file must hold that many."""
    problems: list[str] = []
    for manifest in sorted((DATA / "trajectories").glob("*/manifest.json")):
        kept = json.loads(manifest.read_text()).get("kept")
        with gzip.open(manifest.parent / "trajectories.jsonl.gz", "rt") as stream:
            rows = sum(1 for line in stream if line.strip())
        if kept is not None and rows != kept:
            problems.append(f"{manifest.parent.name}: manifest says {kept} rows, file has {rows}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="regenerate data/MANIFEST.json")
    arguments = parser.parse_args()
    actual = build()
    if arguments.write:
        MANIFEST.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
        print(f"wrote {MANIFEST.name}: {len(actual)} files")
        return 0
    recorded = json.loads(MANIFEST.read_text())
    problems = [f"changed: {k}" for k in actual if k in recorded and actual[k] != recorded[k]]
    problems += [f"unlisted: {k}" for k in actual if k not in recorded]
    problems += [f"missing: {k}" for k in recorded if k not in actual]
    problems += row_count_problems()
    for line in problems:
        print(line)
    print(f"{'FAIL' if problems else 'PASS'}: {len(actual)} files checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
