"""Error ledger: every LLM error an attempt made, as metrics and as unrecoverable lost time (PC-REQ-4).

User rule 2026-10-24: "sessions MUST track LLM errors in both metrics and unrecoverable lost
time". Each attempt journal is read in order. Model turns cost their ``elapsed_seconds`` and
completion tokens; tool calls cost the harness-only ``tool_seconds`` where journaled
(``tool_probe``, from 2026-10-24; never shown to the model, FM-3). Errors are counted by class:

- ``schema``: the SDK refused the arguments (``isError``);
- ``unknown_id``: the tool refused an id the network lacks;
- ``tool``: any other ``execution_failure``;
- ``solver``: the trial did not converge;
- ``protocol``: the controller could not parse the model's call (``model_protocol_error``);
- an unanswered terminal (``incomplete_generation``, ``turn_budget_exhausted``,
  ``inference_timeout``, ``context_exhausted``).

An error is **recovered** when a later call with the same intent succeeds: the same tool, network,
operation and contingency. The time and tokens between them are ``recovery``, recoverable cost.
**Unrecoverable** lost time is everything after the attempt's first unrecovered error. For an
attempt that ended with no answer, it is the whole attempt, since nothing usable came out.
Whether an answer was right is the scorer's business, not the ledger's.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any, TypedDict

UNANSWERED: frozenset[str] = frozenset({"incomplete_generation", "turn_budget_exhausted", "inference_timeout",
                                        "context_exhausted", "invalid_tool_protocol", "invalid_tool_arguments"})
INTENT_KEYS: tuple[str, ...] = ("network_id", "operation", "contingency_line", "path")


class AttemptLedger(TypedDict):
    """One attempt's errors and their cost."""

    errors: dict[str, int]
    recovered: int
    unrecovered: int
    terminal: str
    total_s: float
    total_tokens: int
    recovery_s: float
    recovery_tokens: int
    unrecoverable_s: float
    unrecoverable_tokens: int
    tool_time_journaled: bool


def _classify(result: dict[str, Any]) -> str | None:
    """None for a usable response, else the error class."""
    if result.get("isError"):
        return "schema"
    try:
        response = json.loads(result["content"][0]["text"])
    except (ValueError, KeyError, IndexError, TypeError):
        return "tool"
    status = response.get("status")
    if status in {"success", "constraint_violation"}:
        return None
    if status == "non_convergence":
        return "solver"
    return "unknown_id" if "Unknown " in str(response.get("error") or "") else "tool"


def _intent(request: dict[str, Any]) -> tuple[Any, ...]:
    try:
        arguments = json.loads(request["function"]["arguments"] or "{}")
    except (ValueError, TypeError):
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return (request["function"]["name"], *(json.dumps(arguments.get(k)) for k in INTENT_KEYS))


def ledger(events: list[dict[str, Any]]) -> AttemptLedger:
    """The ledger of one attempt's journal events."""
    # A timeline of (seconds, tokens) per costed event, and the errors placed on it.
    costs: list[tuple[float, int]] = []
    requests: dict[str, dict[str, Any]] = {}
    calls: list[tuple[int, tuple[Any, ...], str | None]] = []  # (timeline index, intent, error class)
    errors: collections.Counter[str] = collections.Counter()
    terminal, journaled = "missing", False
    for event in events:
        kind, payload = event["kind"], event.get("payload") or {}
        if kind == "model_response":
            usage = payload.get("usage") or {}
            costs.append((float(payload.get("elapsed_seconds") or 0.0), int(usage.get("completion_tokens") or 0)))
        elif kind == "tool_request":
            requests[payload["id"]] = payload
        elif kind == "tool_response":
            journaled = journaled or "tool_seconds" in payload
            costs.append((float(payload.get("tool_seconds") or 0.0), 0))
            request = requests.get(payload["tool_call_id"])
            error = _classify(payload["result"])
            if error:
                errors[error] += 1
            if request is not None:
                calls.append((len(costs) - 1, _intent(request), error))
        elif kind == "model_protocol_error":
            errors["protocol"] += 1
            calls.append((len(costs) - 1, ("protocol",), "protocol"))
        elif kind == "terminal":
            terminal = payload.get("state", "missing")
    if terminal in UNANSWERED:
        errors[terminal] += 1

    def spent(start: int, end: int) -> tuple[float, int]:
        window = costs[start + 1:end + 1]
        return sum(s for s, _ in window), sum(t for _, t in window)

    recovered = unrecovered = 0
    recovery_s, recovery_tokens, first_unrecovered = 0.0, 0, None
    for i, (at, intent, error) in enumerate(calls):
        if error is None:
            continue
        fixed = next((later for later, later_intent, later_error in calls[i + 1:]
                      if later_error is None and later_intent == intent), None)
        if fixed is None:
            unrecovered += 1
            first_unrecovered = at if first_unrecovered is None else first_unrecovered
        else:
            recovered += 1
            seconds, tokens = spent(at, fixed)
            recovery_s, recovery_tokens = recovery_s + seconds, recovery_tokens + tokens
    total_s, total_tokens = sum(s for s, _ in costs), sum(t for _, t in costs)
    if terminal in UNANSWERED:
        lost_s, lost_tokens = total_s, total_tokens
    elif first_unrecovered is not None:
        lost_s, lost_tokens = spent(first_unrecovered, len(costs) - 1)
    else:
        lost_s, lost_tokens = 0.0, 0
    return AttemptLedger(errors=dict(errors), recovered=recovered, unrecovered=unrecovered, terminal=terminal,
                         total_s=total_s, total_tokens=total_tokens, recovery_s=recovery_s,
                         recovery_tokens=recovery_tokens, unrecoverable_s=lost_s, unrecoverable_tokens=lost_tokens,
                         tool_time_journaled=journaled)


def summarize(journals: list[Path]) -> dict[str, Any]:
    """One model's ledger over its attempts."""
    rows = [ledger([json.loads(line) for line in j.read_text().splitlines() if line.strip()]) for j in journals]
    errors: collections.Counter[str] = collections.Counter()
    for row in rows:
        errors.update(row["errors"])
    total = sum(r["total_s"] for r in rows)
    lost = sum(r["unrecoverable_s"] for r in rows)
    return {"attempts": len(rows), "attempts_with_errors": sum(bool(r["errors"]) for r in rows),
            "errors": dict(errors.most_common()), "recovered": sum(r["recovered"] for r in rows),
            "unrecovered": sum(r["unrecovered"] for r in rows),
            "total_s": round(total, 1), "recovery_s": round(sum(r["recovery_s"] for r in rows), 1),
            "unrecoverable_s": round(lost, 1), "unrecoverable_share": round(lost / total, 4) if total else None,
            "total_tokens": sum(r["total_tokens"] for r in rows),
            "unrecoverable_tokens": sum(r["unrecoverable_tokens"] for r in rows),
            "tool_time_journaled": all(r["tool_time_journaled"] for r in rows) if rows else False}


def main() -> int:
    """Print one ledger line per model directory under a run root (or one per directory given)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, help="Also write {model: summary} as JSON")
    args = parser.parse_args()
    models = sorted(p for p in [args.root, *args.root.iterdir()] if p.is_dir() and any(p.glob("ex_*.jsonl")))
    out = {}
    for model in models:
        out[model.name] = summarize(sorted(model.glob("ex_*.jsonl")))
        print(json.dumps({"model": model.name, **out[model.name]}))
    if args.out:
        args.out.write_text(json.dumps(out, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
