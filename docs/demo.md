# Demo runbook

A 15-minute live demo. Everything here was run on real hardware; the timings are measurements, not estimates, and where one has not been measured it says so.

## Before you start (once)

1. **The images must be public.** In GitHub, for each of the four packages `wattson`, `wattson-harness`, `wattson-hf` and `wattson-csiro` (Organization,
   then Packages, then the package, then Package settings, then Danger Zone, then Change visibility, then Public). If Public is greyed out, allow
   it first under Organization settings, then Packages. Check, from any machine with no login:
   ```bash
   podman pull ghcr.io/fungible-farm/wattson:latest        # must not ask for credentials
   ```
2. **Podman 5 or newer and `just`** on the demo machine (`podman --version`, `just --version`).
3. **Warm the model cache the night before.** The first `just up` downloads 17 GB (Granite) or 8.5 GB (Qwen) from Hugging Face. Run it once, then `just down`;
   the `wattson-models` volume keeps the weights, so the demo's `just up` takes about 3 minutes, not 10.
4. **On a unified-memory machine (NVIDIA GB10) or one that already uses port 8000**, set these once in your shell:
   ```bash
   export WATTSON_GPU_UTIL=0.26   # Granite on a 128 GB unified-memory GPU; use 0.15 for qwen. On a 24 GB card leave the default.
   export WATTSON_PORT=8100       # any free host port
   ```

If GHCR is not available on the day, build everything locally instead (about 10 minutes the first time, cached after) and use `localhost` images:
```bash
just build
export WATTSON_REGISTRY=localhost WATTSON_TAG=local
```

## The demo, in order

| Step | Command | What to say | Time |
|---|---|---|---|
| 1 | `just labs` | Three deterministic labs run in a container and match their committed results. No GPU, no model. | 15 s |
| 2 | `just up granite` | One pod, four containers, each with one job: a model puller (Hugging Face), a case-data fetcher (CSIRO), the model server, the harness. | about 3 min (model cached) |
| 3 | `just doctor` | Every check, including one real completion from the model. | about 25 s |
| 4 | `just ask "<the Lab 1 question>"` | The model writes a Python program; the solver does the arithmetic; the answer is graded by re-solving. | about 30 s |
| 5 | `just lab1-agent` | Lab 1's task given to the model, printed next to the lab's own bisection result. | not yet measured |
| 6 | `just eval smoke --enforce` | 20 tasks graded by re-solving, with the guard on. | about 4 min |
| 7 | `just pi` | The pi coding agent wired to the model and its tools. Interactive. | open-ended |
| 8 | `just down` | Stop; the model stays cached. | 1 s |

`just demo` runs steps 1 to 6 in order, with a banner before each. It does not run `pi`.

The question for step 4 (from the shipped smoke suite; the reference answer is `s = 1.0625`, and any `s` within the tolerance is correct):
> Using the power flow solver, find the scale factor s in [0.9, 1.1] applied to the active-power dispatch of generator 2 on case 'snem-sa-2000' such that the loading of transformer 4 equals 0.184093 percent (within 0.0006941900815668939 percent). Return only the scale factor.

## What you can claim, and what you cannot

You can say, because it was measured:
- The trained students write the computation as Python and report what the program printed. Granite scored 37 of 40 on a suite built so that adding in your head gets it wrong, and wrote no untraced number in 40 attempts. The untrained model wrote one in 13 of 37 answers.
- Every answer is graded by re-solving the network, never by comparing text.
- With `--enforce`, an answer containing a number no tool produced is refused. In the measured runs it turned 13 of 37 such answers into 0 for the untrained model.

Do **not** say:
- "The model never does arithmetic." The guard polices what is *emitted*. It cannot see a model's private reasoning, and thinking models write some numbers there. The accurate claim is a trained, measured behaviour plus an enforced guarantee on answers.
- That the Python shell is secure. It is a restricted interpreter, not a security boundary.
- That `pi` is equivalent to the harness. With its defaults, these models invent commands and numbers; `just pi` applies the configuration that avoids that, and it has been checked on one task and one model.
- That any result generalises beyond AC power flow on the networks shown.

## If something goes wrong

| Symptom | Fix |
|---|---|
| `just up` seems stuck | The first run downloads the model. `just logs pull-model` shows progress. |
| `address already in use` | Another service owns the port. `export WATTSON_PORT=8100`. |
| The server exits at start | `just logs vllm`. On a unified-memory GPU lower `WATTSON_GPU_UTIL`. |
| `pull` asks for a login | The package is still private (step 1 above), or use the local build. |
| Anything else | `just doctor` says what is wrong and how to fix it; [troubleshooting](troubleshooting.md) lists every failure we hit. |
