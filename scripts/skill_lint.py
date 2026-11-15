#!/usr/bin/env python3
"""Lint skills: frontmatter name/description plus relative markdown links. Exit 0 clean, 1 any FAIL, 2 unreadable path."""
from __future__ import annotations

import re
import sys
from pathlib import Path

LINK = re.compile(r"\]\(\s*([^()\s]+)")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:")
FOLDED = {"", ">", "|", ">-", "|-", ">+", "|+"}


def frontmatter(text: str) -> list[str] | None:
    """Lines between the leading --- markers, or None when there are none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    ends = [i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")]
    return lines[1:ends[0]] if ends else None


def field(block: list[str], key: str) -> str:
    """Value of a top-level frontmatter key, joining folded or block-scalar lines."""
    for n, line in enumerate(block):
        m = re.match(rf"^{key}:[ \t]*(.*)$", line)
        if not m:
            continue
        value = m.group(1).strip().strip("\"'")
        if value not in FOLDED:
            return value
        rest = block[n + 1:]
        stop = next((i for i, l in enumerate(rest) if KEY.match(l) and not l[:1].isspace()), len(rest))
        return " ".join(l.strip() for l in rest[:stop] if l.strip())
    return ""


def check(md: Path) -> str | None:
    """First problem found in a SKILL.md, or None when it is clean."""
    text = md.read_text(encoding="utf-8", errors="replace")
    block = frontmatter(text)
    if block is None:
        return "missing YAML frontmatter"
    name = field(block, "name")
    if not name:
        return "empty or missing 'name'"
    if name != md.parent.name:
        return f"name '{name}' != directory '{md.parent.name}'"
    if not field(block, "description"):
        return "empty or missing 'description'"
    for target in LINK.findall(text):
        if SCHEME.match(target) or target.startswith("#"):
            continue
        if not (md.parent / target.split("#")[0]).exists():
            return f"broken link {target}"
    return None


def main(argv: list[str]) -> int:
    dirs = [Path(a) for a in argv] or sorted(p.parent for p in Path("skills").glob("*/SKILL.md"))
    if not dirs:
        print("ERROR skill-lint: no skills/*/SKILL.md to check", file=sys.stderr)
        return 2
    rc = 0
    for directory in dirs:
        try:
            reason = check(directory / "SKILL.md")
        except OSError as exc:
            print(f"ERROR skill-lint {directory}: unreadable ({exc.strerror or exc})")
            rc = 2
            continue
        label = directory.resolve().name
        print(f"FAIL skill-lint {label}: {reason}" if reason else f"PASS skill-lint {label}")
        rc = rc or (1 if reason else 0)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
