# Grid-study skill: Lab 1 replay with neo-coder (#157 slice A)

One Lab 1 task (fit the load-scale factor so bus 2008 of `snem-sa-2000` reads 0.9422 pu within 0.002) run through the harness with neo-coder on sm3lly via the gateway, then read against `skills/grid-study/SKILL.md`. Run 2026-11-16, one attempt, temperature as the harness sets it.

## How it ran

pi ran this command (the repo mounted read-only at `/repo` so the image's own `/app/.venv` stays visible; the Lab 1 case file comes from the existing `wattson-cases` volume; 8 GiB memory cap):

```
podman run --rm --userns=keep-id:uid=1000,gid=1000 --memory 8g --memory-swap 8g -e PYTHONPATH=/repo/src -e WATTSON_CASE_DIR=/cases \
  -v $PWD:/repo:ro,z -v wattson-cases:/cases:ro,z -v /tmp/sk/lab01-agent:/out:z -w /repo --entrypoint uv localhost/wattson-developer:port \
  run --frozen --no-sync --project /app python benchmarks/lab01-simple-loadflow-fit/agent_mode.py \
  --base-url http://192.168.1.137:8002 --model neo-coder --out /out
```

Two earlier attempts failed before any model call, both my invocation: the repo mount hid the image's environment, and the system `python3` has no `mcp`. The evidence is `benchmarks/lab01-simple-loadflow-fit/evidence/neo-coder-agent-journal.jsonl` (13 events) and `prompt.txt`.

## Result (harness output, verbatim)

```
bisection (committed)        |   0.925 |             0.941409 |    -0.000791 |            3
neo-coder                      |  0.9093 |             0.942200 |     0.000000 |           63
FAIL passed=False numerical=True tool_evidence=False provenance=True
```

## Read against the skill

- **Tools used.** Three `run_python` calls and nothing else, each building `solver.trial(network_id="snem-sa-2000", operation="load_scale", scale=s, observation="bus_voltage", observation_id=2008, max_iter=50)`. Every argument is valid by the skill's rules: the network id is the task's, the scale stays inside 0.9 to 1.1, `observation_id` is the bus the task named, `max_iter` is 50. No tool outside the four was called.
- **A mistake and its repair.** The first search was inverted (it returned the upper bound 1.1); the model then read the voltage at s=0.9, 1.0, 1.1 (0.942536, 0.934801, 0.920161 pu), saw lighter load gives higher voltage, and corrected the search. The final answer 0.909253 gives 0.942200 pu, residual 0.000000, against the committed bisection's 0.925 (0.941409 pu, residual -0.000791).
- **Why `tool_evidence` failed.** The grader (`relevant_evidence` in `src/wattson/inference/evaluate_agent.py`) needs a converged trial whose `scale` equals the answered scale exactly. The model printed `0.909253` (six decimals); its last trials were at `0.9092534627765417`, `0.9092534629628062`, `0.909253462869674`, and none equals `0.909253`. The answer was right; the reporting precision broke the evidence gate.
- **What the skill got wrong.** It called the allow-list three tools and said not to call anything else, but the harness allow-list has four (`run_python` is in `benchmarks/tool-selection/tools.json`) and `run_python` is how the model does a search. The skill also said nothing about reporting precision. Both are fixed in this PR.

## What this does and does not show

- It is one run of one task on one model. It does not show that the skill improves results: `agent_mode.py` does not load the skill text, so this is a check that the model's actual calls conform to the skill, not a with-and-without comparison. That comparison is still to do.
- The tool calls here ran on the harness's local tool server, not through the gateway's MCP route, so the gateway journal holds the four model calls (LLM route) and no `tools/call` records for them. I did not read the gateway journal on sm3lly in this run.
- The 63 solver calls counted by the harness are inside the three `run_python` calls; the journal shows 3 tool requests.

## Follow-up: tolerance, tool-name lint, skill loading (#157 slice A)

- The evidence check in `src/wattson/inference/evaluate_agent.py` now accepts a trial whose scale is within `SCALE_TOLERANCE = 1e-6` of the reported scale (answers carry six decimals, so rounding is at most 5e-7). The first replay failed this check only because `0.909253` was compared with `==` to `0.90925346…`. The numerical check is unchanged: it still re-solves the reported scale itself.
- `scripts/skill_lint.py` now compares the tool names in a skill's `## The allow-list` table with `benchmarks/tool-selection/tools.json` and fails on an unknown or an omitted tool.
- `benchmarks/lab01-simple-loadflow-fit/agent_mode.py --skill PATH` puts the skill body (frontmatter stripped) in front of the task prompt; the grader still sees the original task. The with/without comparison has not been run yet; without `--skill` the behaviour is unchanged.

## With and without the skill (same task, neo-coder, one gateway, 2026-11-18)

`agent_mode.py` run in the same container as above, three runs without the skill and three with `--skill skills/grid-study/SKILL.md`, then one run with a revised skill. Journals are under `benchmarks/lab01-simple-loadflow-fit/evidence/skill-comparison/` (one per arm; the three runs inside an arm gave the same answer).

| Arm | Runs | Result | What the model did |
|---|---|---|---|
| no skill | 3 | PASS 3/3 (numerical, tool evidence, provenance) | 3 `run_python` calls, 63 solver trials, final `0.909253` |
| skill | 3 | FAIL 0/3 | 3 `run_python` calls, stopped at the first in-tolerance trial, final message `The scale factor is **0.925**.` plus a sentence; the grader found no scale in it |
| skill + "follow the task's output format" rule | 1 | FAIL | 12 `run_python` calls refining the scale to nine digits, turn budget exhausted, no final answer |

Reading: for this model on this task the skill did not help. The 0.925 answer is inside the task's 0.002 tolerance, so the failure is the answer format ("return only the scale factor"), which the skill never states. The rule added to fix that made the model chase ever finer digits instead of answering, so it was not merged. Limits: one task, one model, temperature as served; three runs per arm are one sample because the output repeated; the revised skill was run once. No claim is made that the skill is good or bad in general. A fair test needs more tasks (the other Lab 1 families) and a second model.
