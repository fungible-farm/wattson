"""Teacher client for the M05 Qwen3.8 distillation work package.

Sends a structured M04 task to the Qwen3.8 teacher (through the llama-swap
gateway) and receives a candidate Python solution. The teacher PROPOSES
behaviour; the pandapower solver provides the OBJECTIVE observation (see
filter.py). The teacher's own reasoning is never retained as engineering truth.

Sandbox note: the teacher is reached over HTTP at the llama-swap gateway
(http://127.0.0.1:8000) -- the mission's API boundary. The two candidate
teachers are qwen3.8-flash-next and qwen3.8-27b. Both are REASONING models:
they emit a `reasoning_content` field and a final `content`. We capture both
for provenance but only parse the final `content` for the code.

Scaffold design (why the prompt is a template, not an open-ended ask):
the 27B teacher is a reasoning model that, on an open-ended "write the whole
script" prompt, spends its entire completion budget on reasoning and never
emits the code (measured: finish=length at 4096 tokens, code_len=0). So the
prompt hands the teacher a per-subfamily SCRIPT SCAFFOLD that already carries
the boilerplate (network loading, the metric, the bisection loop, the JSON
print) and the core computation. The teacher's job collapses to filling the
@@PLACEHOLDER@@ tokens with the task-specific values (element id, target,
tolerance, ...). That keeps generation short and focused while still yielding
a self-contained, solver-verified solution. The scaffold's loading path is the
canonical pm._load_network_object (file cases AND built fixtures), so the
teacher's metric matches the filter's independent re-solve exactly.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Self

import httpx

from powercoder.inference.client import InferenceClient

# Case-name mapping is handled by pm._load_network_object (it knows both the
# file-backed cases and the built fixtures), so the scaffold only ever needs
# the case NAME, not a file path.
CASE_NAMES: tuple[str, ...] = ("snem-sa-2000", "snem-1803", "fixture-2bus-ok")

# Per-subfamily scaffold templates. @@TOKEN@@ markers are either pre-filled by
# build_prompt (deterministic values: @@CASE@@, @@SCALE_LO@@, @@SCALE_HI@@) or
# left for the teacher to fill from task.inputs (the task-specific values).
# Each scaffold prints EXACTLY one line of JSON to stdout.
SCAFFOLDS: dict[str, str] = {
    "pf-load-scale-fit": '''\
import sys, json, copy
sys.path.insert(0, ".")
from powercoder.inference import powermcp as pm
import pandapower as pp
base, _ = pm._load_network_object("@@CASE@@")
BUS = @@BUS_ID@@
def metric(s):
    n = copy.deepcopy(base)
    for i in n.load.index:
        n.load.loc[i, "p_mw"] *= s
        n.load.loc[i, "q_mvar"] *= s
    pp.runpp(n, numba=True)
    return float(n.res_bus.loc[BUS, "vm_pu"])
TARGET, TOL = @@TARGET@@, @@TOL@@
a, b = @@SCALE_LO@@, @@SCALE_HI@@
fa = metric(a) - TARGET
for _ in range(60):
    m = 0.5 * (a + b)
    fm = metric(m) - TARGET
    if abs(fm) <= TOL:
        a = b = m
        break
    if fa * fm > 0:
        a, fa = m, fm
    else:
        b = m
    if b - a < 1e-9:
        break
S = round(0.5 * (a + b), 6)
print(json.dumps({"answer": {"scale_factor": S}}))
''',
    "pf-line-rating-fit": '''\
import sys, json, copy
sys.path.insert(0, ".")
from powercoder.inference import powermcp as pm
import pandapower as pp
base, _ = pm._load_network_object("@@CASE@@")
LINE = @@LINE_ID@@
def metric(s):
    n = copy.deepcopy(base)
    n.line.loc[LINE, "max_i_ka"] *= s
    pp.runpp(n, numba=True)
    return float(n.res_line.loc[LINE, "loading_percent"])
TARGET, TOL = @@TARGET@@, @@TOL@@
a, b = @@SCALE_LO@@, @@SCALE_HI@@
fa = metric(a) - TARGET
for _ in range(60):
    m = 0.5 * (a + b)
    fm = metric(m) - TARGET
    if abs(fm) <= TOL:
        a = b = m
        break
    if fa * fm > 0:
        a, fa = m, fm
    else:
        b = m
    if b - a < 1e-9:
        break
S = round(0.5 * (a + b), 6)
print(json.dumps({"answer": {"scale_factor": S}}))
''',
    "pf-gen-dispatch-fit": '''\
import sys, json, copy
sys.path.insert(0, ".")
from powercoder.inference import powermcp as pm
import pandapower as pp
base, _ = pm._load_network_object("@@CASE@@")
GEN = @@GEN_ID@@
TRAFO = @@TRAFO_ID@@
def metric(s):
    n = copy.deepcopy(base)
    n.gen.loc[GEN, "p_mw"] *= s
    pp.runpp(n, numba=True)
    return float(n.res_trafo.loc[TRAFO, "loading_percent"])
TARGET, TOL = @@TARGET@@, @@TOL@@
a, b = @@SCALE_LO@@, @@SCALE_HI@@
fa = metric(a) - TARGET
for _ in range(60):
    m = 0.5 * (a + b)
    fm = metric(m) - TARGET
    if abs(fm) <= TOL:
        a = b = m
        break
    if fa * fm > 0:
        a, fa = m, fm
    else:
        b = m
    if b - a < 1e-9:
        break
S = round(0.5 * (a + b), 6)
print(json.dumps({"answer": {"scale_factor": S}}))
''',
    "pf-report": '''\
import sys, json, copy
sys.path.insert(0, ".")
from powercoder.inference import powermcp as pm
import pandapower as pp
import pandapower.pf.run_newton_raphson_pf as _nr
_pp = _nr.newtonpf
_st = {"it": 0}
def _cn(*a, **k):
    o = _pp(*a, **k)
    _st["it"] = o[2] if len(o) > 2 else 0
    return o
_nr.newtonpf = _cn
base, _ = pm._load_network_object("@@CASE@@")
net = copy.deepcopy(base)
CONTINGENCY = @@CONTINGENCY_LINE@@
if CONTINGENCY is not None:
    net.line = net.line.drop(index=int(CONTINGENCY))
try:
    pp.runpp(net, numba=True)
    _conv = True
except Exception:
    _conv = False
if _conv:
    vm = net.res_bus["vm_pu"]
    ans = {"converged": True, "iterations": _st["it"],
           "min_bus_voltage_pu": float(vm.min()),
           "max_bus_voltage_pu": float(vm.max()),
           "max_line_loading_percent": float(net.res_line["loading_percent"].max()),
           "voltage_violations": int(((vm < 0.95) | (vm > 1.05)).sum())}
else:
    ans = {"converged": False, "iterations": None,
           "min_bus_voltage_pu": None, "max_bus_voltage_pu": None,
           "max_line_loading_percent": None, "voltage_violations": None}
print(json.dumps({"answer": ans}))
''',
    "mcp-trajectory": '''\
import sys, json
sys.path.insert(0, ".")
from powercoder.inference import powermcp as pm
mcp = pm.PowerMCP()
CASE = "@@CASE@@"
MAX_ITER = @@MAX_ITER@@
CHECK_LIMITS = @@CHECK_LIMITS@@
traj = []
r1 = mcp.load_network(CASE)
traj.append({"tool": "load_network", "args": {"network_id": CASE},
             "status": r1.status.value,
             "result": r1.data if r1.data else {"error": r1.error},
             "evidence": r1.evidence})
r2 = mcp.run_power_flow(CASE, method="nr", max_iter=MAX_ITER,
                        check_limits=CHECK_LIMITS)
traj.append({"tool": "run_power_flow",
             "args": {"network_id": CASE, "method": "nr",
                      "max_iter": MAX_ITER, "check_limits": CHECK_LIMITS},
             "status": r2.status.value,
             "result": r2.data if r2.data else {"error": r2.error},
             "evidence": r2.evidence})
print(json.dumps({"answer": {"trajectory": traj}}))
''',
}

# Which @@TOKEN@@ each subfamily leaves for the TEACHER to fill (the rest are
# pre-filled by build_prompt). Used to build the placeholder legend.
TEACHER_TOKENS: dict[str, list[tuple[str, str]]] = {
    "pf-load-scale-fit": [
        ("@@BUS_ID@@", 'the integer bus id (from element "bus <int>")'),
        ("@@TARGET@@", "the target value (inputs.target)"),
        ("@@TOL@@", "the tolerance (inputs.tolerance)"),
    ],
    "pf-line-rating-fit": [
        ("@@LINE_ID@@", 'the integer line id (from element "line <int>")'),
        ("@@TARGET@@", "the target value (inputs.target)"),
        ("@@TOL@@", "the tolerance (inputs.tolerance)"),
    ],
    "pf-gen-dispatch-fit": [
        ("@@GEN_ID@@", 'the integer generator id (from "generator <int> ...")'),
        ("@@TRAFO_ID@@", 'the integer transformer id (from "... (transformer <int>)")'),
        ("@@TARGET@@", "the target value (inputs.target)"),
        ("@@TOL@@", "the tolerance (inputs.tolerance)"),
    ],
    "pf-report": [
        ("@@CONTINGENCY_LINE@@", "the contingency line id, or None if inputs.contingency_line is null"),
    ],
    "mcp-trajectory": [
        ("@@MAX_ITER@@", "the max_iter value (inputs.max_iter)"),
        ("@@CHECK_LIMITS@@", "the check_limits value (inputs.check_limits), True or False"),
    ],
}


@dataclass
class TeacherResult:
    """One teacher generation (or repair) call, with full provenance."""

    code: str
    raw_response: str
    reasoning_content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float
    finish_reason: str = ""
    is_repair: bool = False
    repair_attempt: int = 0


class TeacherClient:
    """Wraps InferenceClient to turn an M04 example into candidate code.

    The prompt is a per-subfamily scaffold (see SCAFFOLDS): boilerplate and
    core computation are pre-written, and the teacher fills only the
    task-specific @@TOKEN@@ values. This keeps generation short and reliable
    for the verbose 27B reasoning model.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 1200.0,
        temperature: float = 0.0,
        max_tokens: int = 3000,
        max_retries: int = 5,
        retry_delay_s: float = 8.0,
    ):
        self.model = model
        self.client = InferenceClient(base_url=base_url, timeout=timeout)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_delay_s = retry_delay_s

    def _scaffold(self, example: dict[str, Any]) -> str:
        """Return the subfamily scaffold with deterministic tokens pre-filled."""
        task = example["task"]
        subfamily = example["subfamily"]
        inputs = task["inputs"]
        case = inputs.get("case", "snem-sa-2000")
        scaffold = SCAFFOLDS[subfamily]
        scaffold = scaffold.replace("@@CASE@@", case)
        if "scale_lo" in inputs:
            scaffold = scaffold.replace("@@SCALE_LO@@", repr(float(inputs["scale_lo"])))
            scaffold = scaffold.replace("@@SCALE_HI@@", repr(float(inputs["scale_hi"])))
        return scaffold

    def build_prompt(self, example: dict[str, Any]) -> list[dict[str, str]]:
        """Build the system+user messages for a fresh (non-repair) call."""
        task = example["task"]
        subfamily = example["subfamily"]
        scaffold = self._scaffold(example)
        legend = "\n".join(
            f"  {tok}  ->  {desc}" for tok, desc in TEACHER_TOKENS[subfamily]
        )
        system = (
            "You are a power-systems engineer. You are given a task and a "
            "Python script template (a scaffold) that already contains the "
            "network loading, the metric, and the computation. Your job is "
            "ONLY to replace the @@TOKEN@@ placeholders with the correct "
            "task-specific values. Do not change any other line. Keep your "
            "reasoning to AT MOST 3 short lines (just identify the values). "
            "Then output the COMPLETE filled-in Python script inside a "
            "```python fence, with no surrounding prose."
        )
        user = (
            f"TASK:\n{task['prompt']}\n\n"
            f"INPUTS (a Python dict):\n"
            f"{json.dumps(task['inputs'], indent=2)}\n\n"
            f"PLACEHOLDERS TO FILL (and only these):\n{legend}\n\n"
            f"SCAFFOLD (fill the @@TOKEN@@ markers, keep every other line):\n"
            f"```\n{scaffold}\n```\n\n"
            f"Output the complete filled-in script in a ```python fence."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def build_repair_prompt(
        self,
        example: dict[str, Any],
        previous_code: str,
        diagnostics: str,
    ) -> list[dict[str, str]]:
        """Build the messages for a targeted repair call.

        The repair reuses the original scaffold prompt, shows the teacher its
        own previous (incorrect) filled-in script, and hands back the ACTUAL
        execution diagnostics so the correction is grounded in solver
        observation, not re-derivation.
        """
        messages = self.build_prompt(example)
        messages.append({"role": "assistant", "content": previous_code})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your filled-in script did not pass verification. Actual "
                    "diagnostics from running it:\n\n"
                    f"{diagnostics}\n\n"
                    "Fix the script (usually a wrong placeholder value) so it "
                    "prints the correct required JSON to stdout. Output the "
                    "complete corrected script in a ```python fence, with no "
                    "surrounding prose."
                ),
            }
        )
        return messages

    def _call(self, messages: list[dict[str, str]]) -> TeacherResult:
        # The unified-memory gateway keeps only ONE model loaded, so requesting
        # a model that is not currently loaded triggers a swap that can return
        # a transient HTTP 500 mid-swap. Ride out swaps by retrying server
        # errors (5xx) and transport errors with a delay. Client errors (4xx)
        # are real and are raised immediately.
        resp: dict[str, Any] | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat_completion(
                    self.model,
                    messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500 or attempt == self.max_retries - 1:
                    raise
                time.sleep(self.retry_delay_s)
            except httpx.TransportError:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(self.retry_delay_s)
        assert resp is not None
        choice = resp["choices"][0]
        message = choice["message"]
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or "").strip()
        usage = resp.get("usage", {})
        return TeacherResult(
            code=self.extract_code(content),
            raw_response=content,
            reasoning_content=reasoning,
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            elapsed_s=resp.get("elapsed_seconds", 0.0),
            finish_reason=choice.get("finish_reason", ""),
        )

    def generate(self, example: dict[str, Any]) -> TeacherResult:
        """Fresh generation for an example."""
        result = self._call(self.build_prompt(example))
        result.is_repair = False
        return result

    def repair(
        self,
        example: dict[str, Any],
        previous_code: str,
        diagnostics: str,
        attempt: int,
    ) -> TeacherResult:
        """Targeted correction using actual execution diagnostics."""
        result = self._call(
            self.build_repair_prompt(example, previous_code, diagnostics)
        )
        result.is_repair = True
        result.repair_attempt = attempt
        return result

    @staticmethod
    def extract_code(content: str) -> str:
        """Pull the script out of a ```python ... ``` fence if present."""
        match = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return content.strip()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
