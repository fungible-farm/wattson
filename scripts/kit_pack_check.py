"""Fail-closed pre-pack check: walk a directory and reject anything not explicitly allowed.

    python scripts/kit_pack_check.py DIR [--allow-file FILE]

Without an allow file, every file is UNLISTED (fail closed). Deny rules always win, even if
a path matches an allow glob.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path


def load_allow(path: Path) -> list[str]:
    lines = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def is_denied(rel: str) -> str | None:
    """Return a deny reason or None. rel is POSIX-style relative path."""
    parts = rel.split("/")
    name = parts[-1].lower()
    stem = Path(name).stem.lower()
    suffix = Path(name).suffix.lower()

    # *.m under any 'data' path component (CSIRO case files)
    if suffix == ".m" and any(p.lower() == "data" for p in parts[:-1]):
        return "CSIRO .m file under data/"

    # Private workbooks
    if suffix in (".xlsm", ".xlsx", ".xls"):
        return "private workbook"

    # Env files
    if name == ".env" or name.startswith(".env."):
        return "env file"

    # Keys/certs/keystores
    if suffix in (".pem", ".key", ".p12", ".pfx", ".p8", ".jks", ".keystore", ".kdbx"):
        return "key/cert file"
    if stem in ("id_rsa", "id_ed25519") or name.startswith(("id_rsa", "id_ed25519", "id_dsa", "id_ecdsa")):
        return "key/cert file"

    # Tool credential files
    if name in (".netrc", ".npmrc", ".pypirc", ".pgpass", ".dockercfg"):
        return "tool credential file"

    # Credential/secret in filename
    if "credential" in name or "secret" in name:
        return "credential/secret in name"

    return None


def matches_allow(rel: str, allow: list[str]) -> bool:
    for pattern in allow:
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
        elif fnmatch.fnmatch(rel, pattern):
            return True
    return False


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("ERROR: missing directory argument")
        return 2

    target = Path(args[0])
    if not target.is_dir():
        print(f"ERROR: not a directory: {target}")
        return 2

    allow_path = None
    if "--allow-file" in args:
        idx = args.index("--allow-file")
        if idx + 1 >= len(args):
            print("ERROR: --allow-file requires a path")
            return 2
        allow_path = Path(args[idx + 1])
        if not allow_path.is_file():
            print(f"ERROR: allow file not found: {allow_path}")
            return 2

    allow = load_allow(allow_path) if allow_path else []

    # Resolve allow path relative to target so we can exclude it from the walk
    exclude_rel = None
    if allow_path is not None:
        try:
            exclude_rel = allow_path.resolve().relative_to(target.resolve()).as_posix()
        except ValueError:
            pass  # allow file is outside target, nothing to exclude

    denied = 0
    unlisted = 0
    total = 0

    # Collect all relative paths first, then sort for deterministic output
    rel_paths = []
    symlinks: set[str] = set()
    for root, dirs, files in os.walk(target, followlinks=False):
        # Skip .git directories
        dirs[:] = [d for d in dirs if d != ".git"]
        # A symlink (to a file OR a directory) is never followed and never allowed: kit pack could follow it out of the tree.
        for d in dirs:
            full = Path(root) / d
            if full.is_symlink():
                rel = full.relative_to(target).as_posix()
                symlinks.add(rel)
                rel_paths.append(rel)
        for f in files:
            full = Path(root) / f
            rel = full.relative_to(target).as_posix()
            if exclude_rel and rel == exclude_rel:
                continue
            if full.is_symlink():
                symlinks.add(rel)
            rel_paths.append(rel)

    rel_paths.sort()

    for rel in rel_paths:
        total += 1
        reason = "symlink (not followed, never packed)" if rel in symlinks else is_denied(rel)
        if reason:
            print(f"DENY {rel}: {reason}")
            denied += 1
        elif not matches_allow(rel, allow):
            print(f"UNLISTED {rel}")
            unlisted += 1

    if denied > 0 or unlisted > 0:
        print(f"FAIL denied={denied} unlisted={unlisted} files={total}")
        return 1
    else:
        print(f"PASS files={total}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
