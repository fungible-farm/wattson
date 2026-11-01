# Lab 3 (Advanced) — Multi-Provider Bake-off

"Which model/provider is actually good enough for this class of task, and how would you know?"
This lab answers that with a re-runnable, diffable scorecard instead of an anecdote: the same
tasks, tolerances, and scorer run against every provider, so the comparison is apples-to-apples.

**Read this before trusting the "provider" column**: the three "providers" here are not language
models — see Design notes below.

## See it run

![Lab 3 tour](tour.gif)

A narrated replay of `just check-lab3` — the real bake-off plus the pypowsybl cross-check, real
output. Higher-quality version: [tour.mp4](tour.mp4). Regenerate it yourself: `just tour::tour 3`
(live, unrecorded) or `just tour::tour-record 3` (re-record + re-render).

## What you'll do

1. Reuse `snem1803.m` and three parameter-fitting tasks in the shape of Lab 1's: `load-scale-fit`
   (a second bus, same mechanic as Lab 1), `line-rating-fit` (fit a line's thermal rating to a
   100%-of-nameplate trip point), and `gen-droop-fit` (fit a generator's dispatch to a target
   transformer-loading level — a steady-state stand-in for droop response, the way generators
   automatically adjust output in response to grid frequency).
2. Score three provider policies against those tasks, plus a fourth, non-agentic baseline row: a
   seasonal-persistence demand forecast.
3. Write one scorecard (JSON + table + a grouped bar chart) so the comparison can be read at a
   glance, not parsed out of a JSON array.

## Design notes

The original spec (`docs/VISION.md` §7) has three *live* local language models (served by
llama.cpp, orchestrated via a group-chat pattern) plus a real foundation-model forecasting
checkpoint pulled from Hugging Face. This build swaps in something lighter, named plainly rather
than disguised as a model comparison:

- **`local-policy-A`/`B`/`C`** are three different deterministic search algorithms (plain
  bisection, false-position/regula-falsi, and a seeded-noise perturbed bisection — see
  `orchestrator.py`'s docstrings), never given a model's name. The variation you'll see between
  them is real, reproducible variation between three *algorithms*, not three *language models*.
- **`PowerFM-OpenPowerBench-stub`** is a seasonal-persistence forecast against a synthetic demand
  trace (built from `snem1803.m`'s own load table plus a daily-shape multiplier), not a trained
  forecasting model. The scorecard slot and metric (MAPE over a held-out day) are real; the model
  behind it is not.
- The scoring architecture — run the same task/tolerance/scorer matrix across every provider, land
  every result in one scorecard, including the non-agentic baseline rather than treating it as a
  separate claim — is the actual point of this lab and is unaffected by which policies sit behind
  "provider."
- `kube/benchmark-runner-job.yaml` is a written Kubernetes Job manifest that runs this same matrix
  as parallel pods (`docs/VISION.md` §9); see its own header for what it needs to be run for real.

## Live LLM provider (M03 — a real local model, not a stand-in)

The deterministic policies above are the lab's *controls*. The mission's M03 step adds a genuinely
live local language model to the same task matrix, so the scorecard can distinguish a real model
from the deterministic controls and the non-LLM baseline:

- **`llm_provider.py` (`LLMProvider`)** implements the same propose-next-trial interface as
  `bisection_fit` — a drop-in `provider(evaluate, target, lo, hi, tol) -> FitResult`. Instead of a
  deterministic rule it asks a local LLM (served by llama-swap over OpenAI-compatible HTTP, default
  `qwen3.8-flash-next` at `http://127.0.0.1:8000`) to propose each trial value. The model sees the
  task spec, the current bracket endpoints with their residuals, and the running trial history; it
  infers the monotonic direction from the measurements (the prompt never asserts it) and proposes
  the next `s`. The harness evaluates every proposal with a real `pandapower.runpp()` — the solver,
  not the model, establishes the numerical truth.
- **No hard-coded model answer.** The prompt gives the model a *method* (the secant formula,
  direction-agnostic) plus the measurements; the model computes the actual value. No fitted value is
  baked into the prompt, and the deterministic policies are left untouched as the comparison
  baseline.
- **Sandbox stand-in, named:** the only substitution is the transport — the model is reached over
  HTTP (llama-swap) rather than an in-process llama.cpp call, because that is the API boundary the
  mission fixes. If a trial's response fails to parse as a number (the model rambled past the token
  cap), the harness falls back to the bracket midpoint (bisection's move) and flags the trial
  `adjusted=true` in the provenance — it never silently invents a value.
- **Scorecard distinction.** `--step sweep-llm` writes `scorecard.llm.json` +
  `scorecard.llm.provenance.json` (real prompt/completion token counts, model name, base URL, git
  commit, and per-trial raw response + reasoning). `--step report` merges the cached LLM rows into
  `scorecard.json` so the real model sits beside the deterministic controls; the grouped bar chart
  skips the LLM rows (non-deterministic, not part of the committed fixture), and `--step check`
  still diffs only the deterministic rows against `expected_scorecard.json`.

```
uv run benchmarks/lab03-advanced-provider-bakeoff/orchestrator.py --step sweep-llm
uv run benchmarks/lab03-advanced-provider-bakeoff/orchestrator.py --step sweep-llm --model qwen3.8-27b-uncensored
uv run benchmarks/lab03-advanced-provider-bakeoff/orchestrator.py --step report   # merges cached LLM rows
```

## pypowsybl spike (solver comparison, not a bake-off provider)

> Full write-up, conclusions, and implications:
> [`docs/POWERFLOW_ENGINE_SHOOTOUT.md`](../../docs/POWERFLOW_ENGINE_SHOOTOUT.md).

`spike_pypowsybl.py` answers a different question from the rest of this lab: not "which search
policy is best," but "is [PowSyBl](https://github.com/powsybl) (RTE's open-source power-system
modelling framework, via its real `pypowsybl` Python bindings) a viable alternative power-flow
*engine* for this repo's real NEM data at all." It is deliberately **not** wired into the
provider matrix above — this lab's "providers" are search policies, all running the same
underlying pandapower solver; a different solver is a different axis entirely.

**Real finding #1 — a genuine toolchain gap, not this repo's bug:** pypowsybl's MATPOWER
importer only accepts the binary MATLAB `.mat` serialization of a case, not the human-readable
`.m` script format every `.m` file in this repo (and everything MATPOWER itself ships) actually
is — confirmed against both `snem1803.m` and a hand-written textbook `case9.m`, both rejected by
`pypowsybl.network.is_loadable()`, and confirmed against
[PowSyBl's own MATPOWER import docs](https://powsybl.readthedocs.io/projects/powsybl-core/en/latest/grid_exchange_formats/matpower/),
which state the same requirement plainly. No MATLAB/Octave is installed in this environment to do
that conversion the documented way. **Fix used here:** round-trip the already-loaded pandapower
net through pandapower's own `to_mpc()` (a real `scipy.io.savemat`-backed MATPOWER `.mat` writer)
before handing it to pypowsybl — both solvers then run against the exact same in-memory network.

**Real finding #2 — the two engines agree, within a small, named, honestly-reported gap:** on the
real `snem1803.m` case (1803 buses, 2795 branches), pandapower and pypowsybl/OpenLoadFlow both
converge, total load matches exactly (identical input data), total generation agrees to
**0.005%**, and total system real-power losses agree to **0.17%** (1035.9 MW vs 1034.1 MW) — the
line/trafo loss *split* differs somewhat more than the total (886/150 MW vs 912/122 MW), most
likely a transformer tap-model convention difference between the two solvers' formulations, not a
solver defect; not investigated further than that within this spike's scope.
pypowsybl/OpenLoadFlow's own solve is faster (145ms vs pandapower's 2.3s on a cold/first call;
pandapower drops to ~47ms once its numba JIT is warm) — not a fair head-to-head timing claim
either way without controlling for both engines' warm-up cost, reported honestly rather than
picking whichever number favors one side.

**Verdict for this spike:** pypowsybl is a real, usable alternative load-flow engine for this
repo's NEM case data, gated on the `.m`→`.mat` round-trip step above.

**Update — promoted beyond this spike:** the `.mat` round-trip and bus-id-mapping machinery this
spike proved out moved into `src/wattson/gridfit.py` (`to_pypowsybl_network()`,
`pypowsybl_element_id_map()`) and now backs a real, wired-in capability —
[Lab 2's N-1 pypowsybl cross-check](../02-medium-interconnection-screening/README.md#pypowsybl-n-1-cross-check-a-real-second-opinion-not-another-stand-in),
a genuine second-engine validation of that lab's own contingency screen, not just a standalone
comparison. `docs/PSCADOSSE.md` records pypowsybl as an active MPL-2.0 dependency accordingly
(a second one alongside DPsim, not a documented single exception anymore).

```
uv run benchmarks/lab03-advanced-provider-bakeoff/spike_pypowsybl.py --step run
uv run benchmarks/lab03-advanced-provider-bakeoff/spike_pypowsybl.py --step check
```

## Command

```
uv run scripts/fetch_csiro_nem_data.py   # once, to populate data/snem1803.m
uv run benchmarks/lab03-advanced-provider-bakeoff/orchestrator.py --step sweep
uv run benchmarks/lab03-advanced-provider-bakeoff/orchestrator.py --step report
uv run python -m pytest benchmarks/lab03-advanced-provider-bakeoff/test_lab3.py
```

## Running in a container (Windows-friendly)

No local install needed — works identically under Docker Desktop, Podman Desktop, or native
podman/docker:

```
podman build -t nem-poweragent-base:local -f Containerfile.base .
podman build -t lab3:local -f benchmarks/lab03-advanced-provider-bakeoff/Containerfile .
podman run --rm lab3:local
```

This reproduces the `--step check` output below; override the step to run the full sweep:
`podman run --rm lab3:local --step sweep`.

There's a second, separate image at the repo root — `Containerfile.bakeoff`, built to
`power-agent-bench-lite:local` — the one `kube/benchmark-runner-job.yaml` actually runs (its own
header has the details). The one above is the lighter "just run the tutorial" path on the same
shared base every other lab uses; the two are deliberately kept separate.

## Step-by-step walkthrough

1. **`--step sweep`** — Rows arrive as each provider × task pair completes, e.g. `local-policy-A |
   load-scale-fit | PASS | err=0.0013 | 0.57s`, ending with the baseline row (`n/a
   tokens/latency-per-call` — it's a single forecast, not a search loop). No live run needed:
   `expected_scorecard.json` has one pre-run sweep committed.
2. **`--step report`** — The final scorecard (10 rows: 3 providers × 3 tasks + 1 baseline), written
   to `benchmarks/power-agent-bench-lite/results/scorecard.json` (gitignored, regenerated each run
   — the committed comparison point is `expected_scorecard.json`), plus a grouped bar chart
   (`scorecard_chart.png`, one group per task, one bar per provider; the baseline is excluded since
   it has no like-for-like comparison in the same units).
3. **`pytest test_lab3.py`** — `1 passed`: the full sweep re-run and diffed against
   `expected_scorecard.json` (wall-clock time excluded, since it's machine-dependent).
4. *(Stretch goal, not part of this walkthrough — see `docs/VISION.md` §7)*: the full 2000-bus
   case, real benchmark coverage, a live leaderboard, a resilience sweep.
