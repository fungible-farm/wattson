#!/usr/bin/env python3
"""Demo 2 (#99): questions answered straight from the AEMO ISP Inputs and Assumptions workbook, each answer citing the exact cell.

The AI does not "know" these numbers, it reads them: a private pi (isolated HOME and agent dir) is given ONLY the containerised Excel
MCP server (read-only, no network, the workbook mounted alone) and asked a fixed list of questions about named sheets. Before any
model runs, the expected value of every question is read from the cells directly through the same server (the ground truth).
Per question the check requires, from instruments not from the model's say-so:
  * value:   the answer contains the expected value (numbers compared as numbers, text case-insensitively);
  * cell:    the answer cites the exact cell address(es) the value lives in (for example E7);
  * called:  pi's own event stream shows a `read_range` or `read_cells` call for that sheet (the tool record: stdio MCP has no gateway journal).
A question PASSES only if all three hold. The table is printed with the totals; the exit status is non-zero if any question fails.
Usage: python3 scripts/demo_isp_qa.py [--workbook PATH]   (or WATTSON_ISP_WORKBOOK=PATH) [--only Q1,Q4] [--max-turns 8]
The workbook is the public file attached to issue #4 (fetched by a container into out/isp/, sha256 e9c09575...); this script does not fetch it.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from argparse import Namespace
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WB = os.environ.get("WATTSON_ISP_WORKBOOK") or os.path.join(REPO, "out", "isp", "2026.ISP.Inputs.and.Assumptions.Workbook.7.8_WITH.COMMENTS_260624_KC.xlsm")
RUN = os.path.join(REPO, "integrations", "excel-mcp", "run.sh")
SYSTEM = ("You answer questions about an Excel workbook using your tools. Use read_cells: it returns every non-empty cell WITH ITS ADDRESS "
          "(for example {\"cell\": \"E7\", \"value\": 0.07}). Read the top of the named sheet with ONE wide call such as range A1:J40 with "
          "max_cells 400; if the data is not in it, read one more wide range, not single rows. Pick the cell by its row label and column "
          "header text; never guess a number. In your answer state the value, the sheet name and the cell address exactly as the tool "
          "returned it; for a sum, name the cells you summed. Numbers are stored as fractions (0.07 means 7%): report the stored value.")

# id, question text, sheet, cells to read (A1 range), how the expected value is formed from them
QUESTIONS = [
    ("Q1", "On the sheet 'Financial parameters', what is the central discount rate (row 'Discount rate (%)')?", "Financial parameters", "E7", "cell"),
    ("Q2", "On the sheet 'Financial parameters', what are the lower and upper bounds of the discount rate?", "Financial parameters", "D7:F7", "bounds"),
    ("Q3", "On the sheet 'Financial parameters', what weighted average cost of capital does OCGT (small GT) have in the Step Change scenario?", "Financial parameters", "E14", "cell"),
    ("Q4", "On the sheet 'Reserves', what is the initial regional reserve requirement for SA?", "Reserves", "C11", "cell"),
    ("Q5", "On the sheet 'Reserves', what is the initial regional reserve requirement for TAS?", "Reserves", "C12", "cell"),
    ("Q6", "On the sheet 'Reserves', what is the sum of the initial regional reserve requirements of all five regions (NSW, QLD, SA, TAS, VIC)?", "Reserves", "C9:C13", "sum"),
    ("Q7", "On the sheet 'Max Ramp Rates', what is the maximum ramp up in MW/min of unit BDL01 (Bairnsdale)?", "Max Ramp Rates", "E11", "cell"),
    ("Q8", "On the sheet 'Max Ramp Rates', what is the maximum ramp down in MW/min of unit BDL02 (Bairnsdale)?", "Max Ramp Rates", "F12", "cell"),
    ("Q9", "On the sheet 'Scenarios', what is the Energy Efficiency setting in the Step Change scenario?", "Scenarios", "D11", "cell"),
    ("Q10", "On the sheet 'Reserves', which region has the largest initial regional reserve requirement, and what is it?", "Reserves", "B9:C13", "argmax"),
]

pi_env = None


def fail(msg):
    print(f"FAIL demo-isp-qa: {msg}")
    sys.exit(1)


def cells_of(rng):
    """Expand an A1 range like E7 or C9:C13 or D7:F7 into a list of addresses (single column or single row ranges only)."""
    m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", rng)
    c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4) or m.group(2))
    cols = [chr(c) for c in range(ord(c1), ord(c2) + 1)]
    return [f"{c}{r}" for r in range(r1, r2 + 1) for c in cols]


async def read_truth(workbook, wanted):
    """Read every needed range directly through the MCP server: sheet -> {address: value}."""
    name = os.path.basename(workbook)
    truth = {}
    # The SDK passes only a minimal environment by default; podman needs the caller's (XDG_* decide where its image store is).
    params = StdioServerParameters(command=RUN, args=["stdio", workbook], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for sheet, rng in wanted:
                res = await s.call_tool("read_range", {"path": name, "sheet": sheet, "range": rng, "mode": "values", "max_cells": 60})
                data = json.loads(res.content[0].text)
                data = data.get("data", data)
                addrs = cells_of(rng)
                flat = [v for row in data["values"] for v in row]
                truth.setdefault(sheet, {}).update(dict(zip(addrs, flat)))
    return truth


def expected_for(kind, sheet, rng, truth):
    values = [truth[sheet][a] for a in cells_of(rng)]
    if kind == "cell":
        return {"values": values, "cells": cells_of(rng)}
    if kind == "bounds":
        return {"values": [values[0], values[2]], "cells": [cells_of(rng)[0], cells_of(rng)[2]]}
    if kind == "sum":
        return {"values": [sum(values)], "cells": [cells_of(rng)[0], cells_of(rng)[-1]]}
    if kind == "argmax":
        column = [truth["Reserves"][f"C{r}"] for r in range(9, 14)]
        i = max(range(len(column)), key=lambda k: column[k])
        return {"values": [column[i], truth["Reserves"][f"B{9 + i}"]], "cells": [f"C{9 + i}"]}
    raise ValueError(kind)


def number_in(text, value):
    return any(abs(float(x) - float(value)) < 1e-9 for x in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", "")))


def run_pi(question, workbook, max_turns, max_tools=12, seconds=300):
    proc_cmd = list(pi_env["command"])
    proc_cmd[-1] = question
    proc = subprocess.Popen(proc_cmd, env=pi_env["env"], cwd=pi_env["cwd"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    events, turns, tools, deadline, why = [], 0, 0, time.time() + seconds, "finished"
    import selectors

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        while proc.poll() is None or sel.select(0):
            if time.time() > deadline:
                why = f"stopped: {seconds}s"
                break
            if not sel.select(0.5):
                continue
            line = proc.stdout.readline()
            if not line:
                break
            try:
                e = json.loads(line)
            except ValueError:
                continue
            events.append(e)
            turns += e.get("type") == "turn_start"
            tools += e.get("type") == "tool_execution_start"
            if turns > max_turns or tools > max_tools:
                why = f"stopped: {turns} turns / {tools} tool calls"
                break
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
    final = ""
    for e in events:
        if e.get("type") == "turn_end":
            parts = (e.get("message") or {}).get("content") or []
            text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
            if text.strip():
                final = text
    calls = [(str(e.get("toolName")), e.get("args") or {}) for e in events if e.get("type") == "tool_execution_start"]
    return final, calls, why


def main():
    global pi_env
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", default=DEFAULT_WB)
    parser.add_argument("--only", default="")
    parser.add_argument("--max-turns", type=int, default=12)
    args = parser.parse_args()
    workbook = os.path.abspath(args.workbook)
    if not os.path.exists(workbook):
        fail(f"workbook not found: {workbook} (fetch it into out/isp/ first, see the docstring)")
    pi = shutil.which("pi")
    if not pi:
        fail("pi is not installed")
    questions = [q for q in QUESTIONS if not args.only or q[0] in args.only.split(",")]
    wanted = []
    for _, _, sheet, rng, kind in questions:
        wanted.append((sheet, "B9:C13" if kind == "argmax" else rng))
    built = subprocess.run([RUN, "build"], capture_output=True, text=True, timeout=1500)  # idempotent: the Excel MCP image
    if built.returncode:
        fail(f"building the Excel MCP image failed: {(built.stdout + built.stderr)[-300:]}")
    truth = asyncio.run(read_truth(workbook, wanted))
    print("ground truth read directly from the cells (no model): " + "; ".join(f"{s}!{a}={v!r}" for s, cells in truth.items() for a, v in list(cells.items())[:6]))

    scratch = tempfile.mkdtemp(prefix="isp-", dir="/tmp")
    os.chmod(scratch, 0o755)
    try:
        spec = importlib.util.spec_from_file_location("pi_delegate", os.path.join(REPO, "scripts", "pi_delegate.py"))
        delegate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(delegate)
        ns = Namespace(pi=pi, base_url=delegate.DEFAULT_URL, model=delegate.DEFAULT_MODEL, max_tokens=1024, allow_actions=False,
                       directory=Path(scratch), max_turns=args.max_turns, max_tool_calls=12, timeout=240, prompt="x")
        command, env = delegate.configure(ns, Path(scratch) / "pi-out")
        name = os.path.basename(workbook)
        # pi starts a stdio MCP server with a minimal environment, and podman needs the caller's (XDG_* decide where its image store is):
        # without these the container runs against an empty store, the server never connects and the model is left with no tools.
        # These come from THIS process's environment (where `run.sh build` put the image), not from pi's private HOME, which has an empty store.
        passed_env = [f"{k}={os.environ[k]}" for k in ("HOME", "PATH", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "TMPDIR") if k in os.environ]
        env_flags = [x for kv in passed_env for x in ("--env", kv)]
        added = subprocess.run([pi, "mcp", "add", "excel", "--exposure", "direct", *env_flags, "--", RUN, "stdio", workbook], env=env, capture_output=True, text=True)
        if added.returncode:
            fail(f"pi mcp add failed: {(added.stdout + added.stderr)[-300:]}")
        listed = subprocess.run([pi, "mcp", "list"], env=env, capture_output=True, text=True, timeout=300)
        print(f"pi mcp list: rc={listed.returncode} {listed.stdout.splitlines()[0] if listed.stdout else listed.stderr[-200:]}")
        if listed.returncode or "connected" not in listed.stdout or "read_cells" not in listed.stdout:
            fail(f"pi did not connect to the Excel MCP server: {(listed.stdout + listed.stderr)[-1200:]}")
        command = [c for c in command if c not in ("--no-mcp", "--no-extensions")]
        i = command.index("--tools")
        # read_range returns a bare grid (the model must count to name a cell, and measured runs show it miscounts), so the demo offers only
        # the address-keyed read_cells (and describe_workbook); describe_sheet is excluded because it can force a whole-workbook load.
        command[i:i + 2] = ["--exclude-tools", "bash,edit,write,grep,find,ls,mcp__excel__read_range,mcp__excel__describe_sheet"]
        command[command.index("--system-prompt") + 1] = SYSTEM + f" The workbook's path argument is \"{name}\"."
        pi_env = {"command": command, "env": env, "cwd": scratch}

        print(f"{'id':4} {'value':6} {'cell':5} {'called':6} verdict  question")
        passed = 0
        for qid, text, sheet, rng, kind in questions:
            exp = expected_for(kind, sheet, "B9:C13" if kind == "argmax" else rng, truth)
            final, calls, why = run_pi(text, workbook, args.max_turns)
            low = final.lower()
            value_ok = all((number_in(final, v) if isinstance(v, (int, float)) else str(v).lower() in low) for v in exp["values"])
            cell_ok = all(c.lower() in low for c in exp["cells"])
            called_ok = any(n.endswith(("read_range", "read_cells")) and str(a.get("sheet", "")).lower() == sheet.lower() for n, a in calls)
            ok = value_ok and cell_ok and called_ok
            passed += ok
            print(f"{qid:4} {str(value_ok):6} {str(cell_ok):5} {str(called_ok):6} {'PASS' if ok else 'FAIL':7}  {text[:70]}")
            if not ok:
                print(f"       expected={exp['values']} cells={exp['cells']} run={why} tool_calls={[(n, a.get('sheet'), a.get('range')) for n, a in calls]}")
                print(f"       answer={final.strip()[:240]!r}")
        print(f"summary: {passed}/{len(questions)} questions answered correctly with the cell cited and the tool called")
        if passed != len(questions):
            fail(f"{len(questions) - passed} question(s) failed; the table above is the evidence")
        print("PASS demo-isp-qa: every answer matched the cells read directly, cited the cell, and came from a read_cells call")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
