"""Score every published witness of one corpus with RestoreBench's own scorer.

Runs inside RestoreBench's environment; ``restorebench_lab.py --step witness``
launches it. ``restorebench-score --batch`` cannot do this: it always reads the
IEEE 118 corpus (no data-dir option) and writes only an aggregate report, so
this calls the same ``score_attempt`` with each corpus's ``full/`` snapshots.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from restorebench.scoring.score_maneuvers import score_attempt


def main() -> int:
    """Write one line per witness: scenario, status, maneuvers applied."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, required=True, help="dataset/ieee118 or dataset/pegase89")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    witnesses = json.loads((args.corpus_dir / "private/witnesses.json").read_text())
    with args.out.open("x") as stream:
        for witness in witnesses:
            report = score_attempt({"scenario_id": witness["scenario_id"], "maneuvers": witness["maneuvers"]},
                                   data_dir=args.corpus_dir / "full")
            stream.write(json.dumps({"scenario_id": witness["scenario_id"], "status": report["status"],
                                     "n_maneuvers": report["n_maneuvers"], "witness_length": len(witness["maneuvers"])}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
