"""Fail unless an OCI image index lists every required platform.

    docker buildx imagetools inspect IMAGE:TAG --raw | python scripts/check_platforms.py linux/amd64 linux/arm64

Why: a CI matrix mistake once published an arm64-only "multi-architecture" image and every job was green. Attestation manifests
(platform unknown/unknown) are ignored; only real platforms count.
"""

from __future__ import annotations

import json
import sys


def platforms(index: dict) -> set[str]:
    return {f"{m['platform']['os']}/{m['platform']['architecture']}" for m in index.get("manifests", [])
            if m.get("platform") and m["platform"].get("architecture") not in (None, "unknown")}


def main(required: list[str]) -> int:
    found = platforms(json.load(sys.stdin))
    print(f"published platforms: {sorted(found)}")
    missing = [p for p in required if p not in found]
    for p in missing:
        print(f"::error::missing platform {p}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
