#!/usr/bin/env python3
"""Validate D2 diagrams against the justfile recipe set.

Usage:
    scripts/diagram_check.py [--justfile PATH] [--summary-file PATH] FILE.d2...

Checks:
  1. Every 'just <name>' reference in the diagram must be a real recipe.
  2. If the diagram declares '# procedural-chart' on its first line, every
     transition edge must have a label of the form 'cmd: <text> | pass: <text>'.

Exit codes:
  0  all checks passed
  1  validation error
  2  file missing / usage error
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

JUST_RE = re.compile(r"\bjust[ ]+([a-z0-9][a-z0-9_-]*)")
TRANSITION_RE = re.compile(r"->.*:")
CMD_PASS_RE = re.compile(r"cmd:\s*.+?\s*\|\s*pass:\s*.+?")


def edge_label(line):
    """Return None if the line has no edge operator outside quotes, else the label text after the edge's colon.

    Arrows and colons inside double-quoted strings are label text, never syntax.
    """
    in_quote = False
    last_arrow = -1
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and in_quote:
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and line.startswith("->", i):
            last_arrow = i
            i += 2
            continue
        i += 1
    if last_arrow < 0:
        return None
    rest, in_quote = line[last_arrow + 2:], False
    for j, ch in enumerate(rest):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            return rest[j + 1:].strip()
    return ""


def get_recipes(justfile=None, summary_file=None):
    """Return set of public recipe names."""
    if summary_file:
        text = Path(summary_file).read_text()
        return set(text.split())
    cmd = ["just", "--summary"]
    if justfile:
        cmd = ["just", "--justfile", justfile, "--summary"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL: just --summary failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return set(result.stdout.split())


def check_file(path, recipes):
    """Check one D2 file. Returns list of error strings."""
    errors = []
    p = Path(path)
    if not p.exists():
        return [f"FAIL {path}: file not found"]

    text = p.read_text()
    lines = text.splitlines()

    # Determine if this is a procedural chart
    procedural = len(lines) > 0 and lines[0].strip() == "# procedural-chart"

    refs = 0
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Check just references
        for match in JUST_RE.finditer(line):
            refs += 1
            recipe = match.group(1)
            if recipe not in recipes:
                errors.append(f"FAIL {path}:{i}: unknown recipe {recipe}")

        # Check transition labels in procedural charts
        label = edge_label(line) if procedural else None
        if label is not None:
            if not CMD_PASS_RE.search(label):
                errors.append(f"FAIL {path}:{i}: transition lacks cmd/pass")

    return errors, refs


def main():
    parser = argparse.ArgumentParser(
        description="Validate D2 diagrams against justfile recipes"
    )
    parser.add_argument("files", nargs="+", help="D2 files to check")
    parser.add_argument("--justfile", help="Path to justfile")
    parser.add_argument("--summary-file", help="File with whitespace-separated recipe names")
    args = parser.parse_args()

    recipes = get_recipes(args.justfile, args.summary_file)

    missing = [f for f in args.files if not Path(f).exists()]
    if missing:
        for f in missing:
            print(f"FAIL {f}: file not found")
        sys.exit(2)

    all_errors = []
    total_refs = 0
    for f in args.files:
        result = check_file(f, recipes)
        if isinstance(result, list):
            all_errors.extend(result)
        else:
            errors, refs = result
            all_errors.extend(errors)
            total_refs += refs

    if all_errors:
        for e in all_errors:
            print(e)
        sys.exit(1)

    print(f"PASS {total_refs} references in {len(args.files)} files")
    sys.exit(0)


if __name__ == "__main__":
    main()
