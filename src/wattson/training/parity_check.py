"""Train/serve prompt parity: does training see the prompt the harness will serve? (backlog 0041)

Four data bugs in one week reached a 2-to-3-hour training run before the trainer's post-training generation check
refused the student: a first turn the prompt could not determine, reasoning text the prompt did not contain, and
reasoning kept in training histories that the harness drops when it re-sends them. All were differences between
the prompt a student trains on and the prompt it is served. This checks that directly, before any training.

Two stages, because the chat template needs ``transformers`` (the training image) and the harness needs ``mcp``
(the project environment):

``capture`` replays each scripted trajectory through the REAL harness (``tool_probe.probe``) against a stub
model server that answers each request with the next scripted assistant message, and records the ``messages``
and ``tools`` the harness sent at every turn. The tool calls run against the real MCP server.

``compare`` (in the training image) renders every captured request with the model's own chat template, exactly as
vLLM would (``add_generation_prompt=True``), and requires it to equal the prompt ``agent_sft.turn_segments``
trains that turn on, for every supervised turn, not only the first.

    uv run python -m wattson.training.parity_check capture --trajectories T.jsonl --out captured.json --limit 40
    podman run ... python -m wattson.training.parity_check compare --trajectories T.jsonl --captured captured.json --model /hub/...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# A trajectory's turns are replayed in order; the stub answers request k with assistant message k.
STUB_HOST: str = "127.0.0.1"
STUB_MODEL: str = "parity-stub"


def stub_server(script: list[dict[str, Any]], captured: list[dict[str, Any]]) -> ThreadingHTTPServer:
    """A one-conversation model server that replays ``script`` and records every request body."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 -- http.server's name
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append({"messages": body["messages"], "tools": body.get("tools")})
            turn = len(captured) - 1
            message = script[turn]
            finish = "tool_calls" if message.get("tool_calls") else "stop"
            reply = {"id": f"stub-{turn}", "object": "chat.completion", "model": STUB_MODEL,
                     "choices": [{"index": 0, "message": {k: v for k, v in message.items() if v is not None or k == "content"},
                                  "finish_reason": finish}],
                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: Any) -> None:  # silence the per-request log
            return

    return ThreadingHTTPServer((STUB_HOST, 0), Handler)


def vllm_shaped(message: dict[str, Any]) -> dict[str, Any]:
    """A trained assistant message as vLLM returns it: reasoning in ``reasoning``, which the harness then drops."""
    out = {k: v for k, v in message.items() if k != "reasoning_content"}
    if message.get("reasoning_content"):
        out["reasoning"] = message["reasoning_content"]
    return out


async def replay(trajectory: dict[str, Any], workdir: Path) -> list[dict[str, Any]]:
    """Run one trajectory through ``tool_probe.probe`` against the stub; return what the harness sent each turn."""
    from wattson.inference.tool_probe import probe

    script = [vllm_shaped(m) for m in trajectory["messages"] if m["role"] == "assistant"]
    captured: list[dict[str, Any]] = []
    server = stub_server(script, captured)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    prompt = workdir / f"{trajectory['example_id']}-{trajectory['fault']}.txt"
    prompt.write_text(trajectory["messages"][1]["content"])
    tool_names = {t["function"]["name"] for t in trajectory["tools"]}
    options = argparse.Namespace(
        base_url=f"http://{STUB_HOST}:{server.server_address[1]}", model=STUB_MODEL, prompt=prompt, study_tools=True,
        first_turn_tool_choice="auto", namespace_tools="ls" in tool_names, python_tools="run_python" in tool_names,
        # The v2 shell's tool description mentions report_text; replay with the same shell the trajectory was built with.
        python_report_fields=any("report_text" in t["function"]["description"] for t in trajectory["tools"]))
    try:
        with (workdir / f"{prompt.stem}.jsonl").open("x") as journal:
            await probe(options, journal)
    finally:
        server.shutdown()
    return captured


def capture(arguments: argparse.Namespace) -> None:
    """Replay up to ``--limit`` trajectories per (subfamily, fault) and save the captured requests."""
    rows = [json.loads(line) for line in arguments.trajectories.read_text().splitlines() if line.strip()]
    chosen: list[dict[str, Any]] = []
    seen: dict[tuple[str, str | None], int] = {}
    for row in rows:
        key = (row["subfamily"], row["fault"])
        if seen.get(key, 0) < arguments.per_kind:
            seen[key] = seen.get(key, 0) + 1
            chosen.append(row)
    arguments.workdir.mkdir(parents=True, exist_ok=True)
    out = []
    for row in chosen:
        requests = asyncio.run(replay(row, arguments.workdir))
        out.append({"example_id": row["example_id"], "fault": row["fault"], "requests": requests})
    arguments.out.write_text(json.dumps(out) + "\n")
    print(json.dumps({"trajectories": len(out), "requests": sum(len(c["requests"]) for c in out)}))


def compare(arguments: argparse.Namespace) -> int:
    """Require the served prompt of every supervised turn to equal its training prompt; return a shell exit code."""
    from transformers import AutoTokenizer

    if arguments.trainer is not None:  # an earlier trainer, to confirm this check would have caught its bug
        import importlib.util

        spec = importlib.util.spec_from_file_location("trainer_under_test", arguments.trainer)
        trainer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(trainer)
        normalise, turn_segments = trainer.normalise, trainer.turn_segments
    else:
        from wattson.training.agent_sft import normalise, turn_segments

    tokenizer = AutoTokenizer.from_pretrained(arguments.model, local_files_only=True)
    rows = {(r["example_id"], r["fault"]): r for r in map(json.loads, arguments.trajectories.read_text().splitlines()) if r}
    mismatches, checked = [], 0
    for item in json.loads(arguments.captured.read_text()):
        row = rows[(item["example_id"], item["fault"])]

        def render(messages: list[dict[str, Any]], generation: bool, tools: list = row["tools"]) -> str:
            return tokenizer.apply_chat_template(messages, tools=tools, tokenize=False, add_generation_prompt=generation)

        segments = turn_segments(normalise(row["messages"]), render)
        # A recovery variant's first turn is the injected mistake: replayed, but not a training target.
        offset = 1 if row["fault"] else 0
        for index, (training_prompt, _target) in enumerate(segments):
            if index < offset:
                continue
            request = item["requests"][index]
            served = tokenizer.apply_chat_template(normalise(request["messages"]), tools=request["tools"], tokenize=False,
                                                   add_generation_prompt=True)
            checked += 1
            if served != training_prompt:
                first = next((i for i, (a, b) in enumerate(zip(served, training_prompt)) if a != b), min(len(served), len(training_prompt)))
                mismatches.append({"example_id": item["example_id"], "fault": item["fault"], "turn": index,
                                   "served": served[max(0, first - 60):first + 80], "trained": training_prompt[max(0, first - 60):first + 80]})
    print(json.dumps({"turns_checked": checked, "mismatches": len(mismatches), "examples": mismatches[:3]}, indent=2))
    return 1 if mismatches else 0


def main() -> int:
    """``capture`` or ``compare``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--trajectories", type=Path, required=True)
    cap.add_argument("--out", type=Path, required=True)
    cap.add_argument("--workdir", type=Path, required=True, help="Scratch directory for prompts and journals (must be new)")
    cap.add_argument("--per-kind", type=int, default=3, help="Trajectories replayed per (subfamily, fault)")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--trajectories", type=Path, required=True)
    cmp_.add_argument("--captured", type=Path, required=True)
    cmp_.add_argument("--model", required=True, help="Tokenizer directory (the chat template)")
    cmp_.add_argument("--trainer", type=Path, default=None, help="A trainer file to test instead of training/agent_sft.py")
    arguments = parser.parse_args()
    if arguments.step == "capture":
        capture(arguments)
        return 0
    return compare(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
