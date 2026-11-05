#!/usr/bin/env python3
"""Generate a D2 state chart from the justfile recipes.

Usage:
    scripts/justfile_diagram.py [--justfile PATH] [--check FILE]

Without --check, prints the D2 graph to stdout.
With --check FILE, compares FILE against the generated output and exits 1
if they differ (drift guard).

The generated graph has one node per public recipe with its doc comment
(truncated to 50 chars) and edges for recipe dependencies (dependency -> dependent, i.e. execution order).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def get_justfile_json(justfile=None):
    """Parse justfile via just --dump --dump-format json."""
    cmd = ["just", "--dump", "--dump-format", "json"]
    if justfile:
        cmd = ["just", "--justfile", justfile, "--dump", "--dump-format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL: just --dump failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return json.loads(result.stdout)


def generate_d2(justfile=None):
    """Generate D2 graph from justfile."""
    data = get_justfile_json(justfile)
    recipes = data["recipes"]

    lines = ["direction: down"]
    lines.append("")

    # Collect all recipe names for dependency resolution
    recipe_names = set()
    for name, recipe in recipes.items():
        if not recipe.get("private", False):
            recipe_names.add(name)

    # Generate nodes
    for name in sorted(recipe_names):
        recipe = recipes[name]
        doc = recipe.get("doc") or ""
        # Truncate doc to 50 chars
        if len(doc) > 50:
            doc = doc[:47] + "..."
        # D2 string: no embedded double quotes or backslashes; a newline separates name and doc.
        doc = doc.replace("\\", "/").replace('"', "'")
        label = name + (f"\\n{doc}" if doc else "")
        lines.append(f'"{name}": "{label}"')

    lines.append("")

    # Generate edges for dependencies
    for name in sorted(recipe_names):
        recipe = recipes[name]
        for dep in recipe.get("dependencies", []):
            dep_name = dep.get("recipe", "")
            if dep_name in recipe_names:
                lines.append(f'"{dep_name}" -> "{name}"')  # arrow = runs first -> runs next

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Generate D2 state chart from justfile recipes"
    )
    parser.add_argument("--justfile", help="Path to justfile")
    parser.add_argument("--check", help="Compare against this file (exit 1 if different)")
    args = parser.parse_args()

    d2 = generate_d2(args.justfile)

    if args.check:
        p = Path(args.check)
        if not p.exists():
            print(f"FAIL {args.check}: file not found")
            sys.exit(2)
        existing = p.read_text()
        if existing != d2:
            print(f"FAIL {args.check}: drift detected")
            sys.exit(1)
        print(f"PASS {args.check}: no drift")
    else:
        sys.stdout.write(d2)


if __name__ == "__main__":
    main()
