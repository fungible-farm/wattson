# 0009 — Recovery training supervised the injected mistake

- **Status:** **done.** Fixed in `8314e14`; verified 2026-10-23 on r6b's journals (below). Review finding B1, high severity ([review](reviews/2026-10-23-r6-merit-review.md)).
- **Found:** 2026-10-23. Caught before any r6 student finished training. The run was stopped at about step 17/45 and restarted.

## Defect
Each of the 119 recovery trajectories opens with a deliberately wrong call: `"None"` (FM-7), line 0 out (FM-18), or `run_power_flow` for an N-1 report (FM-1/9). `agent_sft` supervised every assistant turn, so for 119 first-turn prompts the target was the mistake, about half the time against the correct call for the same prompt. `generation_check` sampled the first rows per subfamily and compared 32 tokens. That is short of where the calls diverge, so it could not see the problem.

## Fix
`agent_sft.supervised_segments` drops the injected turn as a target and keeps it in the history of every later turn, so the correction is still learned from the real MCP error. `generation_check` samples plain trajectories only. Supervised turns went from 2,326 to 2,207.

## Verification still owed
When r6 reports, count in every student journal the calls that use `contingency_line: "None"` or `0`, and `run_power_flow` with a contingency. A non-zero rate on tasks that don't need it means the fix didn't hold.

## Verification (2026-10-23, r6b journals)
Counted in every r6b journal:
- `"None"` in `contingency_line` or `observation_id`;
- `contingency_line: 0` on a task whose contingency is not line 0;
- `run_power_flow` with a `contingency_line`.

| Run | Attempts | `run_study_trial` calls | `"None"` | Line 0 (not asked for) | `run_power_flow` + contingency |
|---|---|---|---|---|---|
| Qwen student, smoke | 20 | 102 | 0 | 0 | 0 |
| Granite student, smoke | 20 | 130 | 0 | 0 | 0 |
| Qwen student, 100 tasks | 100 | 483 | 0 | 0 | 0 |
| Granite student, 100 tasks | 100 | 595 | 0 | 0 | 0 |
| Qwen base (positive control) | 100 | 467 | 17 | 0 | 13 |
| Granite base (positive control) | 100 | 678 | 25 | 1 | 0 |

The students made none of the three injected mistakes. The bases made them, which shows the counts
detect them. The fix held.
