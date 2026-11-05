# AGENTS.md

Guidance for AI coding agents (and anyone else) working in this repo. Read `docs/VISION.md` and
`docs/DEFINITION_OF_DONE.md` first for the full plan and the checklist this repo is built against;
this file is the short, practical version for making changes.

## What this repo is

A training lab demonstrating deterministic, agent-driven power-system workflows against a real NEM
network model (CSIRO's Synthetic-NEM-2000-Bus dataset), intended to run on local, open-source
components only (`pandapower`, `powerio`, `agent-framework`, `llama.cpp`, `podman kube play`). See
the root `README.md` for current per-lab status.

## Capabilities & access (per agent/skill context, not one global truth)

Egress and tool availability are tracked **per agent/skill context** — there is no single rule
that holds for every session. Two governing rules:

1. **A tool that doesn't work in your session is an environment mismatch for that context.** Name
   the exact tool/host to the user and stop. Do NOT silently retry, route around it, or fold the
   failure into a repo-wide rule.
2. **A tool/host that works when the table below says otherwise is a session observation, not a
   policy change.** Re-verify before relying on it, report it to the user, and never rewrite the
   table from a single run.

### Capability matrix

| Capability | Context | Expected access / tooling | Mismatch → action |
|---|---|---|---|
| AEMO NEMWeb pull (`nemosis.dynamic_data_compiler`) | any lab agent | blocked — 403 org-policy denial, hand-confirmed via `curl` + `/root/.ccr/__agentproxy/status` | 403 / connection error → inform user; do not retry or route around |
| CSIRO case fetch (`scripts/fetch_csiro_nem_data.py`) | any lab agent | `raw.githubusercontent.com` reachable (this is how Lab 4's real DUID data works) | fetch fails → env mismatch → inform user |
| PyPI / `uv sync` / `uv run` | any lab agent | `pypi.org` reachable; `uv` installed | `uv sync` fails → env mismatch → inform user |
| `github.com` / `gh` / `git` | any lab agent | session-dependent (documented baseline: blocked; observed reachable once, 2026-08-02) | `gh`/`git`/`curl` fails → env mismatch → inform user |
| `crates.io` (`cargo add`/`cargo build`) | any lab agent | session-dependent (documented baseline: blocked — 403, observed 2026-08-02; reachable again, `cargo add --dry-run` resolved cleanly, observed 2026-08-21 building `rust/fft-detector`) | fetch/build fails → env mismatch → inform user; do not assume blocked without a real test in this session |
| codebase-memory skill | `codebase-memory` skill | `codebase-memory-mcp` on PATH, registered in repo `.mcp.json` | handshake / `list_projects` fails → env mismatch → inform user |
| demo display (`just peek` / `just watch`) | demo/display workflow | `chafa` + `mpv` on PATH (install.sh step 7 / `just deploy`) | `command -v` empty → run `just deploy` or inform user |
| animation render (`just render`) | demo workflow | system `ffmpeg` | ffmpeg missing → env mismatch → inform user |
| pods / `podman kube play` | Labs 1-5 golden path | `podman` installed | missing → `install.sh` refuses by design (documented) |

Provenance: the `nemweb.com.au` row is org policy, confirmed by hand (not assumed). The
`github.com` / `crates.io` rows are live session observations — exactly the kind of thing that can
differ per agent/blessing. In any new "sandbox stand-in" docstring, name the specific host you
tried (follow the pattern in `labs/04-aemo-digital-twin-reconciliation/reconcile.py`) rather than
citing this table as a policy guarantee.

## Package management: `uv` only

Never use bare `pip`/`pip3`. Always:

```
uv sync                       # install pinned deps from pyproject.toml/uv.lock
uv run <script>                # run anything inside the project's venv
uv run python -m pytest labs/  # run the test suite
```

## Running the labs

**Canonical commands live in the root `Justfile` — `just --list` is the index** (install, sync,
fetch, proof, test, per-lab check gates, per-lab walkthrough steps, animation render, and the
`peek`/`watch` display recipes). The equivalent bare commands, for reference:

```
uv run scripts/fetch_csiro_nem_data.py   # once: fetch + checksum-verify CSIRO case data into data/
./scripts/run_labs_1_3.sh                # the committed end-to-end proof: fetch -> Labs 1-3 -> pytest -> PASS/FAIL
./scripts/run_lab4.sh                    # the committed end-to-end proof: fetch -> Lab 4 Parts A+B -> pytest -> PASS/FAIL
```

Each lab is also runnable step by step — see the lab's own `README.md` "Command"/"What's
implemented" section (`labs/01-simple-loadflow-fit/run.py --step {load,fit,check}`,
`labs/02-medium-interconnection-screening/workflow.py --step {base,contingencies,check-limits,memo}`,
`labs/03-advanced-provider-bakeoff/orchestrator.py --step {sweep,report,check}`,
`labs/04-aemo-digital-twin-reconciliation/fetch_day.py`, `map_duids.py`, `reconcile.py [--step
check] [--date 2016-09-28]`, `explain_constraint.py`).

**The proof scripts are the proof, not a transcript.** If you change a lab, re-run its proof script
(or at least that lab's `--step check` and pytest file) before considering the change done. Running
commands ad hoc in a session is not proof anything works — a committed script re-deriving the same
result on a clean checkout is.

## Non-negotiable conventions in this codebase

- **No undocumented magic numbers.** Every constant with a specific value (a bus ID, a tolerance,
  a percentage, an iteration cap) must be a named `UPPER_CASE` module-level constant with a comment
  or docstring explaining *where the value came from* (a cited spec line, a real measured quantity
  from an actual run, a derived bound) — never a bare literal dropped into a function body. See
  `labs/01-simple-loadflow-fit/run.py` or `labs/_shared/gridfit.py` for the pattern.
- **PEP 484 type hints everywhere it's practical**, including on module-level constants
  (`FOO: float = 1.0`) and function signatures. Use `TypedDict` for structured dict return values
  instead of bare `dict`.
- **Docstrings on every non-trivial function/class**, explaining what it does, its Args/Returns
  (and Raises where relevant), and — critically for this repo — *why*, when the function stands in
  for something not available in this sandbox (see next point).
- **Sandbox stand-ins must be named, not hidden.** This repo's full spec (`docs/VISION.md`) assumes
  `podman`, a live local LLM server (llama.cpp serving Phi-4-mini/Gemma-4/Llama-3.2-3B), Microsoft
  Agent Framework orchestration, and PowerMCP tool servers. The current build/CI sandbox has none
  of these. Wherever code substitutes something simpler (a deterministic bisection search standing
  in for an LLM's "propose the next trial value" step, direct pandapower calls standing in for an
  MCP tool call, `concurrent.futures.ProcessPoolExecutor` standing in for a podman-hosted pod fan-out),
  the substitution must be named explicitly in that function's docstring *and* in the lab's own
  `README.md` "Sandbox notes" section. Never silently swap in a simplification without saying so at
  the call site — a future contributor with real infrastructure available needs to find the exact
  line to replace.
- **Every lab is self-checking.** A lab's `--step check` (or equivalent) command re-runs its
  computation and diffs the result against a committed `expected_*.json` fixture with a documented
  tolerance, and has a `test_labN.py` pytest wrapper. If you add a new lab step or task, add or
  update the fixture and the test — don't leave a step that can only be "vibes-checked" by reading
  printed output.
- **Real data over synthetic data, wherever real data is actually available.** The CSIRO
  Synthetic-NEM-2000-Bus MATPOWER case files are real (if synthetic-topology) public data, fetched
  and checksum-verified by `scripts/fetch_csiro_nem_data.py` — never hand-roll a toy network when a
  real case file already covers the need. Physics results (voltages, loadings, convergence) must
  come from an actual `pandapower.runpp()` call, never be fabricated or hard-coded to look
  plausible.
- **Golden-path licensing (PSCADOSSE):** the golden path prefers foundational, OSI-approved,
  permissive components (Apache-2.0 / MIT / BSD), foundation-backed where possible — see
  `docs/PSCADOSSE.md` for the policy and the verified license map (incl. the DPsim/MPL-2.0
  exception and the "copyleft display CLIs are fine — they're not API surface" distinction).
  Record a new dependency's license at the point of adoption.
  **2026-10-23 (user):** copyleft is acceptable, though permissive is still preferred, and
  LibreOffice is not used. See `docs/PSCADOSSE.md`.
- **No `b00t` dependency, no commercial power-system engines on the golden path, no cloud LLM API
  keys anywhere.** See `docs/DEFINITION_OF_DONE.md` "Governance / non-goals held" for the full list.

## PowerCoder agent evaluation: known failure modes

Measured on 2026-10-21 from 76 Qwen3.5-4B base attempts (r2: 31, r3: 45) on the 306-task held-out
benchmark. Re-derive with `uv run python -m powercoder.inference.failure_modes <run>/base`, which
assigns each attempt one cause in a fixed precedence order. Don't quote these counts as results:
the benchmark runs in subfamily order (26 report, 120 load-scale, 120 line-rating, 40 dispatch),
so early attempts are not a representative sample. Status and live state:
`docs/POWERCODER_HANDOFF_2026-10-23.md`.

**Feasibility.** Every reference converged; each report needs one
`run_study_trial(operation="none")` call; the reference bisection reached each fit's tolerance in
2–7 trials, against a 24-call / 12-turn budget. **Correction 2026-10-23 (FM-25):** that was
checked against the reference solver, not the tool. Until `run_study_trial` accepted outages that
isolate buses, 8 of the 306 benchmark's 26 reports (8 of 25 in the 100-task sample, 2 of 3 in the
smoke set) were unsolvable through it. Since FM-25 every task is solvable through the tool, and the
tool's report fields match the reference exactly on all 71 report tasks checked.

### Benchmark and harness defects (fix only in a new run root, never mid-run)

| ID | Mode | Evidence | Effect |
|---|---|---|---|
| FM-1 `contingency_dropped` | `run_power_flow` has no `contingency_line` property and no `additionalProperties: false`; `PowerMCP.execute_tool` silently ignores unknown arguments | 34/52 report attempts passed `contingency_line=` to it, got base-case numbers with no error, and answered them | Honest-looking wrong answers. The tool, not just the model, misleads |
| FM-2 `response_token_cap` | `MAX_TOKENS = 2048` per response (`tool_probe.py`); reasoning is spent before the tool call or answer | 32/76 attempts; 32 turns ended with `finish_reason: length`; recorded as `incomplete_generation` with an empty answer | The leading cause of fit failures |
| FM-3 nondeterministic prompt | Solver responses include wall-clock `execution_time_s`, which is fed back to the model | r3 vs r2: same task, temperature 0; identical until `execution_time_s` 1.2655 vs 1.1948 s, then diverged; only 14/31 shared answers identical | Single greedy pass@1 is not reproducible between runs; paired deltas carry this noise |
| FM-4 `solver_correct_strict_rejected` | The strict report parser requires the reference's wording; Markdown or tabular answers fail | 3/76: numerically correct per `solver-verified-domain-v1` | Report both metrics (`domain_scorecard.py`), as already required |
| FM-5 context overflow (latent) | vLLM `--max-model-len 16384` must hold prompt + `max_tokens`; an over-length request is an HTTP error, so `evaluate_agent` raises `controller_error` and **stops the whole run** rather than failing one task | Largest prompt observed 7,583 tokens. Harmless at 2048, but 8192 + 7,583 is already 15,775 | Raise context with any token-budget increase, and record overflow as a per-task failure |
| FM-6 inference timeout | `INFERENCE_TIMEOUT_S = 600` per request at ~19 tok/s single-stream; until r5 a timeout was an `httpx.ReadTimeout` that stopped the run | 2048 tokens ≈ 110 s; 8192 tokens ≈ 430–585 s. **Occurred in r4** at 1800 s, 2026-10-22 04:41: the two Granite 8B servers side by side decoded ~32–36 tok/s each (~4 tok/s per stream, ~2,000 s per full response), and one timeout aborted both Granite evaluators at 26 and 30/306 | Since r5 a timeout is the per-task terminal `inference_timeout`; Granite servers run one at a time. The timeout value stays 1800 s because `compare_agents` refuses runs with different values |
| FM-17 constant `constraint_violation` | The snem-sa-2000 base case already has 39 bus-voltage violations, so every `run_study_trial` returns `status: constraint_violation` whatever the trial value | r4 Qwen base 1045/1045, r5 Granite base 77/77; Granite retried `scale=0.95` six times although each response carried the metric it needed | The status carries no signal for fits and reads as a failure |
| FM-18 `contingency_line: 0` outage | `0` is a valid line index, so a model writing "no contingency" as `0` silently studies the network with line 0 out | r5 Granite base: 66 of 104 calls | Fit trials answer a different question with no error — FM-1 in a new form |
| FM-19 training/evaluation mismatch | `training/unsloth/train.py` trains on `{prompt → expected.answer}` text: no tools or tool calls, a different system prompt, `seq_len: 1024` (evaluation prompts 1.7k–7.5k tokens), 100 steps | Qwen student 0/306 in r4; the Granite student uses the same recipe | Students learn to answer without tools; gate any retrained student on a 20-task smoke run before a full evaluation |
| FM-20 unscoreable reference fit answers | Fit references read "The scale factor is s = 1.037500. At this scale, …"; `extract_scale` rejects a number followed by a full stop, so the reference itself scores as no answer. The prompt asks "Return only the scale factor" | Found 2026-10-22 building r6 trajectories: all 3 fit references in a 5-row sample failed `grade()`; the r4 students were trained on these sentences | Trajectories answer the bare scale (`1.037500`), which the scorer reads. The reference text in the dataset is unchanged |
| FM-21 validation/test splits leak training tasks | `powercoder/datasets/split.py` splits rows, not tasks: report tasks repeat (one base case per network, N-1 lines drawn twice), so identical tasks land on both sides | `audit_rows` against the training split: validation 6 overlapping + 5 duplicate prompts (all `pf-report`), test 2 + 1. The 306/100-task benchmarks are clean | The r6 smoke set is drawn with `sample_benchmark.py --exclude <train split>`, which drops overlaps and duplicates and requires a clean audit (3 independent reports remain) |
| FM-22 prompt/target seam tokens | Tokenising served prompt + target as one string lets BPE merge across the seam: Qwen's `<think>\n` + `\n</think>` becomes one `\n\n` token, so the served prompt's last token never appears in training | First r6 Qwen student: teacher-forced loss 0.0006, but from the served prompt it answered without tools (0/8 on the post-merge greedy check) | `training/agent_sft.encode` tokenises prompt and target separately; `generation_check` gates every merge. Don't judge a student by training loss alone |
| FM-23 sorted tool schemas in training prompts | `common.write_jsonl` sorts keys, and chat templates render tools with `tojson` in stored order, so every training prompt's tool block read `{"function": {"description": …` while the evaluation serves `{"type": "function", "function": {"name": …` | Found 2026-10-22 22:50: same length (4,206 chars), different text; the 8/8 `generation_check` rendered from the same sorted rows and could not see it. Applies to the seam-fixed v1 Qwen student and the stopped v2 run | `trajectories.write_in_order` keeps build order; `test_trajectories.py` asserts the stored tools serialise exactly like the served list |
| FM-25 isolating outages refused by the tool | `run_study_trial` raised "Unsolved or nonfinite voltage on an in-service bus" whenever an outage left buses de-energized (pandapower NaN), while the reference solver skips NaN and reports the contingency as converged | r6 smoke 2026-10-23: both of the Qwen student's "fabricated" reports were these (12 identical failing calls); 8/25 benchmark reports and 2/3 smoke reports affected; training reports 0/43, so no trajectory changed (rebuilt hash identical) | De-energized buses are skipped and listed (`de_energized_buses`, present only when non-zero); a NaN on the intact network still fails. The first r6 attempt (`agent-study-r6/`) was stopped after its smoke stage and is not a result |

| FM-26 unrecoverable unknown id | `run_study_trial` refused an unknown id with "Unknown observation element" alone, and `load_network` returns counts only; snem-sa-2000's 503 bus ids are sparse (985..10106) | r6b Qwen student, 3 of 25 reports: `observation_id: 999`, then guessed 1, 2, 3, … (or 0 nine times) until `turn_budget_exhausted`; two were mislabelled `fabricated` from the last turn's narration until `failure_modes` counted only final answers (`cb855c1`) | Unknown-id errors name the id, the element count and the lowest 10 ids (`studies.unknown_element`); trajectories v3 add an `unknown_bus` recovery to every report recovery row. On `chore/bus-discovery` until r6b's queued arms finish |
| FM-27 unpinned interpreter changes solver output | No `.python-version`; `uv.lock` forks scipy on Python (1.16.3 below 3.12, 1.18.0 from 3.12). The main `.venv` is system 3.12.3; a fresh `uv sync` in a worktree chose a managed 3.11.17 | 2026-10-23: the same trajectory build on 3.11 differed from v2 in the last digits of every solver float (`0.13112597909044268` vs `0.1311259790904505`); on 3.12.3 all 357 v2 rows rebuilt byte-identically | `.python-version` pins 3.12.3, the interpreter every r4–r6 run and trajectory used |
| FM-28 required unused arguments force invented ids | `run_study_trial` (IR 0.1.0) required `scale`, `element_id`, `observation` and `observation_id` for every operation, though a network-wide report uses none of them and load scaling uses no element; the report prompts name no bus | FM-26's 999 was invented to fill a required field; every trained report observed snem-sa-2000's lowest bus, 985, a network-specific number learnt as procedure; Fix 3's `unknown_bus` recovery injected 984, not the observed gap id, so it trained a different mistake | IR 0.2.0 (backlog 0015): only arguments an operation uses are required; reports send none; recoveries and refusals draw bad ids from each network's own id gaps (`unsolvable.absent_id`). Old-shape calls answer byte-identically (r6b replay unchanged) |

### Model behaviour (count as model failures; don't patch around them)

| ID | Mode | Evidence |
|---|---|---|
| FM-7 `"None"` string arguments | Sends `"None"` instead of JSON `null` for `contingency_line` / `observation_id`, so MCP validation errors | 103 of 117 tool errors, in 24/76 attempts |
| FM-8 `turn_budget_exhausted` | Retries the same invalid call (often FM-7) until 12 turns are used | 7/76, all fits |
| FM-9 wrong tool for N-1 | Calls `run_power_flow` instead of `run_study_trial(operation="none")` for contingency reports; this triggers FM-1 | 49 `run_power_flow` calls vs 23 `run_study_trial` calls on reports |
| FM-16 student repetition loop | The original Unsloth Qwen student, at greedy temperature 0, repeats the same reasoning lines 40–60 times on turn 1 (26–55 distinct of 188–403 lines), debating `run_power_flow` vs `run_study_trial`, until `max_tokens`; or it stops with reasoning and no content | r4: all first 8 finished student tasks `incomplete_generation`; base median 620 tokens/turn. Each looping turn costs a full 8192 tokens, which sets r4's pace |

### Infrastructure (GB10 host)

| ID | Mode | Evidence / mitigation |
|---|---|---|
| FM-10 unified-memory OOM | GB10 CPU and GPU share 121 GB. A resident llama-swap model (`qwen3.8-flash-next` ≈ 96 GB) plus benchmark vLLMs pushed the host into a global OOM kill, 2026-10-21 10:02:16 | llama-swap `globalTTL: 600`; Granite backend on demand; `run_comparison.await_memory()` before every GPU stage |
| FM-11 unsupervised processes | r2 was detached exec sessions plus a driver that adopted a PID; nothing owned the pipeline | Launch only via `scripts/launch_powercoder_comparison.sh` (systemd `--user` unit) |
| FM-12 readiness `ReadError` | Rootless podman port forwarding resets connections while vLLM loads; this killed r3's first launch after 0.4 s | `serve()` tolerates `httpx.TransportError`; container exit is the failure signal |
| FM-13 conmon ignores SIGTERM | Container conmon processes sit in the unit cgroup and held a stop for the full timeout | The launcher's `ExecStop` stops containers first |
| FM-14 misleading GPU utilisation | `nvidia-smi` reports 94% at 31 W with a single decode stream at 19 tok/s; decode is memory-bandwidth bound | Measure tokens/s and running requests in the vLLM log, not utilisation % |
| FM-15 stop not journaled (open) | `systemctl --user stop powercoder-agent-domain-r3` at 13:43:57 left no `driver_stopped`, `stage_exit` or evaluator `controller_error`; `pipeline.jsonl` last written 10:37. Not reproduced: the same `systemd-run` + `uv run` + SIGTERM-handler shape, with and without `ExecStop`, recorded the stop | Don't rely on the journal for a stop's cause; read `journalctl --user -u <unit>` |
| FM-24 waiter units cannot find `uv` | The systemd user manager's PATH has no `~/.local/bin`; `launch_agent_study.sh` did `UV="$(command -v uv)"` under `set -e`, so a waiter unit that exec'd it exited 1 with no output. 2026-10-23 06:40: both students passed `generation_check` 8/8 but r6 never launched (GPU idle ~25 min) | Both launchers resolve `uv` explicitly (`$HOME/.local/bin/uv` fallback) and refuse with a message; a launch from a unit is checked with `systemd-run --user --wait --pipe`. **Recurred 2026-10-23 19:5x in r6b's Lab 10 pilot:** `restorebench_lab.py` ran a bare `uv` as a subprocess (`FileNotFoundError: 'uv'`) after all four evaluations and the comparisons had completed; it now resolves `uv` the same way |

**Fixed from r4 on** (r1–r3 ran without these fixes, so their results aren't comparable with r4's):
- FM-1: both canonical schemas now set `additionalProperties: false`, so MCP rejects unknown arguments with a visible validation error.
- FM-2: `MAX_TOKENS` raised to 8192.
- FM-3: `execution_time_s` removed from model-facing MCP responses.
- FM-5: context raised to 32768, and an overflow is recorded as the per-task terminal `context_exhausted`.
- FM-6: `INFERENCE_TIMEOUT_S` raised to 1800.

**Fixed from r5 on:** FM-6 timeouts end only that attempt (`inference_timeout`), and Granite base and student are served one at a time. Neither changes a budget, and no r4 Qwen attempt timed out, so r5 reuses r4's Qwen runs (`run_comparison --qwen-from`).

**Fixed from r6 on** (`training/run_agent_study.py`; r1–r5 results are not comparable with r6):
- FM-17: each `run_study_trial` response carries `metric_valid` and `base_case_voltage_violations`; the canonical status is unchanged.
- FM-18: `contingency_line` is optional, and each response names the line taken out or says "none (intact network)". Taking line 0 out moved the r5 Granite test bus by 9.3e-5 pu, 71% of that task's tolerance.
- FM-19/FM-20: students are retrained by `training/agent_sft.py` on `powercoder/datasets/trajectories.py` output. That is 238 scripted solver trajectories from the training split, each graded by the benchmark's own `grade()`, rendered with the evaluation's system prompt and tools, and gated by a 20-task validation smoke run (≥10 passes).
- Tasks are run as a seeded, stratified, interleaved 100-task sample (`powercoder/datasets/sample_benchmark.py`), so any prefix covers every subfamily.
- Latency and peak memory are measured separately: `powercoder/inference/latency_bench.py` runs on an idle server, and host MemAvailable is sampled for each server's whole life.

**Fixed from r7 on** (`scripts/r7_chain.sh`; r6 results are not comparable with r7 for tool errors, since the tool's schema, description and unknown-id message changed):
- FM-26/FM-28: IR 0.2.0 tool and schema; students retrained on `trajectories-20261024-v6` (IR 0.3.0: the tools also serve pandapower's IEEE 39/118 and PEGASE 89 cases, and take an optional `load_level` operating point) (238 procedures without placeholder ids, 238 recovery variants incl. id-free `invented_observation`/`wrong_element`, 25 `pf-unsolvable` refusals). Hyperparameters unchanged.
- PC-REQ-3/4: every r7 root records each directory's error ledger and IR protocol validity (`run_agent_study.record_ledgers`); tool time is journaled (`tool_seconds`), never shown to the model.
- Fit prompts in wording v2 (`powercoder/datasets/reword.py`): v1 said "applied to bus B" for a load-scale fit, which scales every load, so a model told to refuse incoherent tasks could rightly refuse it. Every suite and the training split were re-rendered from their inputs (297 fit prompts, 0 refused); the scripted procedure still scores 20/20 smoke and 100/100 benchmark; trajectories v4 differ from v3 only in 422 user prompts.
- Abstract skill is measured, not assumed: the judgement suite's snem-1803 reports (a network no training row uses) and the 28-task unsolvable suite (incl. never-trained unsolvable N-1 reports) run in every model's benchmark session.

r4 also runs 8 attempts per server concurrently, with base and student side by side and Granite training alongside. Concurrent batching changes floating-point reduction order, which is one more reason (with FM-3) not to expect bitwise reproduction of an earlier run.

### PowerCoder evaluation requirements

Each requirement names its source and the test that checks it. These are the
lab's own register (the r00t SysML register is the long-term home). PRD-0010 supersedes these interim IDs:
PC-REQ-1 → PC-REQ-004 (evidence provenance), PC-REQ-2 → PC-REQ-002/055; the tests below stay their
verification until gate G1.

| ID | Requirement | Source | Checked by |
|---|---|---|---|
| PC-REQ-1 | Every number in a final answer comes from that attempt's tool responses or its prompt; any other number fails the attempt in both the strict and the solver-verified scores | User rule 2026-10-22 ("never guess or make up numbers"); FM-19 student | `powercoder/inference/test_provenance.py`, `test_failure_modes.py::test_an_answer_with_numbers_no_tool_produced_is_fabricated` |
| PC-REQ-2 | Strict tool calling is studied as its own 2×2 arm on the smoke suite: server `--tool-strict-level` {auto, parameter} × first turn {auto, `required`}, every comparison naming exactly the conditions it spans (`compare_agents.CONDITIONS`). No grammar is stored here: vLLM builds the model-native tool grammar per request from the request's tools, and the prompt is unchanged | Forced-arm decision 2026-10-22 (item 5); independent review 2026-10-23 (backlog 0011: forcing and schema enforcement must not be confounded); measured live: forced replies were native Qwen XML | `test_tool_probe.py::test_required_tool_choice_applies_to_the_first_turn_only`, `test_compare_agents.py::test_a_different_server_strict_level_must_be_named`, `training/test_run_agent_study.py::test_strict_arm_is_a_2x2_per_model_compared_with_its_own_baseline` |
| PC-REQ-3 | A model SHOULD NOT fail on a spelling it makes procedurally when that spelling can be trained out (e.g. FM-7's `"None"`): such errors are removed by training, not tolerated by loosening the scored contract | User rule 2026-10-24 | `protocol_suite.py` (per-model IR-valid rate; students 100% on r6b) |
| PC-REQ-4 | Every evaluation session MUST track LLM errors both as metrics (counts by class) and as unrecoverable lost time: wall time and tokens from an error that is never recovered to the attempt's end | User rule 2026-10-24 | `powercoder/inference/test_error_ledger.py`; `error_ledger.py` over each run root (r6b: unrecoverable share 94.1% Qwen base, 79.0% Granite base, 13.9% Granite student, 8.5% Qwen student of summed request time) |

## Repo layout quick reference

- `docs/VISION.md` — full architecture plan and per-lab spec; the source of truth for what each
  lab is *supposed* to do.
- `docs/DEFINITION_OF_DONE.md` — the checklist this repo is built against; consult before claiming
  anything is "done."
- `docs/backlog/` — gaps found after the fact (not deliberate non-goals — see
  `docs/VISION.md` §12 for those), one numbered file per item, indexed at
  `docs/backlog/README.md` and pointed to from `docs/VISION.md` §13.
- `labs/_shared/gridfit.py` — shared powerio->pandapower loading and the bisection search helper
  used by Labs 1 and 3.
- `labs/_shared/scenario_engine/` — the composable `Generator`/`Detector` platform
  (`docs/prd/0001-composable-generator-detector-platform.md`): `generators.py`/`detectors.py`
  (the two Protocols plus five concrete classes each), `scenario.py` (schedule DAG extension +
  `run_scenario()` driver), `scoring.py` (two-section PASS/FAIL report), `demo_scenario.py` (the
  PRD's own synthetic proof scenario). Imported by Lab 5's `chaosnet.py`/`run_dpsim.py` (whose
  original single fault becomes a one-`NetworkFaultGenerator` degenerate case, zero behaviour
  change) — a deliberate exception to `gridfit.py`'s "labs import `_shared`, never the reverse"
  precedent, documented in the package's own `__init__.py`, since this module's whole point is
  reusing Lab 5's `phase_model.py` views directly rather than reimplementing them. Self-checked by
  `labs/_shared/test_scenario_engine.py` against `labs/_shared/expected_demo_scenario_run.json`.
- `labs/01-simple-loadflow-fit/`, `labs/02-medium-interconnection-screening/`,
  `labs/03-advanced-provider-bakeoff/` — implemented; each has real code, a fixture, a pytest test,
  and a README with "Sandbox notes."
- `labs/04-aemo-digital-twin-reconciliation/` — implemented (Part A + B required, Part C optional,
  also implemented); see `docs/LAB4_AEMO_REAL_DATA.md` and the lab's own `README.md`.
- `labs/05-spartan-chaosnet-transient-stream/` — implemented (laptop-portable core; the
  hardware-validated extension is optional and out of scope). See `docs/LAB5_SPARTAN_CHAOSNET.md`
  and the lab's own `README.md` "Sandbox notes" for the real DPsim/VILLASnode session and the one
  node-type substitution found by actually running it.
  `labs/05-spartan-chaosnet-transient-stream/scenarios/` — PRD-0002's SA 2016 Black System cascade
  scenario (`sa_2016_black_system.py`), built on `labs/_shared/scenario_engine/`; see that script's
  own module docstring for its tap-role mapping and named engineering deviations from the PRD's
  original sketch. Self-checked by `test_sa_2016_black_system.py`, skipped by default (opt in with
  `RUN_SLOW_SCENARIOS=1`) since its `--step check` solves ~43s of grid time.
- `labs/06-sysml-digital-thread/` — implemented (both tracks). A SysML v2/MBSE tooling evaluation
  (PRD-0006), not a physics lab: two tracks (Track A — this repo's own real Agent/MCPServer/
  DataSource inventory; Track B — a real `data/snemSA.m` bus/generator/line cluster; Track C —
  PRD-0005's own real pipeline-phase sequence) share one LinkML→`.sysml`→syntax-gate→isometric-SVG
  (+Track A SBOM) pipeline. See the lab's own `README.md` "Design notes" for the real-tool
  evaluation (SysML v2 Pilot Implementation via a GraalVM container) and exactly why it fell back to
  a named in-repo stand-in.
- `kube/benchmark-runner-job.yaml` — a written, valid Kubernetes Job manifest for Lab 3, not yet
  executed with `podman` in this sandbox (Lab 3's own fixture doesn't require it); see the file's
  own header. `kube/villasnode-tap-pod.yaml` (Lab 5), by contrast, has been actually run with
  `podman kube play` in this sandbox — see its own header.
- `data/` — gitignored; populated by `scripts/fetch_csiro_nem_data.py`, never vendored/committed.
- `benchmarks/power-agent-bench-lite/results/` — gitignored except `.gitkeep`. Both `scorecard.json`
  and `scorecard_chart.png` are regenerated, local-only output from `--step report`/`--step check`
  (real re-derivation; `wall_clock_s` differs every run), never committed — the committed, diffable
  fixture is `labs/03-advanced-provider-bakeoff/expected_scorecard.json`.
- `labs/04-aemo-digital-twin-reconciliation/duid_mapping.csv` — Lab 4's committed, auditable
  DUID-to-synthetic-generator mapping, regenerated by `map_duids.py`.
