#!/usr/bin/env python3
"""Download, verify and install the cosign CLI (#47: signing for ModelKits).

Usage: python3 scripts/install_cosign.py [--version v3.1.4] [--dest DIR]

Integrity: the binary is checked against the checksums file published next to it BEFORE it is installed or run. That detects a
corrupted or swapped download; it is not a signature check on cosign itself (both files come from the same host).
Installs <repo root>/.tools/cosign/cosign (gitignored, like the kit CLI).
"""
import argparse
import hashlib
import os
import platform
import stat
import subprocess
import sys
import tempfile
import urllib.request

BASE = "https://github.com/sigstore/cosign/releases/download"


def main():
    parser = argparse.ArgumentParser(description="Install the cosign CLI")
    parser.add_argument("--version", default="v3.1.4")
    parser.add_argument("--dest", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tools", "cosign"))
    args = parser.parse_args()
    machine = platform.machine().lower()
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(machine)
    if arch is None:
        sys.exit(f"ERROR: unsupported architecture {machine}")
    target = os.path.join(args.dest, "cosign")
    if os.path.exists(target):
        out = subprocess.run([target, "version"], capture_output=True, text=True)
        if out.returncode == 0 and args.version.lstrip("v") in out.stdout:
            print(f"already installed {target} version={args.version}")
            return
    name = f"cosign-linux-{arch}"
    with tempfile.TemporaryDirectory() as tmp:
        binary, sums = os.path.join(tmp, name), os.path.join(tmp, "cosign_checksums.txt")
        for url, path in ((f"{BASE}/{args.version}/cosign_checksums.txt", sums), (f"{BASE}/{args.version}/{name}", binary)):
            print(f"downloading {url}")
            urllib.request.urlretrieve(url, path)
        expected = None
        for line in open(sums):
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == name:
                expected = parts[0].lower()
        if expected is None:
            sys.exit(f"ERROR checksum: {name} not in the checksums file")
        h = hashlib.sha256()
        with open(binary, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != expected:
            sys.exit(f"ERROR checksum: {name} expected {expected} got {h.hexdigest()}; nothing installed")
        os.makedirs(args.dest, exist_ok=True)
        os.replace(binary, target)
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    out = subprocess.run([target, "version"], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"ERROR: installed cosign failed to run: {out.stderr[-300:]}")
    print(f"installed {target} version={args.version}")


if __name__ == "__main__":
    main()
