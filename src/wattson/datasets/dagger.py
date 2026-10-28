"""Oracle relabelling of a student's own failures (DAgger) for the Python-shell students (backlog 0041).

The fit procedure, the planning comparison and the report are programs the oracle already writes correctly, so the right
next step is known at *any* state a student reaches, including the bad ones. A student that wrote a failing program at
its first turn leaves a state (prompt, its program, the solver's refusal) that supervised fine-tuning never showed it.
This turns each such failure into a training trajectory: the student's own first turn and the reply it got are kept as
context only, and the oracle's correct program, its reply and the answer are the supervised turns.

Scope, stated: only attempts whose FIRST program errored are relabelled (the dominant failure of the first Python
student: a call without ``scale``). An attempt whose program ran but whose answer was wrong, or whose failure is a
silent substitution (an absent-line report answered on the intact network), is counted and skipped, not guessed at.

``collect`` reads journals the harness wrote (``evaluate_agent`` or ``tool_probe``), regrades each with the real
grader, and writes ``trajectories.jsonl`` plus a manifest, in the format ``wattson.training.agent_sft`` trains on, with
``fault: "student_error"`` so the first turn is context only (the mechanism the injected recoveries use).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from wattson.datasets import trajectories
from wattson.datasets.audit import load_rows
from wattson.inference.evaluate_agent import grade

FAULT: str = "student_error"


def messages_of(journal: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The conversation the harness held, as chat messages (reasoning dropped, as the harness does), and its raw events."""
    events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
    messages = list(next(e for e in events if e["kind"] == "start")["payload"]["messages"])
    for event in events:
        if event["kind"] == "model_response":
            message = event["payload"]["choices"][0]["message"]
            messages.append({k: message[k] for k in ("role", "content", "tool_calls") if k in message})
        elif event["kind"] == "tool_response":
            messages.append({"role": "tool", "tool_call_id": event["payload"]["tool_call_id"],
                             "content": json.dumps(event["payload"]["result"], allow_nan=False)})
    return messages, events


def first_program_errored(messages: list[dict[str, Any]]) -> bool:
    """Did the student's first turn call ``run_python`` and get an error back?"""
    calls = [m for m in messages if m.get("tool_calls")]
    replies = [m for m in messages if m.get("role") == "tool"]
    if not calls or not replies or calls[0]["tool_calls"][0]["function"]["name"] != "run_python":
        return False
    try:
        text = json.loads(replies[0]["content"])["content"][0]["text"]
        return json.loads(text).get("status") in {"error", "timeout"}
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def splice(student: list[dict[str, Any]], oracle: dict[str, Any]) -> list[dict[str, Any]]:
    """The student's first turn and the solver's reply to it, then the oracle's turns, with the oracle's call ids renumbered."""
    prefix = student[:2 + 2]  # system, user, the student's program turn, its reply
    renamed: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for message in oracle["messages"][2:]:
        message = dict(message)
        if message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                renamed[call["id"]] = f"call_{len(renamed) + 1}"
                calls.append({**call, "id": renamed[call["id"]]})
            message["tool_calls"] = calls
        if message["role"] == "tool":
            message["tool_call_id"] = renamed[message["tool_call_id"]]
        out.append(message)
    return prefix + out


async def relabel(row: dict[str, Any], student: list[dict[str, Any]], work: Path, thinking: bool, v2: bool) -> dict[str, Any] | None:
    """The spliced trajectory for one failed attempt, or None if the oracle cannot build this row."""
    work.mkdir(parents=True, exist_ok=True)
    oracle, reason = await trajectories.build(row, work, None, False, True, thinking, v2)
    if oracle is None:
        return None
    return {"example_id": row["example_id"], "subfamily": row["subfamily"], "fault": FAULT, "tools": oracle["tools"],
            "messages": splice(student, oracle)}


def collect(arguments: argparse.Namespace) -> dict[str, Any]:
    """Relabel every first-program failure among the journals in ``arguments.journals``; write the trajectories."""
    tasks = {row["example_id"]: row for row in load_rows(arguments.tasks)}
    out_dir: Path = arguments.out_dir
    if out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen trajectory directory")
    (out_dir / "oracle-journals").mkdir(parents=True)
    kept: list[dict[str, Any]] = []
    counts = {"attempts": 0, "passed": 0, "failed": 0, "relabelled": 0, "skipped_not_first_program_error": 0, "oracle_failed": 0}
    for journal in sorted(arguments.journals.glob("ex_*.jsonl")):
        row = tasks.get(journal.stem)
        if row is None:
            continue
        counts["attempts"] += 1
        if grade(row, journal)["passed"]:
            counts["passed"] += 1
            continue
        counts["failed"] += 1
        messages, _events = messages_of(journal)
        if not first_program_errored(messages):
            counts["skipped_not_first_program_error"] += 1
            continue
        trajectory = asyncio.run(relabel(row, messages, out_dir / "oracle-journals" / journal.stem, arguments.thinking, arguments.report_fields))
        if trajectory is None:
            counts["oracle_failed"] += 1
            continue
        kept.append(trajectory)
        counts["relabelled"] += 1
    path = trajectories.write_in_order(out_dir / "trajectories.jsonl", kept)
    manifest = {"dataset": "nem-wattson-dagger", "student_journals": str(arguments.journals), "tasks": str(arguments.tasks),
                "counts": counts, "kept": len(kept), "thinking": arguments.thinking, "python_report_fields": arguments.report_fields,
                "trajectories_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--journals", type=Path, required=True, help="A result directory of student attempts (ex_*.jsonl)")
    parser.add_argument("--tasks", type=Path, required=True, help="The tasks those attempts answered (examples.jsonl)")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--thinking", action="store_true", help="Scripted reasoning on the oracle's turns")
    parser.add_argument("--report-fields", action="store_true", help="The v2 shell, as the student was served")
    collect(parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
