"""Regenerate verifier fixtures using actual PowerMCP executions, without a teacher.

Run: uv run python -m powercoder.distillation.refresh_replay_fixture
The named two-bus cases exercise the contract; they are not real NEM cases or
held-out model benchmark tasks. No numerical solver observations are invented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandapower as pp

from powercoder.inference import powermcp as pm

FIXTURES: Path = Path(__file__).parent / "fixtures"


def main() -> int:
    """Execute the declared success/failure cases and retain complete raw observations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=FIXTURES / "expected_mcp_replays.json")
    args = parser.parse_args()
    definitions = json.loads((FIXTURES / "adversarial_filter_cases.json").read_text())
    records = []
    for spec in definitions["replay_specs"]:
        case, budget = spec["case"], spec["max_iter"]
        inputs = {"case": case, "max_iter": budget, "check_limits": True}
        calls = [
            ("load_network", {"network_id": case}),
            ("run_power_flow", {
                "network_id": case, "method": "nr", "max_iter": budget, "check_limits": True,
            }),
        ]
        backend, trajectory = pm.PowerMCP(), []
        for tool, arguments in calls:
            result = backend.execute_tool(tool, arguments)
            trajectory.append({
                "tool": tool, "args": arguments, "status": result.status.value,
                "result": result.data if result.data else {"error": result.error},
                "evidence": result.evidence,
            })
        if trajectory[-1]["status"] != spec["status"]:
            raise ValueError(f"Unexpected real solver status for {spec['name']}: {trajectory[-1]['status']}")
        records.append({
            "name": spec["name"],
            "example": {"subfamily": "mcp-trajectory", "task": {"inputs": inputs}},
            "answer": {"trajectory": trajectory},
        })
    source = records[0]["answer"]["trajectory"][-1]["result"]
    report_fields = (
        "iterations", "min_bus_voltage_pu", "max_bus_voltage_pu",
        "max_line_loading_percent", "voltage_violations",
    )
    report = {"converged": True, **{key: source[key] for key in report_fields}}
    payload = {
        "provenance": {
            "source": "Actual PowerMCP fixture executions; verifier tests, not model benchmark data",
            "solver": "pandapower", "solver_version": pp.__version__,
            "source_sha256": {
                str(path.relative_to(pm.REPO_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (Path(__file__).resolve(), Path(pm.__file__).resolve())
            },
        },
        "replays": records,
        "report_example": {
            "subfamily": "pf-report",
            "task": {"inputs": {"case": records[0]["example"]["task"]["inputs"]["case"], "contingency_line": None}},
        },
        "report_answer": report,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    print(f"Saved {len(records)} real replay cases to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
