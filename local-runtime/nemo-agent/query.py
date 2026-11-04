"""Read NEMO's committed GenCost literals, with exact source-line provenance.

This intentionally avoids importing nemo.__init__, which starts its simulation
dependency graph. It is a read-only metadata adapter, not a replacement optimizer.
No dynamic exec/eval, Excel formula evaluation, or inferred missing cost is used.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import TypedDict

# Immutable upstream source and explicitly reviewed report vintage.
NEMO_COMMIT: str = "0251ee41cf806915905ad43934520d8d258d8810"
MODULE: str = "nemo/costs/gencost2025.py"
REPORT: str = "CSIRO GenCost 2024-25 (NEMO transcription)"
# Enumerated projection years/scenarios actually present in this upstream module.
YEARS: tuple[int, ...] = (2030, 2040, 2050)
SCENARIOS: tuple[str, ...] = ("CP", "NZE2050", "NZEPost2050")


class CostResult(TypedDict):
    """One observed source literal plus its retrieval provenance."""

    report: str
    technology: str
    year: int
    scenario: str
    capital_cost: int | float
    units: str
    source_commit: str
    source_sha256: str
    source_url: str
    adapter: str


def lookup(technology: str, year: int, scenario: str, root: Path = Path("/opt/nemo-source")) -> CostResult:
    """Find exactly one capcost literal in the chosen class; refuse missing data.

    Args: technology: NEMO's exact generator identifier. year/scenario: approved
        enum values. root: installed immutable NEMO source tree.
    Returns: Source value and line-level provenance.
    Raises: ValueError on an unapproved selector or absent/ambiguous source value.
    """
    if year not in YEARS or scenario not in SCENARIOS or not technology.isidentifier():
        raise ValueError("unapproved selector")
    source = (root / MODULE).read_bytes()
    tree = ast.parse(source)
    class_name = f"GenCost2025_{year}_{scenario}"
    matches: list[tuple[int | float, int]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for method in node.body:
            if not isinstance(method, ast.FunctionDef) or method.name != "__init__":
                continue
            capital_table = False
            for statement in method.body:
                if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                    continue
                target = statement.targets[0]
                if isinstance(target, ast.Name) and target.id == "table":
                    capital_table = isinstance(statement.value, ast.Attribute) and statement.value.attr == "capcost_per_kw"
                if not capital_table or not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Name) or target.value.id != "table":
                    continue
                key = target.slice
                if isinstance(key, ast.Attribute) and isinstance(key.value, ast.Name) and key.value.id == "tech" and key.attr == technology:
                    if not isinstance(statement.value, ast.Constant) or type(statement.value.value) not in (int, float):
                        raise ValueError("cost is not a literal; adapter review required")
                    matches.append((statement.value.value, statement.lineno))
    if len(matches) != 1:
        raise ValueError("unknown/ambiguous technology; no value was guessed")
    cost, line = matches[0]
    return {
        "report": REPORT, "technology": technology, "year": year, "scenario": scenario,
        "capital_cost": cost, "units": "AUD/kW", "source_commit": NEMO_COMMIT,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_url": f"https://github.com/bje-/NEMO/blob/{NEMO_COMMIT}/{MODULE}#L{line}",
        "adapter": "literal capital-cost metadata only; not optimizer execution",
    }


def check(root: Path) -> None:
    """Re-derive the three independently source-reviewed fixture values exactly."""
    fixture = json.loads(Path(__file__).with_name("expected_costs.json").read_text())
    for technology, expected in fixture["capital_costs"].items():
        actual = lookup(technology, YEARS[0], SCENARIOS[0], root)
        if actual["capital_cost"] != expected:
            raise AssertionError((technology, actual, expected))
    print("PASS: NEMO source literals match reviewed fixture")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--technology", default="PV1Axis")
    parser.add_argument("--year", type=int, choices=YEARS, default=YEARS[0])
    parser.add_argument("--scenario", choices=SCENARIOS, default=SCENARIOS[0])
    parser.add_argument("--root", type=Path, default=Path("/opt/nemo-source"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check(args.root)
    else:
        print(json.dumps(lookup(args.technology, args.year, args.scenario, args.root), sort_keys=True))
