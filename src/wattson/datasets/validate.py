"""M04 — dataset validation: schema conformance, provenance completeness,
and execution-evidence presence.

Usage:
    uv run python powercoder/datasets/validate.py --examples <examples.jsonl> [--manifest <manifest.json>]

Checks (each example):
  1. Conforms to schemas/training-example.schema.json (Draft 2020-12).
  2. 100% provenance completeness: every provenance field present and
     non-empty (generator, generator_version, seed, git_commit, created_utc,
     licence, source_refs).
  3. Every VERIFIED / ACCEPTED record carries full execution evidence:
     case_id, solver, solver_version, input_hash, result_hash, status.

Exit code 0 iff every example passes all checks; 1 otherwise. This is the
gate the mission's "100% of accepted records have full provenance" and
"every verified record has execution evidence" acceptance criteria are
reduced to.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import jsonschema  # noqa: E402

from wattson.datasets import common  # noqa: E402

SCHEMA_PATH: Path = Path(__file__).with_name("training-example.schema.json")

# Provenance fields that must be present AND non-empty on every example.
PROVENANCE_REQUIRED_FIELDS: tuple[str, ...] = (
    "generator",
    "generator_version",
    "seed",
    "git_commit",
    "created_utc",
    "licence",
    "source_refs",
)

# Execution-evidence fields that must be present AND non-empty on every
# verified / accepted record.
EXECUTION_REQUIRED_FIELDS: tuple[str, ...] = (
    "case_id",
    "solver",
    "solver_version",
    "input_hash",
    "result_hash",
    "status",
)

# Lifecycle states that require execution evidence.
EVIDENCE_REQUIRED_STATES: frozenset[str] = frozenset({"verified", "accepted"})


def _nonempty(value: Any) -> bool:
    """True if the value is present and not empty (None / '' / [] / {})."""
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


def validate_example(example: dict[str, Any], validator: jsonschema.Draft202012Validator) -> list[str]:
    """Return a list of human-readable problems for one example (empty = pass).

    Args:
        example: A parsed training-example record.
        validator: A jsonschema Draft202012Validator for the training schema.

    Returns:
        A list of problem strings. Empty if the example passes all checks.
    """
    problems: list[str] = []

    # 1. Schema conformance.
    for err in sorted(validator.iter_errors(example), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        problems.append(f"schema: {loc}: {err.message}")

    # 2. Provenance completeness (100% on every example).
    prov = example.get("provenance")
    if not isinstance(prov, dict):
        problems.append("provenance: missing or not an object")
    else:
        for field in PROVENANCE_REQUIRED_FIELDS:
            if not _nonempty(prov.get(field)):
                problems.append(f"provenance: {field} missing or empty")

    # 3. Execution evidence on verified / accepted records.
    lifecycle = example.get("lifecycle") or {}
    state = lifecycle.get("status")
    if state in EVIDENCE_REQUIRED_STATES:
        execution = example.get("execution")
        if not isinstance(execution, dict):
            problems.append("execution: missing or not an object (required for verified/accepted)")
        else:
            for field in EXECUTION_REQUIRED_FIELDS:
                if not _nonempty(execution.get(field)):
                    problems.append(f"execution: {field} missing or empty (required for {state})")

    return problems


def validate_dataset(examples_path: Path) -> dict[str, Any]:
    """Validate a whole examples.jsonl file.

    Args:
        examples_path: Path to the examples.jsonl file.

    Returns:
        A report dict: {total, passed, failed, failures: [{example_id, problems}]}.
    """
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.Draft202012Validator(schema)
    validator.check_schema(schema)  # fail fast on a malformed schema

    total = 0
    failures: list[dict[str, Any]] = []
    with open(examples_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            example = json.loads(line)
            problems = validate_example(example, validator)
            if problems:
                failures.append({
                    "example_id": example.get("example_id", "<missing>"),
                    "problems": problems,
                })

    return {
        "total": total,
        "passed": total - len(failures),
        "failed": len(failures),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", required=True, type=Path, help="Path to examples.jsonl")
    parser.add_argument("--manifest", type=Path, default=None, help="Optional manifest.json to cross-check the record count")
    args = parser.parse_args(argv)

    report = validate_dataset(args.examples)

    print(f"[validate] {args.examples}")
    print(f"[validate] total={report['total']} passed={report['passed']} failed={report['failed']}")

    # Cross-check the manifest record count, if provided.
    if args.manifest is not None and args.manifest.exists():
        with open(args.manifest, encoding="utf-8") as f:
            manifest = json.load(f)
        manifest_total = manifest.get("total_examples")
        if manifest_total is not None and manifest_total != report["total"]:
            print(f"[validate] WARNING: manifest total_examples={manifest_total} != {report['total']} examples on disk")

    if report["failures"]:
        print(f"[validate] FAILURES ({report['failed']}):")
        for failure in report["failures"][:20]:
            print(f"  {failure['example_id']}:")
            for problem in failure["problems"]:
                print(f"    - {problem}")
        if report["failed"] > 20:
            print(f"  ... and {report['failed'] - 20} more")
        return 1

    print("[validate] PASS: all examples schema-conformant, 100% provenance, execution evidence present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
