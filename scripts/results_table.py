#!/usr/bin/env python3
"""Turn evaluation run directories into the RESULTS.json that verdict.py consumes.

    results_table.py --arm NAME DERIVED_DIR SMOKE_DIR [--arm NAME2 D2 S2 ...] > RESULTS.json

Each arm is two run directories (derived and smoke) produced by the evaluation harness.
The script verifies each directory strictly, then aggregates into the schema that
scripts/verdict.py expects. Exit 0 on success, 2 on any verification or input error.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

UNANSWERED = frozenset({
    "incomplete_generation", "turn_budget_exhausted", "inference_timeout",
    "context_exhausted", "invalid_tool_protocol", "invalid_tool_arguments",
})

# Settings that must be present in every run's summary and identical across an arm's two runs.
CORE_FIELDS = [
    "model", "temperature", "max_turns", "max_tool_calls", "max_tokens",
    "tool_set", "first_turn_tool_choice", "enforce_provenance",
]
# Added to the harness later: runs made before may lack them; they must still agree (both absent counts as both None).
OPTIONAL_FIELDS = ["extra_body", "max_retries"]
MATCHING_FIELDS = CORE_FIELDS + OPTIONAL_FIELDS


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"{path}: {e}")


def read_results_jsonl(path):
    try:
        with open(path) as f:
            lines = [l for l in f if l.strip()]
    except OSError as e:
        fail(f"{path}: {e}")
    results = []
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            fail(f"{path} line {i+1}: {e}")
        if obj.get("kind") != "result":
            fail(f"{path} line {i+1}: expected kind 'result', got {obj.get('kind')}")
        results.append(obj["payload"])
    return results


def read_journal(path):
    try:
        with open(path) as f:
            lines = [l for l in f if l.strip()]
    except OSError as e:
        fail(f"{path}: {e}")
    if not lines:
        fail(f"{path}: empty journal")
    try:
        last = json.loads(lines[-1])
    except json.JSONDecodeError as e:
        fail(f"{path}: last line is not JSON: {e}")
    if not isinstance(last, dict) or last.get("kind") != "terminal":
        fail(f"{path}: last event is not terminal")
    state = (last.get("payload") or {}).get("state") if isinstance(last.get("payload"), dict) else None
    if not isinstance(state, str) or not state:
        fail(f"{path}: terminal event has no state")
    return state


def verify_dir(path):
    """Verify a run directory and return (summary, results, terminal_states)."""
    p = Path(path)
    if not p.is_dir():
        fail(f"{path} is not a directory")

    summary = read_json(p / "summary.json")
    if not summary.get("complete"):
        fail(f"{path}: summary.complete is not true")
    if not summary.get("independence_audit", {}).get("clean"):
        fail(f"{path}: summary.independence_audit.clean is not true")

    n_tasks = summary.get("n_tasks")
    if not isinstance(n_tasks, int) or n_tasks <= 0:
        fail(f"{path}: summary.n_tasks invalid ({n_tasks})")

    results = read_results_jsonl(p / "results.jsonl")
    if len(results) != n_tasks:
        fail(f"{path}: {len(results)} result rows but n_tasks={n_tasks}")

    ids = [r.get("example_id") for r in results]
    if len(set(ids)) != len(ids):
        fail(f"{path}: duplicate example_ids in results")

    n_pass = sum(1 for r in results if r.get("passed") is True)
    if summary.get("n_pass") != n_pass:
        fail(f"{path}: summary.n_pass={summary.get('n_pass')} but computed {n_pass}")

    expected_rate = n_pass / n_tasks
    pass_at_1 = summary.get("pass_at_1")
    if isinstance(pass_at_1, bool) or not isinstance(pass_at_1, (int, float)):
        fail(f"{path}: summary.pass_at_1 is missing or not a number")
    if abs(pass_at_1 - expected_rate) > 1e-9:
        fail(f"{path}: summary.pass_at_1={pass_at_1} but computed {expected_rate}")

    for field in CORE_FIELDS:
        if field not in summary:
            fail(f"{path}: summary is missing required setting '{field}'")
    if not isinstance(summary["enforce_provenance"], bool):
        fail(f"{path}: summary.enforce_provenance must be a boolean")

    journals = sorted(f.name for f in p.glob("*.jsonl") if f.name != "results.jsonl")
    if len(journals) != n_tasks:
        fail(f"{path}: {len(journals)} journal files but n_tasks={n_tasks}")

    terminal_states = []
    for r in results:
        eid = r.get("example_id")
        if not isinstance(eid, str) or not eid:
            fail(f"{path}: a result row has no example_id")
        journal_path = p / f"{eid}.jsonl"
        if not journal_path.exists():
            fail(f"{path}: missing journal {journal_path.name}")
        if not isinstance(r.get("passed"), bool):
            fail(f"{path}: result {eid} 'passed' is not a bool")
        if not isinstance(r.get("provenance_pass"), bool):
            fail(f"{path}: result {eid} 'provenance_pass' is not a bool")
        if r.get("journal_sha256") != sha256_file(journal_path):
            fail(f"{path}: journal sha256 mismatch for {journal_path.name}")
        terminal_states.append(read_journal(journal_path))

    return summary, results, terminal_states


def process_arm(name, derived_dir, smoke_dir, arms):
    if name in arms:
        fail(f"duplicate arm name: {name}")
    if Path(derived_dir).resolve() == Path(smoke_dir).resolve():
        fail(f"arm {name}: the derived and smoke directories are the same; one run cannot be both suites")

    d_summary, d_results, d_terminals = verify_dir(derived_dir)
    s_summary, s_results, s_terminals = verify_dir(smoke_dir)

    for field in MATCHING_FIELDS:
        if d_summary.get(field) != s_summary.get(field):
            fail(f"arm {name}: {field} differs between derived and smoke")

    derived_pass = d_summary["n_pass"]
    derived_total = d_summary["n_tasks"]
    smoke_pass = s_summary["n_pass"]
    smoke_total = s_summary["n_tasks"]
    provenance_failures = sum(1 for r in d_results if not r["provenance_pass"])
    guard = "on" if d_summary.get("enforce_provenance") else "off"
    guard_refused = sum(1 for t in d_terminals if t == "guard_refused")
    all_terminals = d_terminals + s_terminals
    protocol_failures = sum(1 for t in all_terminals if t in UNANSWERED)
    tasks = derived_total + smoke_total

    arms[name] = {
        "derived_pass": derived_pass,
        "derived_total": derived_total,
        "smoke_pass": smoke_pass,
        "smoke_total": smoke_total,
        "provenance_failures": provenance_failures,
        "guard": guard,
        "guard_refused": guard_refused,
        "protocol_failures": protocol_failures,
        "tasks": tasks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", action="append", nargs=3,
                        metavar=("NAME", "DERIVED_DIR", "SMOKE_DIR"), required=True)
    args = parser.parse_args()

    arms = {}
    for name, d, s in args.arm:
        process_arm(name, d, s, arms)

    print(json.dumps({"arms": arms}, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
