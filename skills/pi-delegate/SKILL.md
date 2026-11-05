---
name: pi-delegate
description: Delegate a bounded coding review, investigation, or explicitly authorized isolated edit to local neo-coder through pi, retaining tool evidence without copying the parent conversation. Use when local pi subagents are requested or useful for an independent subtask.
---

# Delegate to local pi

Use this repository's [scripts/pi_delegate.py](../../scripts/pi_delegate.py). It launches a
separate local pi process, not a cloud agent or a model service. Give it one concrete task
and the minimum paths/context it needs; do not copy the parent transcript.

## Read-only work

Write the task to a UTF-8 file: state the objective, file scope, expected evidence, and
what the delegate should return. Require actual `read` calls, relevant path/line citations,
and explicit limits. A skeptical review is one opposing review, not many repeated calls.

From the checkout, with its environment already installed:

```bash
# Choose a new private output directory for every attempt.
uv run --no-sync python scripts/pi_delegate.py \
  --workdir /absolute/reviewed/checkout \
  --task-file /tmp/delegate-task.txt \
  --out-dir /tmp/delegate-evidence-new --timeout 180
```

`--task 'literal task text'` is also supported. Pass data through arguments/files, never
construct a shell command containing model/task text. The default endpoint is
`http://192.168.1.137:8002/v1`, alias `neo-coder`; overrides accept only localhost or a
literal private IP. There is no cloud fallback. If unavailable, report the endpoint and
failed attempt; do not start/stop/reconfigure the model or silently retry elsewhere.

Default limits: 64 KiB task text, 180 wall seconds, six turns, twelve observed tool starts, 2048 requested output
tokens per response, and 8 MiB combined stdout/stderr. Explicit positive CLI overrides
are available for a justified larger task. Tool/turn budgets stop on observed events; they are not an authorization sandbox.
On budget exhaustion, split or narrow the task
rather than treating the partial report as completion.

The helper supplies only `read,grep,find,ls` by default. It disables MCP, extensions,
skill/prompt/theme discovery, project approval, AGENTS/CLAUDE context discovery, sessions,
startup network operations and telemetry. It creates a private HOME/config/cache and
fixed local-provider configuration without inheriting credential environment variables.
It terminates its owned process group on timeout, interruption and completion; it does
not manage the separately hosted inference service.

## Interpret evidence

Read `report.json` and `final.md` first; keep `events.jsonl` and `stderr.txt` out of the
parent context unless a claim needs checking. Reports include the task hash, bounds,
observed turns/tool calls, matching successful tool events and final answer. Outputs may
contain source code; retain them privately and do not commit transcripts by default.

Only `status: complete` exits zero. It requires a successful process, settled nontruncated
final answer and a recorded successful `read`. A final answer without read evidence is
`no_read_evidence`, not success. Other terminal states include `timed_out`, `output_limit`,
`budget_exceeded`, `protocol_error`, `incomplete`, `interrupted` and `failed`.

Completion proves activity and protocol completion, **not correctness**. Validate material
findings against the source/tool results and run the parent's appropriate checks before
acting on them. A delegate cannot authorize a merge, deployment, credential use or a
broader task. Keep unsupported claims and failed attempts visible.

## Authorized actions

Only use actions when the existing user request authorizes the changes. Prepare an
isolated scratch directory or separate git worktree, then explicitly supply both flags:

```bash
uv run --no-sync python scripts/pi_delegate.py \
  --allow-actions --action-workdir /absolute/isolated/worktree \
  --task-file /tmp/delegate-edit-task.txt --out-dir /tmp/delegate-edit-evidence-new
```

This adds `bash,edit,write`. The helper rejects action directories inside/above its own
checkout; the parent must verify that the supplied directory is the intended isolated
workspace. Inspect the resulting diff and test evidence yourself. Do not ask the delegate
to commit, push or deploy.

Configuration isolation and tool allowlists are not an OS filesystem/network sandbox.
Use trusted tasks/projects; built-in reads can reach outside cwd, and action-mode bash
has the host user's access. Never include secrets or delegate untrusted instructions.
