# Lab 10 — PowerAgentBench RestoreBench on local models

Every PowerCoder number so far comes from this repo's own benchmark, and the 2026-10-22 review found
defects in it (AGENTS.md FM-17–FM-19). This lab runs an outside benchmark on the same node:
**RestoreBench**, Level 3 of [PowerAgentBench](https://github.com/Power-Agent/PowerAgentBench). An
agent gets a Scenario Card for an AC power-flow case that doesn't converge. It has a budget of 10
reactive-control maneuvers (generator voltage setpoints, shunt steps, transformer taps) to restore
convergence, with solver feedback after each one. The scenarios were published frozen, each with a
known solving sequence (its *witness*), and RestoreBench's own scorer decides the result.

## What you'll do

```
just lab10-fetch        # clone PowerAgentBench at the pinned commit into data/, verify 11 pinned files
just check-lab10        # fast gate: pinned sources + the local transport's translation
just lab10-check-slow   # re-score all 92 witnesses in RestoreBench's environment (~5 min)
just lab10-agent http://127.0.0.1:8101 powercoder-qwen-student 5   # its agent on a served model
```

The step-by-step commands are `labs/10-poweragentbench-restorebench/restorebench_lab.py --step
{fetch,verify,witness,check,agent}`.

## The actual result (2026-10-22, GB10 / aarch64)

| Corpus | Scenarios | `restorebench-verify` | Witnesses scoring `SUCCESS` |
|---|---|---|---|
| IEEE 118-bus (`ieee118-reactive-deficit-v1`) | 46 | **FAILED**, 45/46 | 45 — S0081 scores `BUDGET_EXHAUSTED` |
| PEGASE 89-bus (`pegase89-reactive-deficit-v1`) | 46 | OK, 46/46 | 46 |

RestoreBench guarantees that every scenario is solvable. On this machine, with its own lockfile
(pandapower 3.4.0), one scenario's witness is not. S0081's witness is one maneuver (generator 1 set
to 0.988 pu). After that maneuver the scenario still fails to converge within the scorer's
`max_iteration=30`, and this was reproduced on repeated runs. The cause has not been established.
A near-singular case that is sensitive to aarch64 floating-point differences is the likeliest
explanation, but that is a hypothesis, not a measurement. The fixture `expected_witness_summary.json`
records the result as measured, so `--step check` fails if it changes. Reported upstream as
[PowerAgentBench#13](https://github.com/Power-Agent/PowerAgentBench/issues/13).

Model results from `--step agent` are written to `results/agent-<model>/summary.json`. The r6
PowerCoder driver will run all four models on the same corpus; see
`docs/POWERCODER_HANDOFF_2026-10-21.md`.

## Design notes

- **Only Level 3 is used, because only Level 3 is licensed.** PowerAgentBench has no licence at its
  root (GitHub reports `license: null`), so the shared library, Levels 1–2, the dynamic track and
  `cases/` have no stated terms. This is filed as
  [PowerAgentBench#11](https://github.com/Power-Agent/PowerAgentBench/issues/11). RestoreBench's
  code is MIT and its corpora are CC BY 4.0 (`NOTICE`, `dataset/LICENSE`). Under the CC BY 4.0
  attribution requirement, the corpora are by *The RestoreBench Authors*
  ([original repository](https://github.com/Mansutti081/RestoreBench)) and are used unchanged.
- **Fetched, not vendored.** As with the CSIRO case files, the checkout lives in the gitignored
  `data/`. `expected_sources.json` pins commit `b39c48b0b96c…` and the sha256 of the lockfile, the
  licences, both corpora's manifests and both witness files. A different checkout fails `fetch`.
- **RestoreBench runs in its own environment.** `uv sync --frozen` in its level_3 directory builds
  it from its lockfile, which pins pandapower 3.4.0; this repo uses 3.5.4. This lab only
  orchestrates RestoreBench (`uv run --project …`), so its published solver policies apply
  unchanged.
- **`witness_scorer.py` calls the scorer's Python API directly,** using RestoreBench's own
  `score_attempt`. The `restorebench-score --batch` command can't be used here, for two reasons: it
  has no data-directory option, so it always scores against IEEE 118, and it writes only an
  aggregate report. Run against the PEGASE 89 witnesses, it fails while loading the IEEE 118 files.
  Reported as [PowerAgentBench#14](https://github.com/Power-Agent/PowerAgentBench/issues/14).

## Sandbox notes (named stand-ins)

- **Local transport (`local_provider.py`).** RestoreBench reaches models only through Amazon
  Bedrock, the Anthropic API or the OpenAI Responses API. All three are cloud services that need API
  keys, which this repo's golden path excludes. `install()` replaces
  `restorebench.llm.providers._converse`, the Bedrock call that RestoreBench routes every other model
  id through, with a Chat Completions call to a local vLLM or llama-swap server. Model ids are
  `local/<served name>`. Each one is registered with a results-directory name and a zero token price
  (`models._MODEL_SLUGS`, `models.BEDROCK_PRICES`); the price is labelled "local inference: no
  per-token price". Everything else is RestoreBench's own: agents, prompts, the Converse-shaped
  messages, retries, truncation handling, the environment and the scorer. If you have a real
  RestoreBench provider, delete `install()` and pass its model id instead.
- **8,192-token responses.** RestoreBench asks for 16,384 output tokens. Its IEEE 118 first turn
  is already about 43k characters of card and tool schemas, so prompt plus 16,384 overran the
  local servers' 32,768-token context and every call was refused. Local calls use the PowerCoder
  evaluation's 8,192-token budget instead.
- **Greedy decoding.** RestoreBench's agents call at temperature 1.0, and its published campaigns
  run 3 repetitions. The local transport always sends `temperature=0` and runs 1 repetition, the
  same protocol as this repo's evaluations. That makes local results reproducible, but they are not
  directly comparable to the published success rates.
- **Configuration 2 only.** This is RestoreBench's single agent with physics tools, the closest of
  its three configurations to the PowerCoder agents. Configuration 1 has no tools, and configuration
  3 splits analyst and executor across separate calls.
