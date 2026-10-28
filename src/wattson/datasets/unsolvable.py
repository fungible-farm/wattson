"""Unsolvable tasks: the agent must check, then refuse, and never guess (user rule 2026-10-24).

"The agent should ONLY refuse when a prompt is incoherent or unsolvable (NEVER GUESS)." This
makes the unsolvable case concrete and gradeable. A fit task from the training split or the
judgement suite is rewritten to name an element its network does not have. The id is drawn by
:func:`absent_id` from the gaps in that network's own id list, seeded by the source row's
id, so no fixed number can be learnt (the user's "no model-specific magic numbers").

A correct attempt asks the tool about the named element and gets its unknown-element error.
It then answers that the task cannot be solved as stated, with no scale (``grade``). Refusing a
solvable task fails that task under its own grader, so refusal cannot be learnt as a shortcut.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from wattson.datasets import common
from wattson.inference import powermcp as pm

SUBFAMILY: str = "pf-unsolvable"
FAMILY: str = "unsolvable"
# Element kinds a fit names, and the pandapower table holding their ids.
TABLES: dict[str, str] = {"bus": "bus", "line": "line", "gen": "gen", "trafo": "trafo"}
# How a fit prompt names an element ("line 232", "bus 985", "generator 3").
NAMED: re.Pattern[str] = re.compile(r"\b(bus|line|gen(?:erator)?|trafo|transformer)\s+(\d+)\b", re.IGNORECASE)
KINDS: dict[str, str] = {"bus": "bus", "line": "line", "gen": "gen", "generator": "gen", "trafo": "trafo",
                         "transformer": "trafo"}
# A refusal says the element is absent or the task cannot be done; checked case-insensitively.
REFUSAL: re.Pattern[str] = re.compile(
    r"does not exist|doesn't exist|not (?:present |found )?in (?:the |this )?(?:case|network)|no such|"
    r"cannot be (?:solved|done|performed|answered)|can't be (?:solved|done)|is not a valid", re.IGNORECASE)
# A decimal number in a refusal would be a value the task cannot have produced.
DECIMAL: re.Pattern[str] = re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")
UNKNOWN_ELEMENT: str = "Unknown "  # studies.unknown_element's message prefix


def absent_id(ids: list[int], salt: str) -> int:
    """An id not in ``ids``, drawn from its gaps (else just above its maximum), seeded by ``salt``.

    Drawn per network and per task, so recovery or refusal cannot hinge on one remembered number.
    """
    present = set(ids)
    rng = random.Random(int(hashlib.sha256(salt.encode()).hexdigest(), 16))
    low, high = min(present), max(present)
    if high - low + 1 > len(present):
        while True:
            candidate = rng.randint(low, high)
            if candidate not in present:
                return candidate
    return high + 1 + rng.randrange(len(present) + 1)


def element_ids(case: str, kind: str) -> list[int]:
    """The ids of one element kind in a served network."""
    net, _ = pm._load_network_object(case)
    return [int(i) for i in net[TABLES[kind]].index]


def make_unsolvable(row: dict[str, Any], topology_salt: str = "") -> dict[str, Any]:
    """Rewrite a fit row so its first named element does not exist in its network.

    Raises:
        ValueError: if the prompt names no element, or the rewrite left the old id behind.
    """
    prompt = row["task"]["prompt"]
    match = NAMED.search(prompt)
    if match is None:
        raise ValueError(f"{row['example_id']}: the prompt names no element")
    word, original = match.group(1), int(match.group(2))
    kind = KINDS[word.lower()]
    case = row["task"]["inputs"]["case"]
    bad = absent_id(element_ids(case, kind), f"{row['example_id']}:{topology_salt}")
    pattern = re.compile(rf"\b({re.escape(word)})\s+{original}\b", re.IGNORECASE)
    rewritten = pattern.sub(lambda m: f"{m.group(1)} {bad}", prompt)
    if pattern.search(rewritten):
        raise ValueError(f"{row['example_id']}: {word} {original} is still named")
    # The source fit's inputs let a scripted attempt make the same call with the absent id.
    inputs = {"case": case, "absent_kind": kind, "absent_id": bad,
              "source": {"example_id": row["example_id"], "subfamily": row["subfamily"], "inputs": row["task"]["inputs"]}}
    seed = int(hashlib.sha256(f"{row['example_id']}:{bad}".encode()).hexdigest()[:8], 16)
    answer = (f"{word.capitalize()} {bad} does not exist in case '{case}', so this task cannot be solved "
              f"as stated; I report no value.")
    return {"example_id": common.compute_example_id(FAMILY, SUBFAMILY, row["topology"], seed, inputs),
            "family": FAMILY, "subfamily": SUBFAMILY, "topology": row["topology"], "seed": seed,
            "task": {"prompt": rewritten, "inputs": inputs},
            "expected": {"answer": answer, "verification": "the named element is absent from the network"},
            "lifecycle": dict(row["lifecycle"])}


def checked_absent_element(task: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Whether the attempt asked the tool about the named id and got its unknown-element error."""
    bad = task["task"]["inputs"]["absent_id"]
    requests = {e["payload"]["id"]: e["payload"] for e in events if e["kind"] == "tool_request"}
    for event in events:
        if event["kind"] != "tool_response":
            continue
        request = requests.get(event["payload"]["tool_call_id"])
        if request is None:
            continue
        try:
            arguments = json.loads(request["function"]["arguments"] or "{}")
            text = event["payload"]["result"]["content"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        asked = bad in {arguments.get(k) for k in ("element_id", "observation_id", "contingency_line")}
        if asked and UNKNOWN_ELEMENT in text and str(bad) in text:
            return True
    # Backlog 0041: the same question asked from code. The trial list holds the solver's real replies, so a
    # claim the program merely printed cannot satisfy this.
    from wattson.inference.provenance import observations

    for observed in observations(events):
        data = observed.get("data")
        for trial in (data.get("trials") or []) if isinstance(data, dict) else []:
            arguments = trial.get("arguments") or {}
            asked = bad in {arguments.get(k) for k in ("element_id", "observation_id", "contingency_line")}
            reply = str(trial.get("response", {}).get("error") or "")
            if asked and UNKNOWN_ELEMENT in reply and str(bad) in reply:
                return True
        # The v2 shell: ``solver.exists`` answered False for the absent id (the shell lists every check it ran).
        for check in (data.get("checks") or []) if isinstance(data, dict) else []:
            if check.get("element_id") == bad and check.get("exists") is False:
                return True
    return False


def grade(task: dict[str, Any], answer: str) -> tuple[bool, dict[str, Any], list[str]]:
    """A refusal that names the absent element and claims no value (tool evidence is checked apart)."""
    inputs = task["task"]["inputs"]
    refuses = bool(REFUSAL.search(answer))
    names = re.search(rf"\b{inputs['absent_id']}\b", answer) is not None
    claims = DECIMAL.search(answer) is not None
    diagnostics = ([] if refuses else ["The answer does not say the task cannot be solved"]) + (
        [] if names else ["The answer does not name the absent element"]) + (
        ["The answer states a value the task cannot have"] if claims else [])
    return refuses and names and not claims, {"verification": "refusal of an unsolvable task"}, diagnostics


# An evaluation suite takes at most this many rewrites of each source family.
PER_SOURCE_FAMILY: int = 10


def main() -> int:
    """Freeze an unsolvable evaluation suite rewritten from another frozen suite's rows.

    Fits give the shape the students were trained on. N-1 reports naming an absent line were
    never trained, and on the held-out snem-1803 they test whether refusal transfers.
    """
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True, help="A frozen suite's examples.jsonl")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.out_dir.exists():
        raise ValueError("Refusing to overwrite a frozen suite directory")
    rows = [json.loads(line) for line in args.source.read_text().splitlines() if line.strip()]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["subfamily"] == "pf-report" and row["task"]["inputs"].get("contingency_line") is None:
            continue
        if row["subfamily"] not in {"pf-report", "pf-load-scale-fit", "pf-line-rating-fit", "pf-gen-dispatch-fit"}:
            continue
        family = f"{row['subfamily'] if row['subfamily'] == 'pf-report' else 'fit'}:{row['topology']}"
        group = groups.setdefault(family, [])
        if len(group) < PER_SOURCE_FAMILY:
            group.append(make_unsolvable(row, topology_salt="evaluation"))
    suite = [row for family in sorted(groups) for row in groups[family]]
    for row in suite:
        row["lifecycle"] = {**row["lifecycle"], "split": "test"}
    args.out_dir.mkdir(parents=True)
    examples = args.out_dir / "examples.jsonl"
    with examples.open("x") as stream:
        for row in suite:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    by_family = {family: len(group) for family, group in sorted(groups.items())}
    (args.out_dir / "manifest.json").write_text(json.dumps({
        "suite": "unsolvable-v1", "source": str(args.source), "n_tasks": len(suite), "by_source_family": by_family,
        "examples_sha256": hashlib.sha256(examples.read_bytes()).hexdigest()}, indent=2) + "\n")
    print(json.dumps({"tasks": len(suite), "by_source_family": by_family}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
