"""Run RestoreBench's own harness against a locally served model; summarise its result cells.

Runs inside RestoreBench's environment; ``restorebench_lab.py --step agent``
launches it. The harness, agents, prompts and scoring are RestoreBench's
(``restorebench.eval.harness.run_evaluation``); only the transport is local
(``local_provider.install``, a named stand-in).

Configuration 2 is RestoreBench's single agent with physics tools, the
closest of its three to this repo's PowerCoder agents (configuration 1 has no
tools; 3 splits analyst and executor across calls).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_provider import install  # noqa: E402

from restorebench.eval.harness import run_evaluation  # noqa: E402

# RestoreBench's single tool-using agent (see the module docstring).
CONFIGURATION: int = 2
# One greedy attempt per scenario, like this repo's evaluations (README: the
# published campaigns use 3 repetitions at the providers' default temperature).
REPETITIONS: int = 1
# Cells in flight; a local server batches at most its --max-num-seqs (8 in r6).
CONCURRENCY: int = 8


def main() -> int:
    """Run the requested cells, then write status counts per model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", action="append", required=True, help="Served model name; repeatable")
    parser.add_argument("--data-dir", type=Path, required=True, help="dataset/ieee118 or dataset/pegase89")
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="First N scenarios only (RestoreBench --limit counts cells)")
    args = parser.parse_args()
    ids = install(args.base_url, args.model)
    summary = run_evaluation(ids, configurations=[CONFIGURATION], results_dir=args.results_dir,
                             data_dir=args.data_dir, repetitions=REPETITIONS, concurrency=CONCURRENCY,
                             limit=args.limit * len(ids) if args.limit else None)
    cells = [json.loads(path.read_text()) for path in sorted((args.results_dir / "cells").glob("*.json"))]
    report = {
        "data_dir": str(args.data_dir), "configuration": CONFIGURATION, "repetitions": REPETITIONS,
        "planned": summary.n_planned, "run": summary.n_run, "skipped_as_done": summary.n_skipped,
        # Configurations 1 and 2 record their one model as llm_assignment.single_agent.
        "by_model": {model_id: dict(Counter(c["status"] for c in cells if c["llm_assignment"]["single_agent"] == model_id))
                     for model_id in ids},
    }
    for model_id, counts in report["by_model"].items():
        total = sum(counts.values())
        report.setdefault("success_rate", {})[model_id] = counts.get("SUCCESS", 0) / total if total else None
    (args.results_dir / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
