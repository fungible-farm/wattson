"""Run the real pi, launched exactly as `wattson pi` does, against a stub model server, and report what pi sends to the model.

The check behind it: these students were trained with ``run_python`` as a plain tool and a specific system prompt. Offered pi's default
tools (bash, codemode) instead, they invented a command and a number. This prints the tool names pi declares and whether the system prompt
contains the trained one and how much pi added, as one JSON line, so a test can assert both.

    python scripts/pi_capture.py          (needs pi on PATH; run inside the harness image)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from wattson import cli
from wattson.inference.tool_probe import SYSTEM_PROMPT
from wattson.training.parity_check import stub_server

PI_TIMEOUT_S: int = 120


def main() -> int:
    captured: list = []
    server = stub_server([{"role": "assistant", "content": "stub answer"}] * 8, captured)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as home:
        os.environ["PI_CODING_AGENT_DIR"] = home
        cli.configure_pi(f"http://127.0.0.1:{server.server_address[1]}/v1", "wattson", 32768)
        command = cli.pi_command("wattson", ["--print", "--no-session", "hello"])
        done = subprocess.run(command, capture_output=True, text=True, timeout=PI_TIMEOUT_S)
    server.shutdown()
    if not captured:
        print(json.dumps({"error": "pi never called the model", "stderr": done.stderr[-400:]}))
        return 1
    request = captured[0]
    system = next(m["content"] for m in request["messages"] if m["role"] == "system")
    tools = sorted(t["function"]["name"] for t in request.get("tools") or [])
    # pi adds a short working-directory note after the prompt it is given; anything much larger would mean its own coding prompt leaked in.
    print(json.dumps({"tools": tools, "contains_trained_prompt": SYSTEM_PROMPT.strip() in system, "extra_chars": len(system) - len(SYSTEM_PROMPT)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
