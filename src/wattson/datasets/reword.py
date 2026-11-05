"""Re-render a frozen suite's fit prompts in the current wording, as a new frozen suite.

v1 fit prompts said "applied to bus B" for a load-scale fit, which scales every load
(``generate.fit_prompt``). This rewrites a suite's fit prompts from each row's own inputs:

1. the inputs must reproduce the row's existing prompt exactly under the old wording (with or
   without the interval, for the judgement suite's open-interval variant), or the row is
   refused, since a prompt that cannot be reproduced cannot be safely reworded;
2. the prompt is then rendered in the current wording. Inputs, ids, expected answers and every
   other row stay as they are (example ids hash the inputs, not the prompt);
3. the reworded file is frozen beside a manifest naming its source and the wording.

A row whose prompt an earlier rewrite changed beyond the template (``pf-unsolvable``) is
rebuilt from the reworded source instead (``unsolvable.main``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from wattson.datasets.generate import CURRENT_WORDING, fit_prompt

FIT_SUBFAMILIES: frozenset[str] = frozenset({"pf-load-scale-fit", "pf-line-rating-fit", "pf-gen-dispatch-fit"})


def reword(row: dict[str, Any], old: str = "v1") -> dict[str, Any]:
    """The row with its fit prompt in the current wording; other subfamilies are returned unchanged.

    Raises:
        ValueError: if the inputs do not reproduce the existing prompt in the old wording.
    """
    if row["subfamily"] not in FIT_SUBFAMILIES:
        return row
    inputs, prompt = row["task"]["inputs"], row["task"]["prompt"]
    for interval in (True, False):
        if fit_prompt(row["subfamily"], inputs, old, interval) == prompt:
            new = fit_prompt(row["subfamily"], inputs, CURRENT_WORDING, interval)
            return {**row, "task": {**row["task"], "prompt": new, "prompt_wording": CURRENT_WORDING}}
    if prompt in {fit_prompt(row["subfamily"], inputs, CURRENT_WORDING, i) for i in (True, False)}:
        return row
    raise ValueError(f"{row['example_id']}: its inputs do not reproduce its prompt in wording {old}")


def main() -> int:
    """Write the reworded suite and its manifest."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, required=True, help="A frozen examples.jsonl")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen suite directory")
    rows = [json.loads(line) for line in args.source.read_text().splitlines() if line.strip()]
    reworded = [reword(row) for row in rows]
    changed = sum(a["task"]["prompt"] != b["task"]["prompt"] for a, b in zip(rows, reworded))
    args.out_dir.mkdir(parents=True)
    examples = args.out_dir / "examples.jsonl"
    with examples.open("x") as stream:
        for row in reworded:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    source_manifest = args.source.parent / "manifest.json"
    (args.out_dir / "manifest.json").write_text(json.dumps({
        "suite": f"{args.source.parent.name}-reworded-{CURRENT_WORDING}", "source": str(args.source),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "source_manifest": json.loads(source_manifest.read_text()) if source_manifest.is_file() else None,
        "prompt_wording": CURRENT_WORDING, "n_tasks": len(reworded), "prompts_reworded": changed,
        "examples_sha256": hashlib.sha256(examples.read_bytes()).hexdigest()}, indent=2) + "\n")
    print(json.dumps({"tasks": len(reworded), "prompts_reworded": changed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
