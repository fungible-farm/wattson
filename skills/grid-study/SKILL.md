---
name: grid-study
description: Use when a question asks for a power-flow or network-study number (convergence, bus voltage, line loading, N-1 outage, load or dispatch scaling) on one of the served networks. The model picks the tool and its arguments from the allow-list; the solver computes every number.
---

# Grid study

The model plans; the solver computes. Never state a voltage, loading or iteration count that a tool did not return.

## The allow-list (and nothing else)

Four tools. Definitions live in [src/wattson/inference/powermcp.py](../../src/wattson/inference/powermcp.py) (`load_network`, `run_power_flow`), [src/wattson/inference/served_run_study_trial.json](../../src/wattson/inference/served_run_study_trial.json) (`run_study_trial`) and [benchmarks/tool-selection/tools.json](../../benchmarks/tool-selection/tools.json) (all four, including `run_python`). The served network ids are the `network_id` enum in those definitions; use only an id the task or a tool response gave you.

| Need | Tool | Arguments that matter |
|---|---|---|
| Confirm a case exists and read its metadata | `load_network` | `network_id` |
| One AC power flow, converged or not | `run_power_flow` | `network_id`; optional `max_iter` (default 50), `check_limits` (default true) |
| Base case, N-1 outage, or a scaled load, line rating or generator | `run_study_trial` | `network_id`, `operation`, `max_iter`; see below |
| Arithmetic, comparisons, or a search that needs many trials (bisection, a sweep) | `run_python` | imports only `math, json, statistics, fractions, decimal, solver`; `solver.trial(...)` takes the same arguments as `run_study_trial` and each call is a real study |

## Choosing `run_study_trial` arguments

- Base case or N-1 report: `operation=none`; add `contingency_line` (an integer; `0` is a real line) only for an outage. Omit it, or pass null, for the intact network.
- Load scaling: `operation=load_scale`, `scale` in 0.9 to 1.1, `observation=bus_voltage`, plus `observation_id`.
- Line rating: `operation=line_rating`, `element_id` (the line), `observation=line_loading`, plus `observation_id`.
- Generator dispatch: `operation=gen_dispatch`, `element_id` (the generator), `observation=trafo_loading`, plus `observation_id`.
- `load_level` (0.9 to 1.1) sets the operating point; do not combine it with `load_scale`.
- A refused argument is an error to report, not to retry with a guessed value.

## Reading the answer

- `status=constraint_violation` with `metric_valid=true` is still a usable measurement. Compare `voltage_violations` with `base_case_voltage_violations`; the unmodified case already has some.
- `non_convergence` and `execution_failure` are results. Report them with the tool's own message.
- Quote the number from the tool reply with its unit; do the arithmetic in `run_python`, not in your head.

## Reporting a fitted value

A search over `solver.trial` ends with a value the solver actually tried. Report that value at full precision (print it with `repr`, not a rounded format). If you round it, run one more trial at the rounded value and quote that trial's result, because the grader looks for a converged trial at exactly the scale you report. Measured: neo-coder reported `0.909253` (6 decimals) while every trial it ran was at `0.90925346…`, so `tool_evidence` failed although the answer was right to 0.000000 pu.

## What this skill must not do

- Call a tool outside the allow-list, or invent a network id or element id.
- Fetch data or write files; the study tools read the served cases only.
- Report a number from memory or from a model of the grid.

## Logged steps to expect

Each tool use is one call in the harness journal (`tool_request`, `tool_response`); tools served over the gateway's MCP route also give one `tools/call` record in the gateway journal (tool name, and arguments when the owner has opted in to `tool_arguments`). A study that needs a base case and an outage is two `run_study_trial` calls, in that order. A search through `run_python` is one call that contains many solver trials, listed in the response's `trials`.
