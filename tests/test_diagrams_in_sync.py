"""The state charts in docs/diagrams must only name commands that exist (self-documenting means it cannot drift).

CI has no `just`, so recipe names are read from the justfile text by a small parser; where `just` is installed, the parser is
checked against `just --summary` so the parser itself cannot drift.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)(.*)$")
COLON = re.compile(r":(?!=)")  # a ':' that is not the start of ':='
NOT_RECIPES = {"set", "alias", "import", "mod", "export"}


def recipes_from_justfile(text: str) -> set[str]:
    """Public recipe names: a name at column 0 followed by parameters and a ':' that is not ':='; '_private' and settings excluded."""
    names: set[str] = set()
    for line in text.splitlines():
        m = HEAD.match(line)  # indented lines (recipe bodies) never match: the name must start at column 0
        if not m or m.group(1) in NOT_RECIPES:
            continue
        rest = m.group(2).lstrip()
        if rest.startswith((":=", "=")):  # a variable assignment, not a recipe
            continue
        if COLON.search(rest):  # parameters (which may contain '=') then the recipe's ':'
            names.add(m.group(1))
    return names


def test_parser_finds_known_recipes() -> None:
    names = recipes_from_justfile((ROOT / "justfile").read_text())
    assert {"up", "down", "doctor", "gateway-up", "gateway-down"} <= names
    assert "registry" not in names  # a variable assignment (:=) is not a recipe


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed here; the parser alone is used")
def test_parser_agrees_with_just_summary() -> None:
    summary = subprocess.run(["just", "--justfile", str(ROOT / "justfile"), "--summary"], capture_output=True, text=True, check=True)
    assert recipes_from_justfile((ROOT / "justfile").read_text()) == set(summary.stdout.split())


def test_charts_name_only_real_recipes(tmp_path: Path) -> None:
    # recipes.d2 is generated from the justfile (its doc text may contain words like "just in case"); the hand-written charts are checked.
    charts = sorted(p for p in (ROOT / "docs" / "diagrams").glob("*.d2") if p.name != "recipes.d2")
    assert charts, "no diagrams found"
    names = tmp_path / "recipes.txt"
    names.write_text(" ".join(sorted(recipes_from_justfile((ROOT / "justfile").read_text()))))
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "diagram_check.py"), "--summary-file", str(names), *map(str, charts)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
