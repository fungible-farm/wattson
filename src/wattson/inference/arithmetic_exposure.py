"""How many numbers did the model compute itself? (backlog 0041)

The goal is that a model never does arithmetic: every calculation is written as code and run by the shell. A
number the model writes down (in a tool argument, in code, in its reasoning or its answer) is **traced** if it is
in the task's prompt, in the tool schema, or in something a tool returned or a program printed earlier in the attempt, and **untraced**
otherwise. An untraced number is one the model produced without a tool or the prompt giving it: a midpoint it
worked out in its head and sent as ``scale=1.0375`` is the case this exists to count.

Named stand-ins and limits (this is a heuristic, not a proof):

* Structural constants that code needs are not counted: the integers in ``STRUCTURAL``, and any integer that is a
  count or an index under ``STRUCTURAL_INTEGER_MAX``. A model could hide a calculated integer among them, so the
  measure under-counts integer arithmetic; it is aimed at the decimals a bisection produces.
* Numbers inside code that are the result of a *program's* calculation are never written, so they are not seen:
  that is the point of the shell.
* A number the model copies correctly from the prompt or a reply is traced even if it was not needed.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from wattson.inference.provenance import matches

# A number as written: an optional sign, digits, an optional fraction or exponent, not part of a name or a longer number.
NUMBER: re.Pattern[str] = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w])")
# Integers every program needs (a 0 or 1 start, a count, a step, a decimals count), never a computed answer.
STRUCTURAL_INTEGER_MAX: int = 100
STRUCTURAL: frozenset[str] = frozenset({"0.0", "1.0", "0.5", "2.0", "0.0", "1e-6", "1e-9"})


def numbers_in(text: str) -> list[str]:
    """Every number written in ``text``, as written."""
    return NUMBER.findall(text)


def structural(printed: str) -> bool:
    """Is ``printed`` an integer a program needs anyway, or one of a few fixed constants?"""
    if printed in STRUCTURAL:
        return True
    try:
        value = float(printed)
    except ValueError:
        return False
    return "." not in printed and "e" not in printed.lower() and abs(value) <= STRUCTURAL_INTEGER_MAX


def traced(printed: str, sources: list[float]) -> bool:
    """Does a tool reply, a printed result or the prompt hold this number (to the digits written)?"""
    return any(matches(printed, source) for source in sources)


def written_by_the_model(message: dict[str, Any]) -> dict[str, list[str]]:
    """The model-authored text of one assistant message, by place: arguments, code, reasoning, answer."""
    out: dict[str, list[str]] = {"arguments": [], "code": [], "reasoning": [], "answer": []}
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if reasoning:
        out["reasoning"] += numbers_in(str(reasoning))
    for call in message.get("tool_calls") or []:
        arguments = json.loads(call["function"].get("arguments") or "{}")
        for key, value in arguments.items():
            if key == "code":
                out["code"] += numbers_in(str(value))
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                out["arguments"].append(repr(value))
    if not message.get("tool_calls") and message.get("content"):
        out["answer"] += numbers_in(str(message["content"]))
    return out


def exposure(journal: Path) -> dict[str, Any]:
    """Count the traced and untraced numbers one attempt's model wrote, by place."""
    events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
    sources: list[float] = []
    prompt = next(e["payload"]["messages"][1]["content"] for e in events if e["kind"] == "start")
    sources += [float(n) for n in numbers_in(prompt)]
    # The tool schema is a source too: it states the limits a call must respect (the scale bounds 0.9 and 1.1).
    for event in events:
        if event["kind"] == "tools":
            sources += [float(n) for n in numbers_in(json.dumps(event["payload"]))]
    untraced: dict[str, list[str]] = {"arguments": [], "code": [], "reasoning": [], "answer": []}
    seen = 0
    for event in events:
        if event["kind"] == "tool_response":
            for content in event["payload"]["result"].get("content", []):
                sources += [float(n) for n in numbers_in(content.get("text", ""))]
        elif event["kind"] == "model_response":
            message = event["payload"]["choices"][0]["message"]
            for place, printed in written_by_the_model(message).items():
                for number in printed:
                    seen += 1
                    if structural(number) or traced(number, sources):
                        continue
                    untraced[place].append(number)
    total = sum(len(v) for v in untraced.values())
    return {"numbers_written": seen, "untraced": total, "by_place": {k: len(v) for k, v in untraced.items()},
            "examples": {k: v[:5] for k, v in untraced.items() if v}}


def summarise(directory: Path) -> dict[str, Any]:
    """Per-attempt exposure over one result directory (one journal per task)."""
    journals = sorted(p for p in directory.glob("ex_*.jsonl"))
    rows = [exposure(journal) for journal in journals]
    places = {k: sum(r["by_place"][k] for r in rows) for k in ("arguments", "code", "reasoning", "answer")}
    return {"attempts": len(rows), "attempts_with_untraced": sum(r["untraced"] > 0 for r in rows),
            "untraced_total": sum(r["untraced"] for r in rows), "by_place": places,
            "numbers_written": sum(r["numbers_written"] for r in rows)}


def main() -> None:
    """Print the exposure of each result directory given."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directories", nargs="+", type=Path)
    for directory in parser.parse_args().directories:
        print(directory.name, json.dumps(summarise(directory)))


if __name__ == "__main__":
    main()
