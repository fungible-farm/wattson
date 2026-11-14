#!/usr/bin/env python3
"""Gold values (#130 step 4): run each gold SQL once and write its answer values, so the leakage lint has something to check.

Usage:
    python3 scripts/gold_values.py --questions Q.json --out GOLD.json

Q.json is a JSON list of objects `{"id": ..., "question": ..., "sql": ...}` (configs/nem-multitable-questions.proposed.json).
Each item's `sql` is executed by `scripts/demo_timeseries_qa.run_sql` — the same path the harness uses for ground truth: ONE
read-only statement in the `wattson-nem` container with `--network=none`. The returned rows are flattened with that module's
`flat()`, so `values` is exactly the sequence of cells the ground truth consists of, in row order. GOLD.json is the input with a
`values` key added, in the input order, as a JSON list of `{"id", "question", "sql", "values"}` with indent 2 and a trailing
newline — the shape `scripts/runbook_leakage_lint.py --gold` reads, including its `values` check. No model, stdlib only.

Values keep the types the container's own JSON gave them, so numbers stay numbers and strings stay strings: `1234.5` is written
as `1234.5`, not `"1234.5"`, because the lint compares numbers numerically (that is what catches `1,234.5` in prose) and a quoted
number would silently downgrade the check. Output is deterministic: same questions file, same container answer, same bytes.

Failure is all-or-nothing. If any statement errors, this prints `FAIL gold-values: <id> <error>` and exits 1 WITHOUT writing the
output file — a partially written gold file would make the lint report `PASS values` about the questions whose SQL never ran,
which is the one thing a lint result must never be.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import demo_timeseries_qa as arm1  # noqa: E402


class InputError(Exception):
    """The questions file cannot be read as the script was told to read it."""


class GoldError(Exception):
    """A gold SQL statement did not return an answer, so no gold file can be written."""


def fail(msg: str) -> int:
    print(f"FAIL gold-values: {msg}")
    return 1


def load_questions(path: str) -> list[dict[str, Any]]:
    """The questions file as a list of entries, each with a usable id and sql; input order is preserved."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read questions file {path}: {exc.strerror or exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"questions file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise InputError(f"questions file {path} must hold a JSON list of objects")
    for pos, item in enumerate(data):
        if not isinstance(item, dict):
            raise InputError(f"questions entry {pos} in {path} is not an object")
        ident = item.get("id")
        if not isinstance(ident, str) or not ident.strip():
            raise InputError(f"questions entry {pos} in {path} has no id")
        if not isinstance(item.get("sql"), str) or not item["sql"].strip():
            raise InputError(f'questions entry "{ident}" has no sql')
        if "question" in item and not isinstance(item["question"], str):
            raise InputError(f'questions entry "{ident}" has a non-string question')
    return data


def gold_entry(item: dict[str, Any]) -> dict[str, Any]:
    """The input fields plus the flattened answer values of the item's own SQL.

    Raises GoldError if the container reports an error, or answers with something that is not a list of rows: in both cases the
    values for this id are unknown, and unknown values must not ship as gold.
    """
    result = arm1.run_sql(item["sql"])
    ident = str(item["id"])
    if not isinstance(result, dict) or "error" in result:
        error = result["error"] if isinstance(result, dict) else f"not an object: {result!r}"
        raise GoldError(f"{ident} {error}")
    if not isinstance(result.get("rows"), list):
        raise GoldError(f"{ident} returned no rows: {str(result)[:200]}")
    return {
        "id": item["id"],
        "question": item.get("question", ""),
        "sql": item["sql"],
        "values": arm1.flat(result["rows"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gold_values.py",
        description="Run each question's gold SQL in the no-network DuckDB container and write {id, question, sql, values}.",
    )
    parser.add_argument("--questions", required=True, metavar="FILE")
    parser.add_argument("--out", required=True, metavar="FILE")
    args = parser.parse_args(argv)

    try:
        questions = load_questions(args.questions)
        gold = [gold_entry(item) for item in questions]
    except InputError as exc:
        return fail(str(exc))
    except GoldError as exc:
        return fail(str(exc))

    # Only now, with every value in hand, does the output path get written to: one write of the whole file.
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gold, indent=2) + "\n", encoding="utf-8")
    print(f"PASS gold-values: {len(gold)} questions -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
