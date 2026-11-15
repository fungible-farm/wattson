---
name: grid-study
description: Use when a question asks for a power-flow or network-study number (convergence, bus voltage, line loading, N-1 outage, load or dispatch scaling) on one of the served networks. The model picks the tool and its arguments from the allow-list; the solver computes every number.
---

# Grid study

The model plans; the solver computes. Never state a voltage, loading or iteration count that a tool did not return.

## The allow-list (and nothing else)

Tool definitions live in [src/wattson/inference/powermcp.py](../../src/wattson/inference/powermcp.py) (`load_network`, `run_power_flow`) and [src/wattson/inference/served_run_study_trial.json](../../src/wattson/inference/served_run_study_trial.json) (`run_study_trial`). The served network ids are the `network_id` enum in those definitions; use only an id the task or a tool response gave you.

| Need | Tool | Arguments that matter |
|---|---|---|
| Confirm a case exists and read its metadata | `load_network` | `network_id` |
| One AC power flow, converged or not | `run_power_flow` | `network_id`; optional `max_iter` (default 50), `check_limits` (default true) |
| Base case, N-1 outage, or a scaled load, line rating or generator | `run_study_trial` | `network_id`, `operation`, `max_iter`; see below |

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

## What this skill must not do

- Call a tool outside the allow-list, or invent a network id or element id.
- Fetch data or write files; the study tools read the served cases only.
- Report a number from memory or from a model of the grid.

## Logged steps to expect

One `tools/call` per tool use in the gateway journal (tool name, and arguments when the owner has opted in to `tool_arguments`). A study that needs a base case and an outage is two `run_study_trial` calls, in that order.
