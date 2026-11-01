# The pi coding agent

[`pi`](https://pi.dev) is installed in the harness image. `just pi` runs it against the model server. **It must be configured as below;
`pi`'s defaults make these models invent commands and numbers.** `just pi` and `wattson pi` apply that configuration for you.

## What goes wrong with the defaults

Run with its defaults, `pi` offers a model the tools `read`, `bash`, `edit`, `write` and `codemode`. `codemode` is a scripting wrapper that
hides MCP tools behind a search. The students were trained with `run_python` as a plain tool, so they do not know it. We asked the
Qwen student for the scale factor in a task whose answer is 1.0625:

- It chose `bash` and ran `npx @earendil-works/flow-solver ...`, a program that does not exist.
- That failed (`npx: command not found`).
- It then answered **`0.985354`**, a number nothing had produced. Re-solving the network at that scale puts the transformer at
  0.171197 percent against a target of 0.184093 (tolerance 0.000694): wrong by 18 times the tolerance.

That is the behaviour this project exists to prevent, and `pi`'s defaults provoke it.

## What `wattson pi` does

1. Writes `pi`'s provider (your local server) and an MCP server entry for the WattsOn tools, with `"exposure": "direct"` so they are
   declared to the model as ordinary tools, and `autoEnableCodemode: false`.
2. Starts `pi` with `--no-builtin-tools` (no `bash`, `edit`, `write`) and `--system-prompt` set to the exact prompt the students were trained with.

With that configuration, the same question gave one tool call, `mcp__wattson__run_python`, and the answer `1.062500`, the reference answer.

## Limits

- This was checked on **one** task with **one** model (Qwen). The measured results in [results](results.md) come from the harness
  (`just eval`), not from `pi`. If you need a number you can cite, use `just eval`.
- The tools reach the model as `mcp__wattson__run_python` where training used `run_python`. It worked in the test above; its
  reliability across the suites is not measured.
- `scripts/pi_capture.py` runs the real `pi` against a stub server and reports which tools and which system prompt it sends. The pod test
  asserts that, so a `pi` update that changes the defaults is caught.
- If you add other tools to `pi`, you are outside what these models were trained for.
