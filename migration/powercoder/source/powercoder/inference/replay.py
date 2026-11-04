"""Replay an attempt's tool calls and diff them against what the model was shown (PRD-0010 G1; backlog 0015).

G1 asks that a recorded attempt can be re-executed. Each ``tool_request`` in a journal is
re-run through ``mcp_server.respond``, the function the server uses to build the text it
returns. The parsed JSON is then compared with the journal's ``tool_response``. A response
the SDK refused (``isError``) is replayed by validating the arguments against the tool schema
the journal recorded. The tool set (study and namespace tools) is also read from that record.

Each call is classified:

- ``identical``: the re-derived response equals the recorded one.
- ``error_reproduced``: the schema still refuses the arguments.
- ``differs``: anything else. The first differing JSON paths are listed. A difference means the
  solver or tool code changed since the run, or the run was not deterministic (FM-3).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TypedDict

import jsonschema

from powercoder.inference.mcp_server import respond
from powercoder.inference.powermcp import PowerMCP

DIFF_PATHS_SHOWN: int = 5


class CallReplay(TypedDict):
    """One replayed call."""

    tool: str
    outcome: str  # identical | error_reproduced | differs
    diff_paths: list[str]


def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    """JSON paths where two parsed documents differ (exact equality, floats included)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [p for k in sorted(set(a) | set(b)) for p in _diff(a.get(k), b.get(k), f"{path}/{k}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in _diff(x, y, f"{path}/{i}")]
    return [] if a == b else [path or "/"]


def replay(journal: Path, backend: PowerMCP | None = None) -> list[CallReplay]:
    """Re-execute every tool call in one attempt journal."""
    events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
    schemas = {t["function"]["name"]: t["function"]["parameters"]
               for e in events if e["kind"] == "tools" for t in e["payload"]}
    study, namespace = "run_study_trial" in schemas, "ls" in schemas
    responses = {e["payload"]["tool_call_id"]: e["payload"]["result"] for e in events if e["kind"] == "tool_response"}
    backend = backend or PowerMCP()
    out: list[CallReplay] = []
    for e in events:
        if e["kind"] != "tool_request" or e["payload"]["id"] not in responses:
            continue
        name = e["payload"]["function"]["name"]
        recorded = responses[e["payload"]["id"]]
        try:
            arguments = json.loads(e["payload"]["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = None
        if recorded.get("isError"):
            try:
                if arguments is None or name not in schemas:
                    raise jsonschema.ValidationError("unparseable arguments or unknown tool")
                jsonschema.validate(arguments, schemas[name])
                out.append(CallReplay(tool=name, outcome="differs", diff_paths=["schema now accepts"]))
            except jsonschema.ValidationError:
                out.append(CallReplay(tool=name, outcome="error_reproduced", diff_paths=[]))
            continue
        shown = json.loads(recorded["content"][0]["text"])
        again = json.loads(respond(backend, name, arguments, study, namespace))
        paths = _diff(shown, again)
        out.append(CallReplay(tool=name, outcome="differs" if paths else "identical", diff_paths=paths[:DIFF_PATHS_SHOWN]))
    return out


def main() -> int:
    """Replay journals; print one summary line per journal and a total."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("journals", type=Path, nargs="+")
    args = parser.parse_args()
    backend, total = PowerMCP(), {"identical": 0, "error_reproduced": 0, "differs": 0}
    for journal in args.journals:
        calls = replay(journal, backend)
        counts = {k: sum(c["outcome"] == k for c in calls) for k in total}
        total = {k: total[k] + counts[k] for k in total}
        first = next((c for c in calls if c["outcome"] == "differs"), None)
        print(json.dumps({"journal": str(journal), **counts} | ({"first_diff": first} if first else {})))
    print(json.dumps({"total": total}))
    return 0 if total["differs"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
