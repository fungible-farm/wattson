"""Render the paired task scorecards without inventing missing benchmark results."""

from __future__ import annotations

import json
from pathlib import Path
import statistics
import sys


def render(root: Path) -> str:
    """Compare observed pass/call counts and task wall time; name the confounding factors."""
    lines = ["# Local GenCost task comparison", "",
             "Different models/devices; these measurements do not isolate an inference-backend speedup.", "",
             "| Arm | Passed | Correct task calls | Mean task wall time |",
             "|---|---:|---:|---:|"]
    for backend in ("sm3lly", "candle"):
        rows = json.loads((root / backend / "scorecard.json").read_text())["tasks"]
        passes = sum(row["passed"] for row in rows)
        calls = sum(row["correct_task_call"] for row in rows)
        duration = statistics.mean(row["wall_seconds"] for row in rows)
        lines.append(f"| {backend} | {passes}/{len(rows)} | {calls}/{len(rows)} | {duration:.3f} s |")
    document = "\n".join(lines) + "\n"
    (root / "comparison.md").write_text(document)
    return document


if __name__ == "__main__":
    print(render(Path(sys.argv[1])))
