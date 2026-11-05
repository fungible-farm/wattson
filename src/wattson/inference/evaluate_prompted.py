"""Evaluate a model with the scripted procedure written into its system prompt (gate G0).

Backlog 0008 / PRD-0010 G0, baseline (b): if a base model told the procedure in
its prompt does as well as the fine-tuned student, fine-tuning added nothing
measurable. This runs evaluate_agent unchanged except for the system turn,
which states exactly the procedure the students' training trajectories follow
(powercoder/datasets/trajectories.py): the same tools, budgets, grader and
journals, so the only difference is what the prompt tells the model.

Why a wrapper instead of a flag on evaluate_agent: written while the r6b
evaluation was running, whose evaluator subprocesses import evaluate_agent and
tool_probe from the working tree, so those files were not to be edited
mid-run. tool_probe.probe reads SYSTEM_PROMPT from its module globals on each
call, so replacing it before evaluate() runs changes every attempt's system
turn, and each journal's ``start`` event records the prompt actually sent.
After the run, manifest.json and summary.json gain
``"system_prompt_variant": "procedure"`` (compare_agents.CONDITIONS), so a
prompted run is never compared with an unprompted one by accident.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from wattson.inference import evaluate_agent, tool_probe

VARIANT: str = "procedure"
# The evaluation's own system turn first (tool_probe.SYSTEM_PROMPT), then the
# procedure the training trajectories follow, stated as instructions. Kept to
# what the scripted policy actually does; the report wording is the one the
# strict scorer reads.
PROCEDURE_SYSTEM_PROMPT: str = tool_probe.SYSTEM_PROMPT + """

Procedure:
- Reports (run an AC power flow and report the result): make ONE run_study_trial call with operation "none",
  scale 1, element_id 0, observation "bus_voltage", observation_id any existing bus, and contingency_line only if
  the task names a line out of service (omit it otherwise; never write 0 or "None" for "no contingency").
  Then answer exactly: "Power flow on '<case>' with line <n> out of service (N-1 contingency) converged in <k>
  iterations. Min bus voltage = <v> pu, max bus voltage = <v> pu, max line loading = <l>%, voltage violations =
  <n>." (use " (base case)" instead of the contingency phrase when there is none), with voltages to 4 decimals and
  loading to 2, copied from the tool response.
- Fits (find the scale factor s in [lo, hi]): call run_study_trial at s = lo and at s = hi, then bisect: try the
  midpoint rounded to 6 decimals, keep the half whose ends bracket the target, and repeat until the measured metric
  is within the tolerance of the target. A status of constraint_violation with metric_valid true is still a valid
  measurement. Answer with only the last tried scale, to 6 decimals (e.g. 1.037500).
- Never report a number that did not come from a tool response."""


def mark(out: Path) -> None:
    """Record the variant in the run's manifest and summary (see module docstring)."""
    for name in ("manifest.json", "summary.json"):
        path = out / name
        if path.is_file():
            record = json.loads(path.read_text())
            record["system_prompt_variant"] = VARIANT
            path.write_text(json.dumps(record, indent=2) + "\n")


def main() -> None:
    """Run evaluate_agent's CLI with the procedure prompt, then mark the run."""
    tool_probe.SYSTEM_PROMPT = PROCEDURE_SYSTEM_PROMPT
    out = Path(sys.argv[sys.argv.index("--out") + 1])
    try:
        evaluate_agent.main()
    finally:
        mark(out)


if __name__ == "__main__":
    main()
