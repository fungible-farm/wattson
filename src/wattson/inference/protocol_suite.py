"""G1's protocol suite: how many of a model's tool calls are valid IR commands (PRD-0010 §5; backlog 0015).

G1's exit criterion is "≥99% valid commands on the protocol suite"; §5 measures protocol
competence as valid calls, schema repairs and tool errors, prompted base against r6. This reads
a run root's attempt journals and lifts every ``run_study_trial`` call through the Rust IR
(``rust/command-ir``'s ``lift`` binary, so validity is the IR's own judgement, not a Python
restatement of it). Per model:

- ``valid_rate``: IR-valid ``run_study_trial`` calls / all of them (the exit criterion);
- ``repaired``: invalid calls whose next ``run_study_trial`` call is valid (a schema repair);
- ``tool_errors``: valid calls the tool still refused, e.g. an id the network lacks (data, not
  protocol);
- ``outside_ir``: calls to other tools (``run_power_flow``, ``load_network``, ``ls``/``get``),
  which the IR does not cover; counted, not scored;
- ``errors``: the most common refusal reasons.
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
from pathlib import Path
from typing import Any, TypedDict

RUST_DIR: Path = Path(__file__).resolve().parents[3] / "rust"
LIFT: Path = RUST_DIR / "target/release/lift"
EXIT_CRITERION: float = 0.99  # backlog 0015 exit criterion
ERRORS_SHOWN: int = 5


class ModelProtocol(TypedDict):
    """One model's protocol competence over a run root."""

    calls: int
    valid: int
    valid_rate: float | None
    meets_exit_criterion: bool
    invalid: int
    repaired: int
    tool_errors: int
    outside_ir: int
    errors: list[tuple[str, int]]


def lift(arguments: list[str]) -> list[dict[str, Any]]:
    """The IR's verdict on each raw argument string (built on first use)."""
    if not LIFT.exists():
        subprocess.run(["cargo", "build", "-q", "--release", "-p", "command-ir", "--bin", "lift"], cwd=RUST_DIR, check=True)
    flat = [a.replace("\n", " ") for a in arguments]
    out = subprocess.run([str(LIFT)], input="\n".join(flat) + "\n", capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines()]


def _calls(journal: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """``(tool, raw arguments, recorded result)`` for each answered call, in order."""
    events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
    results = {e["payload"]["tool_call_id"]: e["payload"]["result"] for e in events if e["kind"] == "tool_response"}
    return [(e["payload"]["function"]["name"], e["payload"]["function"]["arguments"] or "{}", results[e["payload"]["id"]])
            for e in events if e["kind"] == "tool_request" and e["payload"]["id"] in results]


def _tool_refused(result: dict[str, Any]) -> bool:
    if result.get("isError"):
        return True
    try:
        return json.loads(result["content"][0]["text"]).get("status") in {"execution_failure", "error", "policy_denied"}
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
        return True


def measure(journals: list[Path]) -> ModelProtocol:
    """Protocol competence over one model's journals."""
    per_attempt = [_calls(j) for j in journals]
    studies = [[c for c in calls if c[0] == "run_study_trial"] for calls in per_attempt]
    verdicts = iter(lift([raw for calls in studies for _, raw, _ in calls]))
    valid = invalid = repaired = tool_errors = 0
    errors: collections.Counter[str] = collections.Counter()
    for calls in studies:
        judged = [(next(verdicts), result) for _, _, result in calls]
        for i, (verdict, result) in enumerate(judged):
            if verdict["valid"]:
                valid += 1
                tool_errors += _tool_refused(result)
            else:
                invalid += 1
                errors[verdict["error"]] += 1
                repaired += i + 1 < len(judged) and judged[i + 1][0]["valid"]
    total = valid + invalid
    rate = valid / total if total else None
    return ModelProtocol(calls=total, valid=valid, valid_rate=rate, meets_exit_criterion=rate is not None and rate >= EXIT_CRITERION,
                         invalid=invalid, repaired=repaired, tool_errors=tool_errors,
                         outside_ir=sum(len(c) for c in per_attempt) - total, errors=errors.most_common(ERRORS_SHOWN))


def main() -> int:
    """Print one line per model directory under a run root."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    for model in sorted(p for p in args.root.iterdir() if p.is_dir() and any(p.glob("ex_*.jsonl"))):
        print(json.dumps({"model": model.name, **measure(sorted(model.glob("ex_*.jsonl")))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
