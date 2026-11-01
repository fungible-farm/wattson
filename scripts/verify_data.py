"""Check data/ against data/MANIFEST.json. The logic lives in ``wattson.datacheck`` so the container's ``wattson doctor`` reports the same.

    uv run scripts/verify_data.py            # verify
    uv run scripts/verify_data.py --write    # regenerate MANIFEST.json after an intended change
"""

from __future__ import annotations

import argparse
import sys

from wattson import datacheck


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="regenerate data/MANIFEST.json")
    arguments = parser.parse_args()
    root = datacheck.data_dir()
    if arguments.write:
        print(f"wrote {datacheck.MANIFEST_NAME}: {datacheck.write(root)} files")
        return 0
    checked, problems = datacheck.verify(root)
    for line in problems:
        print(line)
    print(f"{'FAIL' if problems else 'PASS'}: {checked} files checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
