#!/usr/bin/env python3
"""Runbook replay (#130 step c): fill a runbook's slots, run it, score it — with no model call.

Usage:
    python3 scripts/runbook_replay.py --runbook R.json --cases CASES.json --questions Q.json [--out EVIDENCE.json]

The replay is `run_spec` with a runbook as the spec, validated by `scripts/runbook_validate.py` first so a replay never executes
a file the schema would reject (any validator detail prints as `FAIL runbook-valid: <check>: <detail>` and nothing runs). CASES.json
is a JSON list of `{"question_id": ..., "role": "source"|"sibling", "params": {name: value}}` — the question the runbook was drafted
from plus the same-family questions it was not, which design section 3 needs two of before admission. Q.json is the question set
`scripts/gold_values.py` reads: `{"id": ..., "sql": ..., "decimals"?: n}`. For each case every `{slot}` of `sql_template` is filled
from `params`, and both that statement and the question's own gold `sql` go through `scripts/demo_timeseries_qa.run_sql` — the same
read-only, no-network container the harness uses for ground truth. The verdict is `nem_scoring.score(flat(gold rows), filled rows,
decimals)` — the strict scorer a model answer has to pass.

Substitution is textual, so a value must be `[A-Za-z0-9_.:- ]` and nothing else: a quote or a semicolon FAILS the case without
running it, because a runbook that needs quoting is a runbook that can be injected into. The keys of `params` must be exactly the
declared parameter names — a missing slot means the thing that ran was not the procedure, an extra one a different question.

Each case prints `PASS|FAIL replay <question_id> <role>: <reason>`, then `PASS|FAIL runbook-replay: <passed>/<n> cases,
siblings <ps>/<ns>`. Exit 0 clean, 1 for any FAIL (a runbook failing the schema is a FAIL before the cases), 2 for inputs that
cannot be read or parsed. `--out` writes `{"runbook_id", "runbook_sha256", "cases": [{question_id, role, passed, reason,
filled_sql}]}` with indent 2 and a trailing newline — the same bytes for the same inputs, the record step (d) admits or rejects on.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import demo_timeseries_qa as arm1  # noqa: E402
import nem_scoring  # noqa: E402
import runbook_validate  # noqa: E402

SAFE = re.compile(r"[A-Za-z0-9_.:\- ]+\Z")
ROLES = ("source", "sibling")


class InputError(Exception):
    """An input file cannot be read, is not JSON, or is not the list its name promises."""


class ReplayError(Exception):
    """The container errored, or answered with something that is not a list of rows."""


def load_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc


def load_list(path: str) -> list[Any]:
    data = load_json(path)
    if not isinstance(data, list):
        raise InputError(f"{path} is {type(data).__name__}, not a list")
    return data


def schema_problems(runbook: Any) -> list[str]:
    """Every validator detail as `check: detail`, in the validator's check order; empty when the runbook is valid."""
    return [f"{check}: {detail}" for check, details in runbook_validate.validate(runbook).items()
            for detail in (details or [])]


def unsafe(value: Any) -> str | None:
    """Why this value may not be substituted as text; None when it may. Numbers are stringified — the set allows no quote."""
    text = value if isinstance(value, str) else str(value) if isinstance(value, (int, float)) else None
    if text is None:
        return f"{value!r} is {type(value).__name__}, not a string or number"
    if "--" in text:
        return f"{value!r} contains '--', an SQL comment that could hide the rest of the statement"
    return None if SAFE.match(text) else f"{value!r} has characters outside [A-Za-z0-9_.:- ]"


def rows_of(sql: str) -> list[Any]:
    """The container's rows for one statement; ReplayError when it errors or returns no rows list."""
    result = arm1.run_sql(sql)
    if not isinstance(result, dict) or "error" in result:
        raise ReplayError(result["error"] if isinstance(result, dict) else f"not an object: {result!r}")
    if not isinstance(result.get("rows"), list):
        raise ReplayError(f"no rows in {str(result)[:160]}")
    return result["rows"]


def replay_case(case: Any, runbook: dict[str, Any], questions: dict[str, Any], declared: list[str]) -> dict[str, Any]:
    """Fill, run and score one case as {question_id, role, passed, reason, filled_sql}; filled_sql stays None if it never got filled."""
    out: dict[str, Any] = {"question_id": None, "role": None, "passed": False, "reason": "", "filled_sql": None}

    def fail(reason: str) -> dict[str, Any]:
        out["reason"] = reason
        return out

    if not isinstance(case, dict):
        return fail(f"case is {type(case).__name__}, not an object")
    out["question_id"], out["role"] = case.get("question_id"), case.get("role")
    if out["role"] not in ROLES:
        return fail(f"role {out['role']!r} is not one of {'/'.join(ROLES)}")
    question = questions.get(str(out["question_id"]))
    if question is None:
        return fail(f"no question {out['question_id']!r} in the questions file")
    params = case.get("params")
    if not isinstance(params, dict):
        return fail(f"params is {type(params).__name__}, not an object")
    if missing := [name for name in declared if name not in params]:
        return fail("missing parameter(s): " + ", ".join(missing))
    if extra := sorted(str(key) for key in params if key not in declared):
        return fail("undeclared parameter(s): " + ", ".join(extra))
    for name in declared:
        if why := unsafe(params[name]):
            return fail(f"unsafe parameter {name}: {why}")
    filled = runbook_validate.SLOT.sub(lambda m: str(params[m.group(1)]), runbook["sql_template"])
    out["filled_sql"] = filled
    try:
        gold = rows_of(question["sql"])
    except ReplayError as exc:
        return fail(f"gold sql: {exc}")
    try:
        got = rows_of(filled)
    except ReplayError as exc:
        return fail(f"filled sql: {exc}")
    out["passed"], out["reason"] = nem_scoring.score(arm1.flat(gold), got, question.get("decimals"))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runbook_replay.py",
        description="Replay a runbook over its source question and same-family siblings with no model call (#130).",
    )
    parser.add_argument("--runbook", required=True, metavar="FILE")
    parser.add_argument("--cases", required=True, metavar="FILE")
    parser.add_argument("--questions", required=True, metavar="FILE")
    parser.add_argument("--out", metavar="FILE")
    args = parser.parse_args(argv)

    try:
        runbook = load_json(args.runbook)
        cases, items = load_list(args.cases), load_list(args.questions)
    except InputError as exc:
        print(f"ERROR runbook-replay: {exc}", file=sys.stderr)
        return 2
    if problems := schema_problems(runbook):
        print("FAIL runbook-valid: " + "; ".join(problems))
        return 1

    questions = {str(item["id"]): item for item in items if isinstance(item, dict) and "id" in item}
    declared = [param["name"] for param in runbook["parameters"]]
    results = [replay_case(case, runbook, questions, declared) for case in cases]
    for res in results:
        print(f"{'PASS' if res['passed'] else 'FAIL'} replay {res['question_id']} {res['role']}: {res['reason']}")
    if args.out:
        evidence = {"runbook_id": runbook["id"], "runbook_sha256": hashlib.sha256(Path(args.runbook).read_bytes()).hexdigest(),
                    "cases": results}
        Path(args.out).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    passed, siblings = sum(res["passed"] for res in results), [res for res in results if res["role"] == "sibling"]
    sib_passed = sum(res["passed"] for res in siblings)
    print(f"{'PASS' if passed == len(results) else 'FAIL'} runbook-replay: {passed}/{len(results)} cases, "
          f"siblings {sib_passed}/{len(siblings)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
