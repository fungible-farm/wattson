"""Check whether model files fit in available host memory.

    python scripts/model_mem_guard.py 233GB
    python scripts/model_mem_guard.py /path/to/model-00001-of-00009.gguf
    python scripts/model_mem_guard.py --fraction 0.75 /path/to/model-dir

Accepts file paths (sharded GGUF sets are summed), directory paths (all
.gguF/.safetensors/.bin files are summed), or size literals (121GiB, 233GB).
Decimal GB vs binary GiB are distinct. Default fraction 0.60 of MemAvailable.

Exit 0 = PASS, 1 = FAIL (exceeds limit), 2 = error (bad input, unreadable file).
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from decimal import Decimal


def parse_size_literal(text: str) -> int:
    """Parse a size literal like 233GB or 121GiB into bytes."""
    m = re.fullmatch(r'(\d+(?:\.\d+)?)([a-zA-Z]+)', text)
    if not m:
        raise ValueError(f"unparseable size: {text}")
    unit = m.group(2).lower()
    # Decimal (SI) units are powers of 1000; binary (IEC) units are powers of 1024. Never mixed.
    units = {
        'b': 1,
        'kb': 1000, 'kib': 1024,
        'mb': 1000 ** 2, 'mib': 1024 ** 2,
        'gb': 1000 ** 3, 'gib': 1024 ** 3,
        'tb': 1000 ** 4, 'tib': 1024 ** 4,
    }
    if unit not in units:
        raise ValueError(f"unknown unit: {m.group(2)}")
    return int(Decimal(m.group(1)) * units[unit])


def get_file_size(path: str) -> int:
    """Get file size in bytes; raise if unreadable."""
    try:
        return os.path.getsize(path)
    except (OSError, IOError) as e:
        raise RuntimeError(f"cannot read file {path}: {e}")


def find_shards(path: str) -> list[str]:
    """Find all shards of a GGUF model matching -NNNNN-of-MMMMM pattern."""
    # Match pattern like model-00001-of-00009.gguf
    m = re.search(r'-(\d+)-of-(\d+)\.', path)
    if not m:
        return [path]
    total_shards = m.group(2)
    # Build pattern to match all shards
    prefix = path[:m.start()]
    suffix = path[m.end():]
    # Use * instead of - to match the hyphen
    pattern = f"{glob.escape(prefix)}*of-{glob.escape(total_shards)}.{glob.escape(suffix)}"
    shards = sorted(glob.glob(pattern))
    if not shards:
        return [path]
    return shards


def sum_files(paths: list[str]) -> int:
    """Sum sizes of files, expanding a sharded set to ALL its shards (including the one named).

    Files are de-duplicated by path AFTER expansion, so naming one shard, several shards, or the
    directory never double-counts and never skips a shard.
    """
    total = 0
    seen: set[str] = set()

    def add(path: str) -> None:
        nonlocal total
        if path not in seen:
            seen.add(path)
            total += get_file_size(path)

    for p in paths:
        if os.path.isdir(p):
            for ext in ('.gguf', '.safetensors', '.bin'):
                for f in sorted(glob.glob(os.path.join(glob.escape(p), f'*{ext}'))):
                    add(f)
        else:
            for shard in find_shards(p):
                add(shard)
    return total


def get_mem_available(meminfo_path: str) -> int:
    """Read MemAvailable from /proc/meminfo (or a test file)."""
    try:
        with open(meminfo_path, 'r') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    parts = line.split()
                    # Value is in kB
                    return int(parts[1]) * 1024
    except (OSError, IOError) as e:
        raise RuntimeError(f"cannot read meminfo {meminfo_path}: {e}")
    raise RuntimeError(f"MemAvailable not found in {meminfo_path}")


def main():
    parser = argparse.ArgumentParser(description='Check model size vs available memory')
    parser.add_argument('paths', nargs='+', help='Model files, directories, or size literals')
    parser.add_argument('--fraction', type=float, default=0.60, help='Fraction of MemAvailable to use (default 0.60)')
    parser.add_argument('--meminfo', default='/proc/meminfo', help='Path to meminfo file')
    args = parser.parse_args()

    # Determine total model size
    total = 0
    on_disk: list[str] = []
    for p in args.paths:
        # Try as size literal first
        try:
            total += parse_size_literal(p)
            continue
        except ValueError:
            pass
        # Must be a file or directory
        if not os.path.exists(p):
            print(f"ERROR file not found: {p}", file=sys.stderr)
            return 2
        if sum_files([p]) == 0:
            print(f"ERROR no model bytes found in {p} (refusing: an empty path must not pass)", file=sys.stderr)
            return 2
        on_disk.append(p)
    # One call for every path, so naming several shards (or a shard and its directory) never double-counts.
    total += sum_files(on_disk)

    # Get available memory
    try:
        avail = get_mem_available(args.meminfo)
    except RuntimeError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2

    limit = int(avail * args.fraction)

    if total <= limit:
        print(f"PASS total={total} avail={avail} limit={limit}")
        return 0
    else:
        print(f"FAIL total={total} avail={avail} limit={limit} reason=exceeds")
        return 1


if __name__ == '__main__':
    sys.exit(main())
