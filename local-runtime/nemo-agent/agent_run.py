"""Run the same simple prompts through real Pi + upstream just-mcp, retaining JSON events.

Only MCP tools are exposed. No bash/read/write tools, host mounts, or engine socket
are provided. This is a paired task demonstration, not a statistically powered study.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import time
import urllib.request
from typing import Any

from query import lookup

# Explicit LAN service observed in the user's Pi configuration and /v1/models.
SM3LLY_BASE_URL: str = "http://192.168.1.137:8002/v1"
CPU_BASE_URL: str = "http://127.0.0.1:1234/v1"
# Same client output budget for both arms. Server/model/context differ and are named.
MAX_OUTPUT_TOKENS: int = 1024
CONTEXT_TOKENS: int = 8192
# Bound one prompt including MCP startup and CPU inference.
PROMPT_TIMEOUT_SECONDS: int = 300
# Defaults in tasks/Justfile are part of its real, optional-argument MCP contract.
DEFAULT_TECHNOLOGY: str = "PV1Axis"
DEFAULT_YEAR: str = "2030"
DEFAULT_SCENARIO: str = "CP"
# Pi reports tool duration in milliseconds, independently of server/model time.
MILLISECONDS_PER_SECOND: float = 1000.0
# Two independent, source-verifiable tasks; task numbers are not embedded in the prompt.
PROMPTS: tuple[tuple[str, str], ...] = (
    ("PV1Axis", "Use the GenCost tool to get the 2030 Current Policies capital cost for large-scale solar PV (PV1Axis). Return the observed capital cost, units and source."),
    ("Wind", "Use the GenCost tool to get the 2030 Current Policies capital cost for onshore wind (Wind). Return the observed capital cost, units and source."),
)


def configuration(backend: str, destination: Path) -> str:
    """Write isolated Pi configuration so no personal/cloud tools or auth are loaded."""
    destination.mkdir(parents=True, exist_ok=True)
    provider = "sm3lly" if backend == "sm3lly" else "candle"
    url = os.environ.get("WATTSON_SM3LLY_URL", SM3LLY_BASE_URL) if backend == "sm3lly" else CPU_BASE_URL
    model = "neo-coder"
    if backend == "candle":
        # Use the serving stack's actual advertised ID rather than inventing an alias.
        with urllib.request.urlopen(url + "/models", timeout=PROMPT_TIMEOUT_SECONDS) as response:
            model = json.load(response)["data"][0]["id"]
    models = {"providers": {provider: {
        "baseUrl": url, "api": "openai-completions", "apiKey": "local-no-auth",
        "models": [{"id": model, "name": model, "reasoning": False,
                    "input": ["text"], "contextWindow": CONTEXT_TOKENS, "maxTokens": MAX_OUTPUT_TOKENS,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}],
    }}}
    (destination / "models.json").write_text(json.dumps(models))
    (destination / "settings.json").write_text(json.dumps({"quietStartup": True, "autoEnableCodemode": False}))
    (destination / "mcp.json").write_text(json.dumps({
        "autoEnableCodemode": False,
        "mcpServers": {"gencost": {"command": "/usr/local/bin/just-mcp",
            "args": ["--watch-dir", "/opt/wattson-nemo/tasks"],
            "exposure": "direct", "timeout": PROMPT_TIMEOUT_SECONDS}},
    }))
    return f"{provider}/{model}"


def summarize(events: list[dict[str, Any]], expected: dict[str, Any]) -> dict[str, Any]:
    """Score tool calls and final text separately; never treat guessed values as passes."""
    calls: list[dict[str, Any]] = []
    outputs: list[Any] = []
    final_text = ""
    input_tokens = output_tokens = 0
    tool_seconds = 0.0
    observed_rows: list[dict[str, Any]] = []
    model_errors: list[str] = []

    def collect_result(value: Any) -> None:
        """Decode actual tool JSON; numbers appearing in errors/hashes are not evidence."""
        if isinstance(value, dict):
            if "capital_cost" in value:
                observed_rows.append(value)
            for child in value.values():
                collect_result(child)
        elif isinstance(value, list):
            for child in value:
                collect_result(child)
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return
            if isinstance(decoded, (dict, list)):
                collect_result(decoded)

    for event in events:
        if event.get("type") == "tool_execution_end":
            outputs.append(event.get("result"))
            tool_seconds += event.get("durationMs", 0) / MILLISECONDS_PER_SECOND
            if not event.get("isError", False):
                collect_result(event.get("result"))
        if event.get("type") != "message_end":
            continue
        message = event.get("message", {})
        if message.get("role") != "assistant":
            continue
        if message.get("stopReason") == "error":
            model_errors.append(message.get("errorMessage", "unknown model error"))
        usage = message.get("usage", {})
        input_tokens += usage.get("input", 0)
        output_tokens += usage.get("output", 0)
        text_parts: list[str] = []
        for block in message.get("content", []):
            if block.get("type") == "toolCall":
                calls.append({"name": block.get("name"), "arguments": block.get("arguments")})
            elif block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        final_text = "\n".join(text_parts)
    valid_call = any(
        "gencost" in str(call["name"]) and isinstance(call.get("arguments"), dict)
        and call["arguments"].get("technology", DEFAULT_TECHNOLOGY) == expected["technology"]
        and str(call["arguments"].get("year", DEFAULT_YEAR)) == str(expected["year"])
        and call["arguments"].get("scenario", DEFAULT_SCENARIO) == expected["scenario"]
        for call in calls
    )
    observed = any(all(row.get(key) == expected[key] for key in (
        "capital_cost", "technology", "year", "scenario", "source_commit")) for row in observed_rows)
    # A number must stand alone; 11230 or a hash containing 1123 is not the answer.
    answer_numbers = re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w.])", final_text)
    correct_answer = any(float(number.replace(",", "")) == expected["capital_cost"] for number in answer_numbers)
    return {"calls": calls, "tool_results": outputs, "correct_task_call": valid_call,
            "observed_expected_value": observed, "correct_final_value": correct_answer,
            "passed": valid_call and observed and correct_answer and not model_errors,
            "model_errors": model_errors,
            "tool_seconds": tool_seconds,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "final_answer": final_text}


def run(backend: str, output: Path) -> None:
    """Execute actual Pi prompts sequentially and write raw journals plus a scorecard."""
    output.mkdir(parents=True, exist_ok=True)
    model = configuration(backend, Path("/tmp/pi"))
    rows: list[dict[str, Any]] = []
    for index, (technology, prompt) in enumerate(PROMPTS):
        command = ["pi", "--offline", "--no-context-files", "--no-skills", "--no-prompt-templates",
                   "--no-extensions", "-e", "builtin:mcp", "--no-builtin-tools", "--no-session",
                   "--thinking", "off", "--mode", "json", "--model", model,
                   "--system-prompt", "Use the available read-only GenCost task. Never guess a cost. Call the task, then report its observed result and source. Do not create or edit tasks.",
                   "--print", prompt]
        started = time.monotonic()
        terminal = "completed"
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=PROMPT_TIMEOUT_SECONDS)
            stdout, stderr, code = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as error:
            terminal = "timeout"
            stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout or ""
            stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr or ""
            code = None
        duration = time.monotonic() - started
        (output / f"prompt-{index}.jsonl").write_text(stdout)
        (output / f"prompt-{index}.stderr").write_text(stderr)
        events: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        expected = lookup(technology, 2030, "CP")
        row = {"backend": backend, "model": model, "prompt": prompt,
               "wall_seconds": duration, "process_exit_code": code, "terminal": terminal,
               "expected": expected, **summarize(events, expected)}
        row["passed"] = row["passed"] and code == 0 and terminal == "completed"
        rows.append(row)
        print(json.dumps(row), flush=True)
    (output / "scorecard.json").write_text(json.dumps({"backend": backend, "tasks": rows,
        "conditions": {"same_prompts": True, "same_mcp_and_tasks": True, "same_weights": False,
        "note": "Different models/devices: task-level deployment comparison, not an inference-backend speedup claim."}}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=["sm3lly", "candle"])
    parser.add_argument("--out", type=Path, default=Path("/results"))
    args = parser.parse_args()
    run(args.backend, args.out)
