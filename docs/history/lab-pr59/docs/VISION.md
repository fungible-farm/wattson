# PowerAgent NEM Lab — Vision & Architecture Plan

Status: **PLAN / PRE-BUILD**. Nothing in `labs/`, `kube/`, or `benchmarks/` is working code yet —
this document is the spec those directories will be filled against. Treat it as a training-guide
outline (MEA137A style: numbered steps, state what you did, state what you should observe, state
why it matters to an AEMO modeller), not a sales deck.

## 0. One paragraph, no adjectives

We are composing five existing open-source projects — **pandapower**, **powerio**, **PowerMCP**,
**PowerWF/PowerSkills** and **Microsoft Agent Framework** — around a real, public NEM network
model (CSIRO's Synthetic-NEM-2000-Bus dataset), run entirely on local compute (llama.cpp serving
Phi-4-mini on CPU, no cloud API keys), packaged as `podman kube play` pods so each piece can be
started, replaced and scaled independently. The deliverable is five runnable labs (simple, medium,
advanced, a real-AEMO-data lab, and an EMT-domain transient-streaming lab) that show
deterministic, scriptable agent workflows doing the kind of thing a power modeller currently does
by hand: load a case, run a study, check limits, produce a report — plus a benchmark harness that
scores how well different *local* LLM providers perform simple parameter-fitting/regression tasks
against a public power-systems benchmark, a reconciliation exercise against real historical AEMO
market data, and a real-time transient stream feeding an external edge anomaly-detection project.

Nothing here requires PSS/E, PowerFactory, a cloud LLM subscription, or `b00t`.

## 1. Grounding: the paper this lab operationalises

This repo is not an original architecture — it is a runnable, laptop-scale implementation of the
roadmap laid out in:

> Q. Zhang and L. Xie, "PowerAgent: A Road Map Toward Agentic Intelligence in Power Systems:
> Foundation Model, Model Context Protocol, and Workflow," *IEEE Power and Energy Magazine*,
> vol. 23, no. 5, pp. 93–101, Sept.–Oct. 2025.
> [ieeexplore.ieee.org/document/11131348](https://ieeexplore.ieee.org/document/11131348/) ·
> open-access preprint: [doi.org/10.36227/techrxiv.174918210.07854858/v1](https://www.techrxiv.org/doi/full/10.36227/techrxiv.174918210.07854858/v1)
> · project site: [poweragent.seas.harvard.edu](https://poweragent.seas.harvard.edu/) (Qian Zhang
> and Le Xie, Harvard SEAS, Power and AI Initiative)

> "The operational resilience of electric power grids is facing growing challenges caused by aging
> infrastructure, increasing system complexity, and a rising frequency of extreme weather events.
> Traditional control paradigms, built around deterministic models and human-in-the-loop decision
> making, will become insufficient to manage the escalating demands on power grids. In response,
> recent advances in artificial intelligence (AI)—particularly the emergence of general-purpose AI
> agents capable of tool use, reasoning, and task orchestration—offer a new direction for
> enhancing grid flexibility and resiliency. This article introduces the concept of the Power
> Agent: an AI-enabled, context-aware assistant that leverages foundation models, standardized
> tool interfaces, and structured workflows to support grid operation and planning decisions. We
> discuss the conceptual architecture, implementation pathways, and system-level benefits of
> deploying Power Agents in power grid operations, with an emphasis on augmenting operator
> capabilities, improving situational awareness, and reducing operational bottlenecks."
> — abstract, Zhang & Xie 2025

The paper's own subtitle names its three architectural pillars — **Foundation Model, Model Context
Protocol, and Workflow** — and the Power-Agent GitHub organisation is that same triad, shipped as
code:

| Paper's pillar | Repo | Role in *this* lab |
|---|---|---|
| Foundation Model | [PowerFM](https://github.com/Power-Agent/PowerFM) | §7, Lab 3 — a domain-trained baseline sitting alongside the general-purpose LLM agents |
| Model Context Protocol | [PowerMCP](https://github.com/Power-Agent/PowerMCP) | Tool layer — pandapower exposed to any MCP client |
| Workflow | [PowerWF](https://github.com/Power-Agent/PowerWF) / [PowerSkills](https://github.com/Power-Agent/PowerSkills) | Orchestration pattern, reimplemented here on Microsoft Agent Framework |

So the "why compose these five projects" question in §3 has a one-line answer: **this is the
reference architecture from the IEEE paper, assembled from the org's own open-source
implementation of each of its three named pillars**, with a local-only inference stack and a real
NEM case substituted in for the demo. Everything downstream in this document is that
substitution made concrete and runnable.

## 2. Why (the actual problem, not the pitch)

Provisioning studies (new generator/load connection screening, N-1 contingency checks, model
validation before a planning submission) are currently a chain of manual steps: pull a case,
open it in a desktop tool, run a study, eyeball the output, write a memo. Every step is a place
where two engineers get different answers because they ran a slightly different version of the
tool, a slightly different case file, or forgot a step.

The pattern we want to demonstrate — not sell, demonstrate — is: **encode the steps as a
deterministic script, let an agent drive the script, containerise each tool so the exact version
is pinned, and get the same answer every time.** That's the entire thesis. Everything else in this
document is plumbing to make that demonstrable in an hour, on a laptop, with no vendor accounts.

## 3. Ecosystem map (what we're composing, not building)

| Project | Role in this lab | Language | Note |
|---|---|---|---|
| [pandapower](https://www.pandapower.org/) | The power-flow / OPF / contingency engine | Python | Wraps PYPOWER + pandas; solves AC/DC PF, OPF, short-circuit, topological search |
| [powerio](https://github.com/eigenergy/powerio) | Fast MATPOWER `.m` / PSS/E `.raw` / PyPSA-CSV parser + format converter, Python bindings over Rust | **Rust**, Python bindings | This is our Rust requirement — it reads CSIRO's `.m` files directly and its own benchmark suite validates against pandapower, PowerModels.jl and egret |
| [PowerFM](https://github.com/Power-Agent/PowerFM) | Domain-trained foundation models for power/energy: **OpenPowerBench** (transformer models for topology-dependent tasks — power flow, OPF, contingency — and topology-independent tasks — load/price forecasting), **GridLDM** (diffusion models for time-series generation), **GridFM** (GNNs trained on grid topology), **mAIEnergy** (multimodal) | Python, models on Hugging Face Hub | This is the paper's "Foundation Model" pillar; §7 Lab 3 pulls an OpenPowerBench load-forecasting checkpoint as a fixed domain-specific baseline row, run *without* any LLM in the loop, next to the general-purpose agents |
| [PowerMCP](https://github.com/Power-Agent/PowerMCP) | MCP servers exposing pandapower (and other engines) as tools an LLM can call | Python | `pip install powermcp && powermcp install` |
| [PowerSkills](https://github.com/Power-Agent/PowerSkills) | Agent-Skill playbooks on top of PowerMCP — "load case → solve base case → escalate to contingency → mitigation playbook" | Python + Markdown SKILL.md | Progressive disclosure: low-risk ops first |
| [PowerWF](https://github.com/Power-Agent/PowerWF) | Reference agentic workflows (grid-impact evaluation, market-inquiry agent) | Python | We replace their suggested LangGraph/AutoGen/CrewAI orchestrator with Microsoft Agent Framework per this lab's brief |
| [PowerAgentBench](https://github.com/Power-Agent/PowerAgentBench) family | Deterministic evaluators: agent proposes a solution, an independent checker recomputes the physics and scores feasibility | Python | We borrow the *pattern* (structured task → tool calls → deterministic re-check → score) for our own "lite" benchmark rather than depending on the full external harness |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | Orchestration: sequential / concurrent / handoff / Magentic workflows, native MCP client support, checkpointing | Python (+.NET, unused here) | Points at any OpenAI-compatible endpoint — that's how it talks to our local llama.cpp server |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Local inference server, OpenAI-compatible `/v1/chat/completions`, CPU-only | C/C++ | Serves Phi-4-mini-instruct (GGUF, Q4), swappable for Gemma-4/Llama-3.2 for the bake-off lab |
| [CSIRO Synthetic-NEM-2000-Bus](https://github.com/csiro-energy-systems/Synthetic-NEM-2000bus-Data) | The actual grid: ~2000 buses, all NEM states + Tasmania, 3 HVDC interconnectors, CC-BY 4.0 | MATPOWER `.m` | Our one and only network model — real topology, synthetic parameters, no confidentiality issue |
| `podman kube play` | Composition/runtime for every service above | — | Each pod is one YAML file, no cluster, no kubelet, just Podman turning k8s manifests into local containers |
| [NEMOSIS](https://github.com/UNSW-CEEM/NEMOSIS) | Downloads/caches real historical AEMO MMS dispatch data | Python | Lab 4 only — `pip install nemosis`, `dynamic_data_compiler()` / `static_table()` |
| [mms-guide](https://www.mdavis.xyz/mms-guide/) | Documents the MMS table joins (`PARTICIPANTID → STATIONID → DUID`, versioning/dedup rules) | reference, not code | Lab 4 only |
| [susantoj/NEM_constraints](https://github.com/susantoj/NEM_constraints) | Decodes public NEMDE generic-constraint-equation formulations into LHS/RHS terms | Python | Lab 4 only — used instead of writing a constraint-equation parser |
| [akxen/egrimod-nem](https://github.com/akxen/egrimod-nem) | Prior art for collating DUID/generator metadata from AEMO's MMSDM+NTNDP | Python (notebooks) | Lab 4 only — referenced for methodology, we keep the CSIRO topology rather than its own GA/ABS-derived network |
| [SimBench](https://github.com/e2nIEE/simbench) | 13 real benchmark grids (LV–EHV) with full-year time series, pandapower-native | Python | Lab 5 only — seeds "chaos-net" procedural topology generation with realistic parameter distributions |
| [DPsim](https://github.com/sogno-platform/dpsim) | Real-time-capable power-flow / dynamic-phasor / EMT solver, deterministic timesteps down to 50µs | C++, Python bindings | Lab 5 only — the actual transient-physics engine; pandapower stays the steady-state tool everywhere else |
| [VILLASnode](https://github.com/VILLASframework/node) | Real-time multi-protocol gateway (~18 protocols incl. native IEC 61850 Sampled Values/GOOSE), ships as an OCI container | C/C++ | Lab 5 only — DPsim's own documentation names this as its recommended external-interface path; drops into `podman kube play` like every other pod |

Everything in this table already exists. This repo's job is the glue: kube manifests, the five
lab scripts, and the benchmark scorer. **Verify exact install commands against each project's
current README at build time** — the ones quoted above were fetched in July 2026 and may drift.

## 4. Component architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ podman kube play  (single pod-network, all on localhost)            │
│                                                                       │
│  ┌───────────────────┐   OpenAI-compatible HTTP   ┌────────────────┐│
│  │ llamacpp-server    │◄───────────────────────────│ agent-framework││
│  │ (Phi-4-mini GGUF,  │                            │ orchestrator   ││
│  │  CPU, --n-gpu-layers 0)                         │ (runs as a     ││
│  └───────────────────┘                             │ plain uv-run   ││
│                                                     │ process, not   ││
│  ┌───────────────────┐   MCP (stdio→HTTP bridge)   │ a pod — it's   ││
│  │ powermcp-pandapower│◄────────────────────────────│ the "driver")  ││
│  │ MCP server pod     │                            └────────────────┘│
│  └───────────────────┘                                                │
│           ▲                                                           │
│           │ loads                                                     │
│  ┌───────────────────┐                                                │
│  │ CSIRO snem2000.m   │  parsed by powerio (Rust, via Python binding) │
│  │ → pandapower net   │  before being handed to pandapower/PowerMCP   │
│  └───────────────────┘                                                │
└─────────────────────────────────────────────────────────────────────┘
```

The orchestrator itself is **not** containerised — it's the thing a human runs (`uv run
labs/02.../workflow.py`). Everything it *depends on* (model server, tool server) is a pod. That
split matters for the "why Kubernetes" argument in §9: you containerise the things that need
version-pinning and horizontal scale-out, not the control script.

## 5. Data: CSIRO Synthetic NEM 2000-Bus

- Source files are MATPOWER `.m` text case files: `snem2000.m` (full NEM), `snem1803.m`
  (mainland), `snem197.m` (Tasmania), `snem2000_acdc.m` (+3 HVDC links), `snem2000_tnep.m` (+9
  transmission-expansion candidates), and per-region `snemNSW.m` / `snemQLD.m` / `snemVIC.m` /
  `snemSA.m`.
- License CC-BY 4.0 — redistribution in this repo is fine with attribution; `scripts/
  fetch_csiro_nem_data.py` pulls them at install time rather than vendoring the raw files, to keep
  the repo small and to always pick up upstream corrections.
- Path in: `powerio` parses the `.m` file → normalised case struct → handed to
  `pandapower.converter.from_ppc(...)` (or pandapower's native MATPOWER path, whichever `powerio`'s
  Python binding target is — confirm at implementation time) → a `pandapowerNet` object every
  other component operates on.
- Why this dataset specifically: it's public, it's NEM topology (not a generic IEEE 14-bus toy),
  it's the right order of magnitude to make a contingency-analysis lab feel real (2000 buses is
  enough that N-1 over all lines is a genuine "why would I want this parallelised" moment), and
  it needs zero data-sharing agreement.

## 6. Repo layout (target state)

```
nem-poweragent-lab/
├── install.sh                       # the one command a board member's laptop needs to run
├── pyproject.toml                   # uv-managed workspace, single lockfile
├── docs/
│   ├── VISION.md                    # this file
│   ├── DEFINITION_OF_DONE.md
│   ├── LAB4_AEMO_REAL_DATA.md       # Lab 4 lives in its own file — see §7a
│   └── LAB5_SPARTAN_CHAOSNET.md     # Lab 5 lives in its own file — see §7b
├── data/                            # gitignored raw CSIRO + NEMOSIS cache; fetch scripts populate
├── scripts/
│   ├── fetch_csiro_nem_data.py
│   ├── record_asciinema_demo.sh
│   ├── tour_lib.sh
│   └── record_tour.sh
├── kube/
│   ├── llamacpp-phi-pod.yaml
│   ├── powermcp-pandapower-pod.yaml
│   ├── benchmark-runner-job.yaml
│   └── villasnode-tap-pod.yaml
├── labs/
│   ├── 01-simple-loadflow-fit/
│   ├── 02-medium-interconnection-screening/
│   ├── 03-advanced-provider-bakeoff/
│   ├── 04-aemo-digital-twin-reconciliation/
│   ├── 05-spartan-chaosnet-transient-stream/
│   └── 06-sysml-digital-thread/
└── benchmarks/
    └── power-agent-bench-lite/
```

## 7. The six labs

Each lab folder will contain: a numbered `README.md` (do this → run this → you should see this →
here's why an AEMO modeller cares — plus, per every lab's README, a "presenter/backup script"
section detailed enough to talk through even if the live run isn't available on the day), one
Python entry point runnable via `uv run`, and a fixed expected-output fixture so the lab is
self-checking, not vibes-checking.

### Lab 1 — Simple: load-flow model fitting (single agent, single tool, no orchestration framework)

- **Setup**: `snemSA.m` (South Australia only — small enough to run in seconds) loaded via
  `powerio` into pandapower.
- **Task given to the agent**: "Bus 14's voltage is reported at 0.968 pu in the field SCADA
  snapshot; the base case model gives 0.951 pu. Adjust the shunt/load scaling parameter within
  ±10% until the modelled voltage matches the field reading to within 0.002 pu." — this is the
  "model fitting" regression task in miniature: a single scalar parameter search against a
  deterministic physics re-check (a plain least-squares or bisection loop the agent calls as a
  tool, not something it eyeballs).
- **What it demonstrates**: an agent calling one MCP tool (`pandapower.runpp`) repeatedly inside a
  fit loop, with the local Phi-4-mini model only deciding *which parameter to try next* — the
  actual physics stays in pandapower, never in the LLM. This is the load-bearing point for the
  whole repo: the LLM proposes, pandapower disposes.
- **Definition of pass**: script prints the fitted parameter, iteration count, and final residual;
  compares against `expected_results.json` fixture within tolerance.

### Lab 2 — Medium: interconnection / asset-provisioning screening (Agent Framework Sequential + Concurrent workflow)

- **Setup**: `snem1803.m` (mainland), a hypothetical new 250 MW generator connection at a named
  bus.
- **Workflow** (mirrors PowerWF's "Grid Impact Evaluation" example, reimplemented on Agent
  Framework instead of LangGraph):
  1. *Sequential* step — load case, attach candidate generator, solve base-case AC power flow.
  2. *Concurrent* step — fan out an N-1 contingency screen (drop each of the ~20 lines local to
     the connection point) as parallel tool calls; each branch's result feeds back into one
     collector step.
  3. Sequential step — thermal/voltage-limit check against AEMO-style planning criteria
     (documented, not invented — pull the actual NEM planning voltage bands into the check),
     produce pass/fail per contingency.
  4. Final step — agent drafts a plain-English screening memo citing which contingencies (if any)
     breach limits; a human-in-the-loop checkpoint (Agent Framework's native support for this)
     gates before the memo is "finalised".
- **What it demonstrates**: the exact "deterministic scripting of workflow automation of steps
  necessary to provision an asset or study" line from the brief — every step is a script, the
  agent's job is sequencing and reporting, not computing. This is also the first place
  `podman kube play`'s pod-per-tool-version pattern earns its keep — the pandapower MCP server pod
  can be pinned to an exact version for the entire screening run, then torn down.

### Lab 3 — Advanced: multi-provider bake-off + multi-agent orchestration (Agent Framework Magentic/group-chat, Podman scale-out)

- **Setup**: same mainland case, but now the *goal* is a benchmark, not a single answer: run the
  Lab 1 style regression/model-fitting task (and 2–3 more task families borrowed from the
  PowerAgentBench-SS pattern — e.g. fit a line rating to match a known thermal trip, fit a
  generator droop constant to match a frequency-response trace) against **several local model
  providers** swapped through the same llama.cpp pod: Phi-4-mini-instruct, Gemma-4, Llama-3.2-3B
  — same tasks, same tolerance, same deterministic scorer, only the model file changes.
- **Orchestration**: Agent Framework's Magentic/group-chat pattern runs a small "manager +
  worker" pair per provider so the runs are directly comparable (identical prompts, tool set,
  and evaluator across providers); `podman kube play --replace` on the llama.cpp pod is what swaps
  the model between sweeps, and the benchmark runner itself ships as a Kubernetes `Job` manifest
  (`kube/benchmark-runner-job.yaml`) so the whole matrix (3 providers × N task families) can be
  farmed out as parallel Job pods instead of a serial for-loop — this is the concrete "here's where
  you'd actually reach for an orchestration platform" moment for the Operations audience.
- **PowerFM baseline (the fourth row, no LLM involved)**: alongside the LLM-agent providers,
  pull one pretrained [PowerFM](https://github.com/Power-Agent/PowerFM) **OpenPowerBench**
  load-forecasting checkpoint (topology-independent task family — a short-horizon regional demand
  forecast) from Hugging Face Hub, run it directly against a CSIRO NEM regional load trace, and
  score it on the identical held-out-window/error-tolerance metric used for the LLM-driven
  parameter-fit tasks. It is not asked to do the model-fitting task families (that would compare
  unlike things) — it establishes the "domain-trained foundation model, no agent loop, no tool
  calls" reference point the paper itself distinguishes from the agentic path, in the same
  scorecard, so the audience sees the Foundation-Model and Agent+MCP+Workflow pillars from §1
  side by side rather than as competing claims.
- **Output**: a single scorecard (JSON + printed table) — provider (Phi-4-mini / Gemma-4 /
  Llama-3.2-3B / PowerFM-OpenPowerBench), task family, pass/fail, error margin, wall-clock,
  tokens (n/a for the PowerFM row) — checked into `benchmarks/power-agent-bench-lite/results/` so
  the demo is re-runnable and diffable, not a screenshot.

#### Lab 3 — Super-Stretch Goal (explicitly out of the v1 Definition of Done)

Everything below is aspirational — it is what Lab 3 grows into *if* the core version above lands
cleanly and there's appetite (and hardware) to keep going. None of it is required to call the lab
"done"; it's written down now so a later push doesn't have to re-derive the shape from scratch.
Treat every subsection as independently optional — cut whichever ones don't fit the time or
hardware available on the day.

1. **Full case, full task suite, real parallelism.** Swap `snem1803.m` for the full `snem2000.m`
   (all NEM states + Tasmania), and swap the "2–3 borrowed task families" for genuine coverage of
   both [PowerAgentBench-SS](https://arxiv.org/pdf/2606.18789) (steady-state: power flow, OPF,
   contingency, thermal/voltage) *and* [PowerAgentBench-Dyn](https://arxiv.org/abs/2606.20401)
   (dynamic: model-quality review, security-risk screening) task families — not the "lite" scorer,
   the real breadth. At that scale the provider × task-family matrix stops being a for-loop you'd
   ever run serially and becomes the thing that actually justifies `kube/benchmark-runner-job.yaml`
   fanning out across a real multi-node cluster rather than a single Podman host — the honest
   answer to "when do you actually need Kubernetes, not just `podman kube play`" is *here*, once
   the matrix is large enough that a single machine's core count is the bottleneck.
2. **Wider, honestly-labelled provider matrix.** Add a larger local model if the demo hardware has
   the RAM for it (e.g. a quantized 8B–14B model) purely to show the latency/accuracy trade-off
   against Phi-4-mini's 3.8B — same tasks, same tolerance, one more row. Optionally add a single
   cloud frontier model as a **calibration reference only**: off by default, requires an explicit
   opt-in flag and the operator's own API key, clearly labelled "not on the golden path, shown only
   to calibrate how far local inference is from a state-of-the-art baseline." This is the one place
   in the whole repo a cloud key is even discussed, and it stays optional and clearly fenced off —
   the "no cloud LLM keys" rule (`docs/DEFINITION_OF_DONE.md`) governs everything else.
3. **PowerFM, plural.** The core lab uses one OpenPowerBench checkpoint for one task family
   (load forecasting). The stretch version adds a **GridFM** (GNN, trained on grid topology rather
   than text) baseline for the topology-dependent task families, and a **GridLDM** (diffusion)
   baseline for a time-series *generation* task (e.g. synthesising a plausible EV-charging load
   profile for a bus that has none) — each PowerFM model scored only on the task family it's
   actually suited for, so the scorecard reads as "best tool for the job" across foundation-model
   architectures, not a single forecasting number standing in for the whole PowerFM project.
4. **Chaos/resilience sweep.** A small "chaos" Job pod that, between benchmark runs, randomly
   escalates the contingency severity fed to each provider (N-1 → N-2 → a randomly perturbed load
   profile within realistic bounds) and tracks the model-size/provider accuracy curve as stress
   increases — a direct, hands-on echo of the cited paper's own framing ("a rising frequency of
   extreme weather events" straining "traditional control paradigms"): does a smaller local model's
   advice degrade gracefully or fall off a cliff as the scenario gets harder, and does that curve
   look different for the PowerFM baselines than for the LLM-agent providers?
5. **Live dashboard, not a post-hoc table.** Move the scorecard from "printed after the sweep
   finishes" to a live Gradio page that updates as each Job pod completes (Gradio's generator-based
   streaming updates make this a small addition, not a rewrite) — for a room full of people watching
   a demo, a leaderboard filling in row by row is a materially different experience than a table
   dropped at the end.
6. **Flint-authored dashboard, on request.** Wire the optional [Flint](https://github.com/microsoft/flint-chart)
   MCP server (`npx flint-chart-mcp`, Node-gated, static-chart fallback if Node isn't present — see
   the visualization discussion) as one more tool call the *orchestrating agent itself* can make:
   once the (already-computed, already-correct) scorecard data exists, let the agent decide how to
   chart it and render that live in the Gradio page next to the pre-built version. The data is
   fixed either way — only the presentation is agent-authored — which is why this is safe to hand
   to the LLM even though nothing else in this repo lets it touch anything load-bearing.
7. **Human row.** Add a `gr.Button("I'll try it myself")` to the Gradio dashboard that lets someone
   in the room attempt one of the Lab 1-style parameter-fit tasks by hand through the same UI,
   timed and scored on the identical metric, and appended to the leaderboard live next to the AI
   providers. Cheap to build, and it's the single most effective way to make a benchmark number
   mean something to a non-engineer in the room — they just watched themselves lose (or win) to
   Phi-4-mini on a task they now understand because they just did it.

### Lab 4 — Real AEMO Data: Digital-Twin Reconciliation & Constraint Literacy

Full concept, library choices, and caveats: [`docs/LAB4_AEMO_REAL_DATA.md`](LAB4_AEMO_REAL_DATA.md).
In one line: Labs 1–3 are entirely synthetic; Lab 4 pulls real historical NEM dispatch data via
[NEMOSIS](https://github.com/UNSW-CEEM/NEMOSIS), maps real DUIDs onto the CSIRO synthetic
generators (using the `mms-guide`'s documented `PARTICIPANTID → STATIONID → DUID` join pattern,
with [`akxen/egrimod-nem`](https://github.com/akxen/egrimod-nem) as prior art for the collation
step), imposes real dispatch on the synthetic network, and scores how well the modelled power
flow reconciles against AEMO's actually-reported outcome — plus a constraint-equation-literacy
exercise built on the public [`susantoj/NEM_constraints`](https://github.com/susantoj/NEM_constraints)
library rather than a hand-rolled parser. It carries two non-negotiable caveats (not a digital
twin of the real network; not a fault-reproduction claim for its optional 2016 SA Black System
case study) that must appear in the lab's own README, not only in the design doc.

### Lab 5 — SPARTAN Chaos-Net: Transient Streams via DPsim + VILLASnode

Full concept, architecture, and Definition of Done: [`docs/LAB5_SPARTAN_CHAOSNET.md`](LAB5_SPARTAN_CHAOSNET.md).
In one line: Labs 1–4 all operate at the dispatch-interval timescale; Lab 5 operates at the
waveform timescale — procedurally generated "chaos-net" grid topologies (SimBench seed grids +
NetworkX perturbation), a network-wide EMT-domain transient solve in
[DPsim](https://github.com/sogno-platform/dpsim) at a 4kHz-class timestep with a scheduled,
counting-down sequence of faults and switching events, streamed per-substation via
[VILLASnode](https://github.com/VILLASframework/node)'s native IEC 61850 Sampled Values node-type
toward SPARTAN, an external edge PMU anomaly-detection project. Its Definition of Done is split:
a laptop-portable core (no physical hardware required, a stub stands in for SPARTAN) is required;
validation against the actual Radxa Dragon Q8B hardware is an optional, separately-gated
extension. It does not implement SPARTAN's anomaly-detection logic (a subsequent phase) and does
not claim any generated topology represents a real substation network — both caveats must appear
in the lab's own README, not only in the design doc.

### Lab 6 — SysML v2 Digital-Thread MVP (MBSE tooling evaluation)

Full concept, tool-evaluation findings, and Definition of Done:
[`docs/prd/0006-sysml-digital-thread-mvp.md`](prd/0006-sysml-digital-thread-mvp.md). Unlike Labs
1–5, this lab isn't a physics/dispatch scenario — it's a greenfield evaluation of whether SysML v2
(a formal systems-modelling notation) is viable tooling for two emerging business cases: AI
agent/MCP/data workflow modelling, and grid network topology modelling. One LinkML→`.sysml`
generator/syntax-gate/isometric-diagram-renderer pipeline runs against three tracks — Track A (this
repo's own real Agent/MCPServer/DataSource inventory), Track B (a real bus/generator/line cluster
pulled from `data/snemSA.m`), and Track C (PRD-0005's own real pipeline-phase sequence) — proving
the same code generalizes across all three. The normative SysML v2 Pilot Implementation (syntax
checking) was genuinely attempted first, timeboxed, and precisely root-caused where it didn't land;
it fell back to a small, named in-repo stand-in. The diagram renderer is a pure, deterministic,
in-repo isometric SVG writer. Those findings are themselves the sprint's deliverable, not just the
working pipeline.

## 8. Rust component

We are **not** writing a new Rust crate. `powerio` already does exactly what's needed — parses
CSIRO's native MATPOWER `.m` files, converts formats, and its own test suite already benchmarks
against pandapower — so the Rust requirement in the brief is satisfied by depending on it (via its
Python bindings, installed like any other `uv add`-able package once we confirm its packaging
story — source build via `maturin`/`cargo` if it isn't on PyPI yet). If, once we're hands-on with
the crate, its `.m` coverage doesn't handle every CSIRO file variant (the ACDC/TNEP variants push
into MATPOWER extension fields), the fallback is pandapower's own `from_ppc`/`from_mpc`
converters — but leading with `powerio` is the more interesting demo and the one the brief asked
for by name.

## 9. Why `podman kube play` / why Kubernetes-style thinking at all

Deliberately explained, not sold, because the board audience includes Operations and System
Design, who will (rightly) ask "why not just a shell script":

- **Version pinning as an audit artifact.** A planning submission that says "screened against
  pandapower 2.14.x, running in a container built from this exact Containerfile" is a
  reproducibility statement System Design can defend later. A shell script with `pip install -U`
  is not.
- **Parallel fan-out for contingency/bake-off sweeps.** Lab 2's N-1 screen and Lab 3's provider
  bake-off are both embarrassingly parallel. `podman kube play` applied to a `Job` manifest is the
  smallest possible step towards "now run this across a real cluster when the case is the full
  5000-bus NEM model instead of the 1803-bus mainland subset," without rewriting anything — the
  YAML is the same shape whether Podman or a real kubelet is behind it.
- **Where it stops being worth it**: for a single interactive fit (Lab 1), a container is pure
  overhead — that lab intentionally runs as a plain `uv run` process talking to one already-running
  pod, to make the contrast visible rather than containerising everything by reflex.
- **Not chosen**: a real Kubernetes cluster, Helm, or any managed k8s service — out of scope
  entirely; `podman kube play` gives us the manifest shape and the "one command, no cluster" story
  that fits a laptop demo.

## 10. `install.sh` — spec

One command, POSIX shell, checks-then-acts (never silently reinstalls something already present):

1. Check for `uv`; if absent, install via the official installer script (documented, not
   auto-piped-without-telling-the-user).
2. Check for `podman`; if absent, print the distro-specific install instructions and stop (we do
   not attempt to install a container runtime with root for the user).
3. Check for `cargo`/`rustc` only if `powerio` needs a source build on this platform; skip if a
   prebuilt wheel is available.
4. `uv sync` — installs pandapower, the `powerio` binding, `agent-framework`, `mcp`, `powermcp`,
   pytest, from a single `pyproject.toml`/`uv.lock`.
5. `scripts/fetch_csiro_nem_data.py` — downloads the CC-BY dataset into `data/` (idempotent,
   checksums the download).
6. `podman kube play kube/llamacpp-phi-pod.yaml` then `kube/powermcp-pandapower-pod.yaml` —
   downloads the Phi-4-mini GGUF on first run (documented size/time), starts both pods.
7. Best-effort install of the display/demo tools `mpv` + `chafa` via a direct `sudo apt-get`
   (checks-then-acts) -- the viewers behind the root Justfile's `watch`/`peek` recipes that show
   animations and charts over SSH with zero file transfer. This step is *not* a gate: the labs
   run headless, and on hosts where non-interactive sudo isn't available it warns and continues.
   The canonical per-command path is the committed authorized-script boundary
   (`scripts/deploy_demo_tools.sh`, the only NOPASSWD root surface, granted by
   `scripts/sudoers.d/nem-poweragent-lab` via `just authorize`) -- `just deploy` runs it as root
   with no password anywhere, and the `just` workflow never hard-stops (a missing rule prints the
   exact `just authorize` step).
8. Smoke test: one `uv run` call that does a trivial power flow through the running pods (and
   reports the display tools' presence, informational only) and prints PASS/FAIL -- this is the
   "did the install actually work" gate before anyone opens a lab.

## 11. Recorded demos: install walkthrough + per-lab tours

Two recording mechanisms, both wrapping real `asciinema rec` sessions around real commands rather
than a hand-edited video — the recording is an artifact of this repo, regenerable on demand, so a
walkthrough can't silently drift out of sync with the actual code the way a slide deck does:

- `scripts/record_asciinema_demo.sh` wraps `./install.sh` → Lab 1 → Lab 2 → Lab 3 summary table,
  with `PS1` and terminal width pinned so the `.cast` plays back consistently regardless of the
  presenter's own shell config. The original, narrower walkthrough.
- `scripts/record_tour.sh` covers every lab (1-9): `labs/0N-.../tour.sh` narrates each lab's real
  `check-labN` gate (via `scripts/tour_lib.sh`'s `narrate`/`run_cmd` helpers — asciinema records
  real terminal stdout, not a script's `#` comments, so the story has to be spoken on purpose), then
  renders the `.cast` to a committed `tour.gif` (primary — renders inline on GitHub, zero clicks)
  and `tour.mp4` (secondary, higher quality) via `agg`/`ffmpeg`. Each lab's own README embeds its
  GIF under `## See it run`. The raw `.cast` stays gitignored/regenerate-on-demand like the original
  script's; only the rendered GIF/MP4 are the deliberate exception, committed the same way this
  repo already commits final rendered PNG charts while keeping their generation pipelines
  regenerable. Memoized via the `labs/tour.just` module (`just tour::tour[-record|-render]`).

## 12. Explicit non-goals

- No `b00t` dependency, anywhere.
- No commercial engines (PSS/E, PowerFactory, PowerWorld, PSCAD) required for any of the 5 labs —
  PowerMCP's servers for those tools exist and could be added later as *optional* extras, never on
  the golden path.
- No cloud LLM API keys — every model call in every lab goes to `localhost` only; this is a network
  policy worth stating explicitly to Operations, not just an implementation detail.
- No new Rust code (see §8) — we consume `powerio`, we don't fork it.
- Not a sales artifact — no ROI slide, no competitor comparison; the labs either reproduce the
  claimed behaviour on the reader's own machine or they don't.

## 13. Known gaps / backlog

Everything above is the plan as designed. This section is the honest gap between that plan and
what auditing the actually-committed code found, once all 5 labs were already marked
"implemented." Detail lives in `docs/backlog/`, indexed at
[`docs/backlog/README.md`](backlog/README.md); this section is the pointer, kept current as items
open/close.

**PowerCoder (2026-10-23):** items 0008–0014 come from an independent merit review of the r6
agent study. The first is 0008: the current benchmark cannot measure agent value over a scripted
policy, so any r6 gain has to be read as "a LoRA learned the tool protocol". 0009 records a
recovery-training defect that is fixed. 0010–0013 are provenance, strict-arm, answer-by-reference
and numerics-tool work, and 0014 is the grammar-engine decision. The forward plan is
[PRD-0010](prd/0010-powercoder-physics-grounded-engineering-agent.md) (gates G0–G8: 0008 is G0, and
0015–0022 are G1–G8).

**Original state (as of the audit that opened these items): visualization was essentially absent.**
`matplotlib` was a hard dependency imported in exactly one of roughly fifteen lab/script files
(`labs/05-spartan-chaosnet-transient-stream/verify_stream.py`, one fault-transient waveform plot).
No lab called `pandapower.plotting`. Lab 5 built and perturbed a real `networkx` graph every run and
never drew it. Lab 3's provider-bake-off scorecard — a dataset whose entire purpose is comparison —
was JSON-only. Lab 2's contingency screen and Lab 4's reconciliation were both printed-text/memo
only. None of this was a deliberate non-goal (contrast with §12 above); it was an oversight, tracked
so it doesn't get silently mistaken for "done" the way `AGENTS.md`'s own self-checking convention
exists to prevent for correctness, not just for visuals.

**Current state: Lab 1, Lab 2, Lab 3, Lab 4, and Lab 5's free-tier fixes (0003, 0004) are done.**
`pandapower.plotting` is no longer unused: Lab 1's `run.py` now renders a committed
`sample_network_chart.png` — the fitted network's buses colored by real solved voltage (`vm_pu`)
via `pandapower.plotting.create_bus_collection`/`create_line_collection`, TARGET_BUS highlighted —
gated by `refresh_chart` the same way (`--step check` never overwrites it), and asserted to exist
by both `--step check` and a dedicated `test_lab1.py` test. (No real geodata exists for `snemSA.m`,
and pandapower's own `create_generic_coordinates` needs `igraph` or `pygraphviz`, neither installed;
Lab 1's chart instead builds a `networkx.spring_layout` and writes it to `net.bus["geo"]` via
pandapower's own re-exported `geojson` module — no new dependency.) Lab 5's
`generate_topology.py` now draws the generated chaos-net topology to a committed
`sample_topology_plot.png` (tap-point buses highlighted/labelled), gated the same way its JSON
fixtures are so ad-hoc seeds never touch the committed artifact. Lab 4's `reconcile.py` now renders
a grouped bar chart (`sample_reconciliation_chart.png`) of modelled-vs-actual interconnector flow
and synthetic-vs-actual network losses — the same two numbers the printed reconciliation memo
already discusses — gated on `date == LAB4_DATE` so the optional Part C (2016 SA Black System) run
never overwrites it. Lab 3's `orchestrator.py` now renders a 3-series grouped bar chart
(`scorecard_chart.png`, regenerated gitignored output alongside `scorecard.json`) of error margin by
task family across
its three local-policy providers, with the PowerFM baseline row deliberately excluded (no shared
units/task_family to compare against) and that exclusion noted both in-image and in the README. Lab
2's `workflow.py` now renders `sample_contingency_chart.png` (two panels: worst line-loading % vs
the 100% thermal limit, and worst bus-voltage pu vs the 0.90–1.10 pu band) from the same per-
contingency numbers `check_limits()` already evaluates, regenerated deterministically by
`--step check-limits`. All
four are covered by their lab's `--step check` + `test_labN.py` per AGENTS.md's self-checking
convention, not just printed (Lab 3's chart specifically via
`test_lab3.py::test_lab3_report_renders_scorecard_chart` exercising `report_step()` directly —
`check_step()` never touches the uncommitted file; Lab 2's chart is asserted to exist by
`--step check` while `--step check-limits` regenerates it). Lab 5's stretch-tier symbolic
single-line rendering (gated on a CIM/CGMES export step Lab 5 doesn't have yet) remains open — see
`docs/backlog/README.md` for the current per-item status.

- [`docs/backlog/0001-topology-and-results-visualization-gap.md`](backlog/0001-topology-and-results-visualization-gap.md)
  — **Done.** The gap itself, with the grep evidence above expanded per-lab; every item closed,
  including item 2's `pandapower.plotting` miss (Lab 1, above).
- [`docs/backlog/0002-pandapower-diagram-and-symbolic-representation-options.md`](backlog/0002-pandapower-diagram-and-symbolic-representation-options.md)
  — what `pandapower.plotting` already offers beyond `simple_plot()`, and a survey of open-source
  symbolic/single-line-diagram options outside pandapower (`powsybl-diagram`/`pypowsybl`,
  `GridCal`, `Grid2Op.PlotGrid`, and others), tiered by whether they need a new dependency.
- [`docs/backlog/0003-lab3-scorecard-visualization.md`](backlog/0003-lab3-scorecard-visualization.md)
  — **Done.** Lab 3's bar chart rendering of its scorecard (both regenerated gitignored output —
  the committed fixture is `expected_scorecard.json`), zero new dependencies.
- [`docs/backlog/0004-lab4-lab5-visualization-options.md`](backlog/0004-lab4-lab5-visualization-options.md)
  — **Lab 4's reconciliation chart: done.** **Lab 5's topology drawing: done.** Lab 5's stretch tier
  (real IEC-style symbolic single-line rendering) remains open, gated on a CIM/CGMES export step
  Lab 5 doesn't have yet.
- [`docs/backlog/0005-unified-notebook-playbook.md`](backlog/0005-unified-notebook-playbook.md) —
  **Done.** `notebooks/lab_playbook.py`, a jupytext `percent`-format notebook (`just playbook`) that
  narrates all 5 labs' charts in one linear document *without* becoming a second, ad hoc source of
  truth alongside the existing `--step check` proof scripts (`AGENTS.md`'s "the proof scripts are the
  proof, not a transcript" applies to this exactly as much as to anything else in the repo) — every
  section shells out to that lab's real `--step check`, asserts PASS, then only renders that lab's
  already-committed fixture/chart.
- [`docs/backlog/0006-lab5-advanced-transient-visualization-techniques.md`](backlog/0006-lab5-advanced-transient-visualization-techniques.md)
  — next-tier Lab 5 visualizations beyond its original six views. **Done, all four options.**
  Symmetrical components (V0/V1/V2, not just |V1|, `view_telemetry_rates.py`/
  `animate_telemetry_rates.py`) and a spectrogram (`view_spectrogram.py`), both reusing data already
  captured with no new dependency or `run_dpsim.py` change; an R-X impedance-trajectory / mho-circle
  distance-relay view (`view_rx_trajectory.py`), which needed one small `run_dpsim.py` capture
  addition — current on the fault-adjacent `PiLine` (`i_intf`) alongside the already-captured
  fault-bus voltage; and a network-wide sag-propagation animation (`animate_sag_propagation.py`),
  which extended `run_dpsim.py` to capture every bus's voltage (`bus_voltages`, all 14 buses of this
  lab's real topology — the "N buses" scale concern this doc's own backlog file originally raised
  turned out to be small in practice, ~2.6 MB/~25s render, not a real cost at this lab's scale) and
  animated it onto `generate_topology.py`'s own topology layout, connecting the topology and
  transient artifacts into one. Each of the four implementations surfaced a real, measured finding
  that corrected this doc's own original prediction (see the backlog file's Option 1/2/4 sections)
  rather than asserting the textbook shape assumed in advance — option 4's real run showed the sag
  propagates almost network-wide (not sharply localized to the fault bus) because this topology's
  line impedances are tiny relative to load impedance, the same root cause option 2's own R-X
  finding already surfaced.

None of these are part of any lab's current Definition of Done (`docs/DEFINITION_OF_DONE.md` is
unchanged by this section) — they're recorded so the gap is a tracked decision, not a silent one.

## 14. Forward-looking capability PRDs

Where §13 records gaps found by auditing already-committed code, [`docs/prd/`](prd/README.md) is
the forward-looking counterpart: build plans for capability this repo doesn't have yet, broken into
composable units with acceptance criteria, written after a 2026-08-03 review of AEMO's 2026 GPSRR
report against this repo's existing labs. Three items so far:

- [`docs/prd/0001-composable-generator-detector-platform.md`](prd/0001-composable-generator-detector-platform.md)
  — a `Generator`/`Detector` platform generalizing Lab 5's one-fault `chaos_schedule.yaml` into a
  causally-linked event timeline, plus a scoring harness. This is, concretely, the "subsequent
  phase" `docs/LAB5_SPARTAN_CHAOSNET.md`'s own Definition of Done already named and deferred
  (SPARTAN-style anomaly-detection logic), given a build plan decoupled from actual SPARTAN
  hardware.
- [`docs/prd/0002-sa-2016-black-system-cascade-scenario.md`](prd/0002-sa-2016-black-system-cascade-scenario.md)
  — closes the half of Lab 4 Part C's own stated caveat ("not a claim of reproducing the 2016 SA
  Black System event's actual root cause") that Lab 4's dispatch-reconciliation approach was never
  positioned to attempt: an actual cascading-fault causal-chain reproduction, built on the 0001
  platform, scored against AEMO's own published incident figures.
- [`docs/prd/0003-iberian-2025-blackout-scenario.md`](prd/0003-iberian-2025-blackout-scenario.md) —
  a new scenario for the 28 April 2025 Iberian Peninsula blackout (the reference international
  incident the 2026 GPSRR itself cites for voltage-control risk), grounded directly in ENTSO-E's own
  472-page Final Report's root-cause tree and second-by-second timeline, deliberately complementary
  to 0002 (a control/oscillation-driven cascade with no initiating network fault, vs. 0002's
  weather/network-fault-driven one).

Same discipline as §13: recorded so the direction is a tracked decision, not a silent one, and none
of it is part of any lab's current Definition of Done until a PRD is actually implemented and that
document is updated to say so.
