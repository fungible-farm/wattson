#!/usr/bin/env python3
"""Admit or reject one runbook into the catalog (#130 step d) with no model call.

Usage: python3 scripts/runbook_admit.py --runbook R.json --cases CASES.json --questions Q.json --gold GOLD.json --catalog CATALOG.json
ACCEPT when the schema is valid, nothing leaks from GOLD, every source case replays, and >=2 siblings replay sharing no source question_id."""
from pathlib import Path

import argparse
import hashlib
import json
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import runbook_leakage_lint as lint  # noqa: E402
import runbook_replay as replay  # noqa: E402

class BadCatalog(ValueError):
    """The catalog cannot be read, or is not {accepted: [], rejected: []}."""


def leakage_problems(runbook_path: str, gold_items: list[dict]) -> list[str]:
    """The lint's three checks for one runbook against gold, as failure lines; prints nothing of its own."""
    runbooks, failures = lint.load_runbooks([runbook_path]), []
    for check in lint.CHECKS:
        found, _ = lint.lint_values(gold_items, runbooks) if check == "values" else lint.lint_text(check, gold_items, runbooks)
        failures.extend(found)
    return failures


def load_catalog(path: str) -> dict[str, list]:
    """The catalog as {accepted: [...], rejected: [...]}, empty when the file does not exist yet."""
    if not Path(path).exists():
        return {"accepted": [], "rejected": []}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BadCatalog(f"cannot read catalog {path}: {getattr(exc, 'strerror', None) or exc}") from exc
    if not isinstance(data, dict) or not all(isinstance(data.get(key), list) for key in ("accepted", "rejected")):
        raise BadCatalog(f"catalog {path} must be {{accepted: [], rejected: []}}")
    return {key: data[key] for key in ("accepted", "rejected")}


def record(path: str, entry: dict, accepted: bool) -> None:
    """Replace any entry with this id, add this one to its list, and write both lists sorted by id."""
    catalog, chosen = load_catalog(path), "accepted" if accepted else "rejected"
    for key in ("accepted", "rejected"):
        kept = [item for item in catalog[key] if not isinstance(item, dict) or item.get("id") != entry["id"]]
        catalog[key] = sorted(kept + ([entry] if key == chosen else []), key=lambda item: str(item.get("id", "")))
    Path(path).write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")


def rejection(results: list[dict]) -> str | None:
    """Why the replayed cases do not admit the runbook, from the first rule they break; None when they admit it."""
    sources = [res for res in results if res["role"] == "source"]
    siblings = [res for res in results if res["role"] == "sibling"]
    if not sources:
        return "replay source: no source case, so the runbook is not shown to reproduce the success it was drafted from"
    if bad := next((res for res in sources if not res["passed"]), None):
        return f"replay source {bad['question_id']}: {bad['reason']}"
    if len(siblings) < 2:
        return f"siblings: {len(siblings)} sibling case(s), need at least 2"
    if bad := next((res for res in siblings if not res["passed"]), None):
        return f"replay sibling {bad['question_id']}: {bad['reason']}"
    shared = {str(res["question_id"]) for res in siblings} & {str(res["question_id"]) for res in sources}
    return f"siblings: {', '.join(sorted(shared))} replay(s) as both source and sibling" if shared else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runbook_admit.py", description="Decide whether one runbook joins the admitted catalog (#130).")
    for flag in ("runbook", "cases", "questions", "gold", "catalog"):
        parser.add_argument(f"--{flag}", required=True, metavar="FILE")
    args = parser.parse_args(argv)
    try:
        runbook, cases = replay.load_json(args.runbook), replay.load_list(args.cases)
        items, gold_items = replay.load_list(args.questions), lint.load_gold(args.gold)
        load_catalog(args.catalog)  # a broken catalog fails the run before it decides anything
    except (replay.InputError, lint.InputError, BadCatalog) as exc:
        print(f"ERROR runbook-admit: {exc}", file=sys.stderr)
        return 2

    doc = runbook if isinstance(runbook, dict) else {}
    evidence: dict = {"validate": None, "leakage": None, "replay_passed": 0, "replay_total": 0, "siblings_passed": 0, "siblings_total": 0}
    reason = None
    if problems := replay.schema_problems(runbook):
        evidence["validate"], reason = False, "validate: " + "; ".join(problems)
    else:
        evidence["validate"] = True
        if leaks := leakage_problems(args.runbook, gold_items):
            evidence["leakage"], reason = False, "leakage: " + leaks[0]
        else:
            evidence["leakage"] = True
            questions = {str(item["id"]): item for item in items if isinstance(item, dict) and "id" in item}
            results = [replay.replay_case(case, runbook, questions, [param["name"] for param in runbook["parameters"]]) for case in cases]
            siblings = [res for res in results if res["role"] == "sibling"]
            evidence.update(replay_passed=sum(res["passed"] for res in results), replay_total=len(results),
                            siblings_passed=sum(res["passed"] for res in siblings), siblings_total=len(siblings))
            reason = rejection(results)
    entry = {"id": str(doc.get("id") or Path(args.runbook).stem), "family": doc.get("family"),
             "runbook_sha256": hashlib.sha256(Path(args.runbook).read_bytes()).hexdigest(),
             "decision_reason": "validator, leakage lint, source replay and siblings all pass" if reason is None else reason,
             "evidence": evidence}
    record(args.catalog, entry, reason is None)
    print(f"{'ACCEPTED' if reason is None else 'REJECTED'} {entry['id']}: {entry['decision_reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
