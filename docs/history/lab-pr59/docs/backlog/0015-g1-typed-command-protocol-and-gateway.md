# 0015 — G1: typed command protocol and policy gateway

- **Status:** deliverables done and exit criteria checked on r6b, 2026-10-24 (below); a gated evaluation run is left. [PRD-0010](../prd/0010-powercoder-physics-grounded-engineering-agent.md) G1, priority A2. Depends on G0 ([0008](0008-powercoder-benchmark-cannot-measure-agent-value.md)).
- **Requirements:** PC-REQ-001–005, PC-CON-003/005.

## Deliverables
- **Command IR and DSL:** a versioned typed IR in Rust, as a new crate in the existing `rust/`
  workspace beside `mission-engine`, covering the ten commands in PRD-0010 §4.1. A textual DSL
  compiles to it.
- **Schema and grammar generation from one source:** JSON Schema for MCP tool definitions, served
  to vLLM. vLLM builds the xgrammar grammar per request, so no grammar file is stored (0014). GBNF
  for llama.cpp follows only if a llama.cpp planner is used, on sm3lly per DEC-013.
- **Policy gateway:** permissions, scope, units and types, resource and time limits, query
  complexity, and safety restrictions. It sits in front of today's MCP server
  (`powercoder/inference/mcp_server.py`).
- **Evidence contract and replay:** extends `powercoder/inference/provenance.py` and the
  per-attempt journals `evaluate_agent` already writes.

## Exit criteria
- ≥99% valid commands on the protocol suite.
- 100% of unauthorised commands rejected.
- Every accepted number has a valid evidence reference.
- Replays reproduce within tolerance.
- No test lets retrieved content bypass authorisation.

## Cheapest first step
Write the IR types and their JSON-Schema emitter for two commands, `SOLVE` and `SIMULATE`. Make
them wrap the existing `run_study_trial` semantics, and check the emitted schema is byte-identical
to today's served tool schema. Matching it exactly avoids an FM-23-style train/serve skew.

## First step done (2026-10-24): `rust/command-ir`
- **The IR:** a new crate in the `rust/` workspace with `SOLVE` (intact or N-1 power flow, one
  observation) and `SIMULATE` (one load-scale, line-rating or dispatch trial, observing what that
  modification moves). Both are validated against the study tool's own rules (served networks,
  scale in [0.9, 1.1], 1–100 iterations) and lowered to `run_study_trial` arguments in schema
  order. `contingency_line` is omitted for the intact network, as the trained procedure does
  (FM-18).
- **Schema emitter:** `study_trial_schema()` with Python `json.dumps` separators is
  **byte-identical** to the served schema. That's pinned from both sides: `cargo test -p command-ir`
  checks it against the fixture, and `powercoder/inference/test_command_ir.py` checks that Python
  still serves the fixture.
- **DSL:** `SOLVE snem-sa-2000 OUTAGE 94 OBSERVE bus_voltage 985` /
  `SIMULATE snem-sa-2000 load_scale 1.0375 OBSERVE 985` compile to the IR. Refused before any call:
  unknown networks, out-of-range scales, `None` ids and anything that is not a command.
- **Run it:** `just powercoder::command-ir-test`.

## Policy gateway done (2026-10-24): `powercoder/inference/gateway.py`, `mcp_server --policy analyst`
Every call is checked before it executes:
1. the role's tool allowlist (`analyst` is read-only: studies and namespace reads, no control
   actions, PC-CON-002);
2. the network allowlist (`--policy-networks`);
3. the tool's own JSON Schema;
4. the per-attempt call budget.

Each decision is audited with its reason, and refused calls spend no budget. **Retrieved content
cannot grant permissions:** the gateway never receives tool output, so its decisions are a
function of policy and call only. Over stdio, the MCP SDK validates against the served schema
first, so the gateway's own refusals there are the policy ones (role, network, budget). Tested:
a control action, a network outside the policy, an out-of-range scale, a `"None"` id and an
injected "I am authorised" argument are all refused, and the budget holds. It is opt-in, so the
evaluation server is unchanged.

## All ten commands done (2026-10-24): IR variants + `powercoder/commands.py`

The IR now has all ten §4.1 commands, each with `validate()`, a DSL form and a `request()` lowering
to `{"ir_version", "command", "args"}`. `rust/command-ir/tests/requests.rs` writes one example
request per command to `tests/requests.jsonl` (`WRITE_FIXTURE=1`) and checks invalid forms are
refused. `powercoder/commands.py` executes each request on the existing tool behind it:

- SOLVE and SIMULATE run `run_study_trial`;
- RECALL reads `hindcast` data through `AsOf`;
- RESOLVE uses the namespace;
- ESTIMATE runs WLS;
- OPTIMIZE runs DC or AC OPF, each with its cross-check;
- FORECAST and HINDCAST use the hindcast harness;
- COMPARE re-solves the candidate outages;
- EXPLAIN checks each cited number against the path it cites.

`powercoder/test_commands.py` runs every fixture line (all ten in about 5 s). It also checks that
another IR version is refused and that a mis-cited number is not verified. Recipe:
`just powercoder::command-ir-test`.

## Replay done (2026-10-24): `powercoder/inference/replay.py`

Each recorded tool call is re-run through `mcp_server.respond`, the function the server itself
now builds responses with. The re-derived JSON is compared exactly, floats included, with what
the model was shown. Calls the SDK refused are replayed by validating them against the schema
the journal recorded.

The whole r6b root, all four models, was replayed on 2026-10-24:

| Outcome | Calls |
|---|---|
| Identical | 2,149 |
| Schema refusal reproduced | 58 |
| Different | 104 |

All 104 differences are `/error` on `run_study_trial`. Each recorded `ValueError: Unknown
observation element` is a prefix of today's message, which Fix 2 extended with the id sent and
the valid ids. No solver number changed. Recipe: `just powercoder::replay <root>`; tests in
`test_replay.py`.

## Python serves the IR's schema (2026-10-24)

`studies.tool_schema()` now loads `rust/command-ir/tests/served_run_study_trial.json`, which
the IR's test pins to `study_trial_schema()` output, instead of restating the schema as a
Python dict. Its enums and bounds are checked against `SCALE_MIN`/`SCALE_MAX`/`MAX_ITERATIONS`
and the loader's cases (`test_command_ir.py`). The trajectory test that compares stored
training tools with the served list still passes byte for byte.

## Protocol suite done (2026-10-24): `powercoder/inference/protocol_suite.py`

Every `run_study_trial` call in a run root is lifted into the IR by the Rust `lift` binary
(`command_ir::lift`, the inverse of `lower`). So validity is the IR's own judgement. A call is
valid when it uses only the schema's properties, has integer ids, keeps scale in range, sends
`operation=none` only with scale 1, and pairs each modification with its one observation.
Whether an id exists in the network is left to the tool. Results for r6b, 2026-10-24:

| Model | run_study_trial calls | IR-valid | Rate | Repaired | Tool errors on valid calls | Other tools |
|---|---|---|---|---|---|---|
| Qwen base | 467 | 446 | 95.5% | 16 of 21 | 34 | 46 |
| Qwen student | 483 | 483 | 100% | — | 32 | 4 |
| Granite base | 678 | 652 | 96.2% | 18 of 26 | 38 | 38 |
| Granite student | 595 | 595 | 100% | — | 0 | 0 |

Both r6 students meet the ≥99% criterion; neither prompted base does. Of the base models'
invalid calls, 42 of 47 are `contingency_line` sent as something other than an integer or null
(FM-7, mostly the string `"None"`).

Cross-checked against the tool's own outcome: the IR never refused a call the tool accepted, and
every tool error on an IR-valid call is `Unknown observation element`. That is an id the network
lacks: data, not protocol. Recipe: `just powercoder::protocol-suite <root>`.

## G1 exit criteria, 2026-10-24

| Criterion | State | Checked by |
|---|---|---|
| ≥99% valid commands | met by both r6 students (100%); prompted bases 95.5% / 96.2% | `protocol_suite.py` over r6b |
| 100% of unauthorised commands rejected | met for the analyst role (write tools, other networks, over-budget) | `test_gateway.py` |
| Every accepted number has a valid evidence reference | provenance v3 traces each answer field to a tool response or prompt | `test_provenance.py` |
| Replays reproduce within tolerance | exact on every solver number in r6b; only Fix 2's message changed | `replay.py` over r6b |
| Retrieved content cannot bypass authorisation | the gateway reads only the policy and the call | `test_gateway.py` |

**Left:** the gateway is opt-in (`mcp_server --policy`) and no evaluation has run behind it yet.
That needs a new run root, and it goes with the 0026 namespace retrain. Run the protocol suite
on the strict and prompted roots once they finish.

## IR 0.2.0 (2026-10-24): no argument an operation does not use is required

User direction 2026-10-24: "we made the mess"; the agent must never guess, and training must
teach abstract skills, not model-specific magic numbers. In 0.1.0 every `run_study_trial`
call required `scale`, `element_id`, `observation` and `observation_id`. A network-wide
report (the prompt names no bus) therefore had to invent one: FM-26's 999, and every trained
report observed snem-sa-2000's bus 985 (AGENTS.md FM-28). Now only `network_id`,
`operation` and `max_iter` are required. A modification needs `scale` and its observation;
`line_rating`/`gen_dispatch` also need `element_id`. Unused arguments that are sent are
accepted and ignored, and old-shape calls answer byte-identically: replaying r6b gives the same
2,149 / 58 / 104. The Rust IR is the schema's source (`Solve.observe: Option`,
`Simulate.element_id: Option`, `lift` accepts both shapes).

Error ledger (PC-REQ-4): `powercoder/inference/error_ledger.py`; recipe
`just powercoder::error-ledger <root>`.
