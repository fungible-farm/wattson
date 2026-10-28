"""Candidate filtering for the M05 distillation work package.

Executes the teacher's candidate Python script in an isolated subprocess
(same venv, fresh process, bounded wall clock), parses its single-line JSON
answer, and runs an INDEPENDENT numerical check: the pandapower solver is
re-run on the same case at the candidate's claimed parameters, and the
re-computed quantity is compared against the task target. The solver is the
objective observation; the teacher's reasoning is never retained as truth.

Acceptance classes (the mission's rejected/quarantined distinction):
  accepted    -- the script ran, produced a parseable answer, and the
                 independent solver check passed within tolerance.
  rejected    -- the script ran and produced a parseable answer, but the
                 independent check FAILED (numerically wrong / out of range).
  quarantined -- the script could not be evaluated at all (non-zero exit,
                 timeout, or no parseable JSON answer).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandapower as pp

from wattson.datasets import generate as gen
from wattson.inference import powermcp as pm

REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# Case-file mapping (matches teacher.CASE_FILES and powermcp.CASE_FILES).
CASE_FILES: dict[str, str] = {
    "snem-sa-2000": "data/snemSA.m",
    "snem-1803": "data/snem1803.m",
}

# Wall-clock bound for one candidate execution. A correct script finishes in
# a few seconds (the solver is numba-warm); anything slower is treated as
# hung and quarantined rather than let it stall the batch.
EXEC_TIMEOUT_S: float = 180.0

# Tolerances for the independent check. Voltage/loading fields are compared
# with a small absolute tolerance (the solver is deterministic, so a correct
# answer reproduces the reference to far better than this); iteration counts
# and violation counts must match exactly.
VOLTAGE_FIELD_ATOL: float = 1e-3
LOADING_FIELD_ATOL: float = 1e-3

# The canonical pf-report scaffold's complete claim schema. Unknown fields
# cannot carry unchecked conclusions alongside an otherwise correct result.
REPORT_FIELDS: frozenset[str] = frozenset({
    "converged", "iterations", "min_bus_voltage_pu", "max_bus_voltage_pu",
    "max_line_loading_percent", "voltage_violations",
})

FIT_SUBFAMILIES: frozenset[str] = frozenset(
    {"pf-load-scale-fit", "pf-line-rating-fit", "pf-gen-dispatch-fit"}
)


@dataclass
class FilterResult:
    """Outcome of executing + independently checking one candidate."""

    status: str  # accepted / rejected / quarantined
    answer: Any | None
    diagnostics: list[str] = field(default_factory=list)
    exec_ok: bool = False
    exec_returncode: int | None = None
    exec_wall_s: float = 0.0
    exec_stdout: str = ""
    exec_stderr: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def is_accepted(self) -> bool:
        return self.status == "accepted"


def execute_candidate(code: str) -> dict[str, Any]:
    """Run `code` in a fresh subprocess of the current venv.

    The script is written to a temp file and run with cwd=REPO_ROOT so its
    relative `data/...` path resolves. The subprocess uses sys.executable so
    it runs in the SAME venv as the driver (pandapower/powerio available).
    """
    with tempfile.TemporaryDirectory(prefix="wattson-m05-") as tmp:
        script = Path(tmp) / "candidate.py"
        script.write_text(code, encoding="utf-8")
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=EXEC_TIMEOUT_S,
                check=False,
            )
            wall = time.monotonic() - started
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "wall_s": wall,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            wall = time.monotonic() - started
            return {
                "ok": False,
                "returncode": None,
                "stdout": (exc.stdout or b"").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                "stderr": (exc.stderr or b"").decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or ""),
                "wall_s": wall,
                "timed_out": True,
            }


def parse_answer(stdout: str) -> Any | None:
    """Enforce the scaffold's single JSON answer, without hidden extra claims.

    Reject duplicate keys, non-finite numbers, multiple answers, and prose
    surrounding an answer instead of silently selecting a convenient line.
    """
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """Reject ambiguous duplicate keys rather than overwriting earlier claims."""
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    try:
        obj = json.loads(lines[0], object_pairs_hook=unique_object)
        json.dumps(obj, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None
    return obj["answer"] if isinstance(obj, dict) and set(obj) == {"answer"} else None


def _parse_element(element: str) -> tuple[str, int, int | None]:
    """Parse 'bus <id>' / 'line <id>' / 'generator <id> (transformer <id>)'.

    Returns (kind, primary_id, secondary_id) where kind is one of
    {'bus','line','gen'} and secondary_id is the transformer id for 'gen'.
    """
    m = re.match(r"bus\s+(\d+)", element)
    if m:
        return "bus", int(m.group(1)), None
    m = re.match(r"line\s+(\d+)", element)
    if m:
        return "line", int(m.group(1)), None
    m = re.match(r"generator\s+(\d+)\s*\(transformer\s+(\d+)\)", element)
    if m:
        return "gen", int(m.group(1)), int(m.group(2))
    raise ValueError(f"unrecognised element grammar: {element!r}")


# Cache of base networks per case. pm._load_network_object is expensive
# (~40s for snem-sa-2000: powerio parse + gridfit.load_case), and the filter
# re-solves every candidate against the same base. The M04 modifiers deepcopy
# the base before mutating it, so a shared cached base is safe. Caching cuts
# ~40s off every filter re-solve (the dominant hidden cost in a batch run).
_BASE_NET_CACHE: dict[str, Any] = {}


def _get_base_net(case: str) -> Any:
    """Return the cached base network for a case, loading it on first use."""
    if case not in _BASE_NET_CACHE:
        _BASE_NET_CACHE[case] = pm._load_network_object(case)[0]
    return _BASE_NET_CACHE[case]


def _check_fit(example: dict[str, Any], answer: Any) -> tuple[bool, dict[str, Any], list[str]]:
    """Independently re-solve a fit task at the candidate's scale_factor."""
    inputs = example["task"]["inputs"]
    subfamily = example["subfamily"]
    case = inputs["case"]
    element = inputs["element"]
    target = float(inputs["target"])
    tolerance = float(inputs["tolerance"])
    scale_lo = float(inputs["scale_lo"])
    scale_hi = float(inputs["scale_hi"])

    diags: list[str] = []
    if not isinstance(answer, dict) or set(answer) != {"scale_factor"}:
        return False, {}, ["answer must be an object with 'scale_factor'"]
    value = answer["scale_factor"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, {}, ["scale_factor must be a finite JSON number"]
    try:
        s = float(value)
    except OverflowError:
        return False, {}, ["scale_factor exceeds finite floating-point range"]
    if not math.isfinite(s) or not scale_lo <= s <= scale_hi:
        return False, {}, [f"scale_factor {s} outside finite interval [{scale_lo}, {scale_hi}]"]

    _kind, primary, secondary = _parse_element(element)
    base_net = _get_base_net(case)
    if subfamily == "pf-load-scale-fit":
        modifier = gen._modifier_load_scale
        reader = gen._reader_bus_voltage(primary)
    elif subfamily == "pf-line-rating-fit":
        modifier = gen._make_modifier_line_rating(primary)
        reader = gen._reader_line_loading(primary)
    else:  # pf-gen-dispatch-fit
        modifier = gen._make_modifier_gen_dispatch(primary)
        reader = gen._reader_trafo_loading(secondary)

    # An N-1 fit is checked on the network it was posed on (backlog 0017).
    net = gen.with_load_level(gen.with_outage(modifier, inputs.get("contingency_line")), inputs.get("load_level"))(base_net, s)
    try:
        iters, vm_min, vm_max, viol = gen._solve(net)
    except pp.LoadflowNotConverged:
        diags.append(f"power flow did not converge at scale_factor={s}")
        return False, {"scale_factor": s, "converged": False}, diags
    except Exception as exc:  # noqa: BLE001 -- backend failures become rejected solver outcomes
        diags.append(f"solver raised {type(exc).__name__}: {exc} at s={s}")
        return False, {"scale_factor": s, "converged": False}, diags

    metric = reader(net)
    residual = abs(metric - target)
    within = residual <= tolerance
    evidence = {
        "scale_factor": s,
        "metric": metric,
        "target": target,
        "residual": residual,
        "within_tolerance": within,
        "converged": True,
        "iterations": iters,
        "vm_min_pu": vm_min,
        "vm_max_pu": vm_max,
        "voltage_violations": viol,
    }
    if not within:
        diags.append(
            f"independent check: |metric - target| = {residual:.6f} > "
            f"tolerance {tolerance} (metric={metric:.6f}, target={target:.6f})"
        )
    return within, evidence, diags


def _check_pf_report(example: dict[str, Any], answer: Any) -> tuple[bool, dict[str, Any], list[str]]:
    """Independently re-solve a pf-report task and compare the fields."""
    inputs = example["task"]["inputs"]
    case = inputs["case"]
    contingency = inputs.get("contingency_line")

    diags: list[str] = []
    if not isinstance(answer, dict):
        return False, {}, ["answer must be an object"]
    if not isinstance(answer.get("converged"), bool):
        return False, {}, ["converged must be an explicit JSON boolean"]
    if set(answer) - REPORT_FIELDS:
        return False, {}, ["report contains unsupported additional claims"]
    try:
        json.dumps(answer, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return False, {}, ["report contains non-finite or non-JSON claims"]

    base_net = _get_base_net(case)
    net = copy.deepcopy(base_net)
    if contingency is not None:
        net.line = net.line.drop(index=int(contingency))

    try:
        iters, vm_min, vm_max, viol = gen._solve(net)
    except pp.LoadflowNotConverged:
        converged, iters, vm_min, vm_max, viol = False, None, None, None, None
    except Exception:  # noqa: BLE001 -- backend failures must not invent measurements
        converged, iters, vm_min, vm_max, viol = False, None, None, None, None
    else:
        converged = True
    max_loading = gen._max_line_loading(net) if converged else None

    reference = {
        "converged": converged,
        "iterations": iters,
        "min_bus_voltage_pu": vm_min,
        "max_bus_voltage_pu": vm_max,
        "max_line_loading_percent": max_loading,
        "voltage_violations": viol,
    }

    def close(key: str, atol: float) -> bool:
        ref = reference[key]
        cand = answer.get(key)
        if isinstance(cand, bool) or not isinstance(cand, (int, float)):
            return False
        try:
            return abs(float(cand) - float(ref)) <= atol
        except (TypeError, ValueError, OverflowError):
            return False

    ok = True
    if answer["converged"] != converged:
        ok = False
        diags.append(f"converged mismatch: candidate={answer.get('converged')} reference={converged}")
    if converged:
        if isinstance(answer.get("iterations"), bool) or answer.get("iterations") != iters:
            ok = False
            diags.append(f"iterations mismatch: candidate={answer.get('iterations')} reference={iters}")
        for key, atol in (
            ("min_bus_voltage_pu", VOLTAGE_FIELD_ATOL),
            ("max_bus_voltage_pu", VOLTAGE_FIELD_ATOL),
            ("max_line_loading_percent", LOADING_FIELD_ATOL),
        ):
            if not close(key, atol):
                ok = False
                diags.append(f"{key} mismatch: candidate={answer.get(key)} reference={reference[key]}")
        if isinstance(answer.get("voltage_violations"), bool) or answer.get("voltage_violations") != viol:
            ok = False
            diags.append(f"voltage_violations mismatch: candidate={answer.get('voltage_violations')} reference={viol}")
    elif any(answer.get(key) is not None for key in reference if key != "converged"):
        ok = False
        diags.append("failed solve cannot support numerical measurements")

    evidence = {"reference": reference, "candidate": answer}
    return ok, evidence, diags


def _check_mcp_trajectory(example: dict[str, Any], answer: Any) -> tuple[bool, dict[str, Any], list[str]]:
    """Replay task-declared calls and validate the candidate's entire transcript.

    No stored reference answer is consulted. Each claimed status, raw result,
    and evidence record must match independent execution. This proves replay
    coherence; a separate trusted runner trace is required to prove that the
    candidate itself executed a solver rather than copying a known result.
    """
    inputs = example["task"]["inputs"]
    case = inputs["case"]
    if not isinstance(answer, dict) or set(answer) != {"trajectory"}:
        return False, {}, ["answer must be an object with 'trajectory'"]
    trajectory = answer["trajectory"]
    calls = [
        ("load_network", {"network_id": case}),
        ("run_power_flow", {
            "network_id": case, "method": "nr",
            "max_iter": inputs["max_iter"], "check_limits": inputs["check_limits"],
        }),
    ]
    if not isinstance(trajectory, list) or len(trajectory) != len(calls):
        return False, {}, ["trajectory must contain the two task-declared calls in order"]

    def canonical(value: Any) -> str:
        """Preserve JSON types so true cannot masquerade as numeric one."""
        return json.dumps(value, sort_keys=True, allow_nan=False)

    def same_result(candidate: Any, actual: Any) -> bool:
        """Check the raw tool schema exactly, allowing documented metric rounding."""
        if not isinstance(candidate, dict) or set(candidate) != set(actual):
            return False
        tolerances = {
            "min_bus_voltage_pu": VOLTAGE_FIELD_ATOL,
            "max_bus_voltage_pu": VOLTAGE_FIELD_ATOL,
            "max_line_loading_percent": LOADING_FIELD_ATOL,
        }
        for key, reference in actual.items():
            value = candidate[key]
            if key in tolerances:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return False
                if not math.isclose(value, reference, rel_tol=0.0, abs_tol=tolerances[key]):
                    return False
            elif canonical(value) != canonical(reference):
                return False
        return True

    def same_evidence(candidate: Any, actual: Any, payload: Any) -> bool:
        """Check input/status/validity metadata and the candidate payload's own hash."""
        if actual is None:
            return candidate is None
        if not isinstance(candidate, dict) or set(candidate) != set(actual):
            return False
        candidate_metadata = {key: value for key, value in candidate.items() if key != "result_hash"}
        actual_metadata = {key: value for key, value in actual.items() if key != "result_hash"}
        digest = "sha256:" + hashlib.sha256(canonical(payload).encode()).hexdigest()
        return candidate["result_hash"] == digest and canonical(candidate_metadata) == canonical(actual_metadata)

    required_fields = {"tool", "args", "status", "result", "evidence"}
    for step, (tool, arguments) in zip(trajectory, calls, strict=True):
        if not isinstance(step, dict) or set(step) != required_fields:
            return False, {}, ["each step requires tool, args, status, result, and evidence"]
        try:
            canonical(step)
            valid = step["tool"] == tool and canonical(step["args"]) == canonical(arguments)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            return False, {}, [f"candidate tool/arguments differ from task-declared {tool}"]

    observed = []
    try:
        mcp = pm.PowerMCP()
        for tool, arguments in calls:
            result = mcp.execute_tool(tool, arguments)
            observed.append({
                "tool": tool, "args": arguments, "status": result.status.value,
                "result": result.data if result.data else {"error": result.error},
                "evidence": result.evidence,
            })
    except Exception as exc:  # noqa: BLE001 -- independent backend failure rejects the claim
        return False, {"observed": observed}, [f"independent replay raised {type(exc).__name__}: {exc}"]
    try:
        serialized = canonical(observed)
    except (TypeError, ValueError):
        return False, {"backend_payload_json_valid": False}, ["independent replay returned non-finite or non-JSON data"]
    diags = []
    for index, (candidate, actual) in enumerate(zip(trajectory, observed, strict=True)):
        try:
            checks = {
                "status": canonical(candidate["status"]) == canonical(actual["status"]),
                "result": same_result(candidate["result"], actual["result"]),
                "evidence": same_evidence(candidate["evidence"], actual["evidence"], candidate["result"]),
            }
        except (TypeError, ValueError, OverflowError):
            checks = {"JSON payload": False}
        diags.extend(
            f"step {index + 1} {field_name} differs from independent replay"
            for field_name, matches in checks.items() if not matches
        )
    evidence = {
        "observed": observed,
        "n_steps": len(trajectory),
        "independent_status": observed[-1]["status"],
        "candidate_status": trajectory[-1]["status"],
        "replay_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        "matches": not diags,
        "candidate_execution_proven": False,
    }
    return not diags, evidence, diags


def filter_example(example: dict[str, Any], code: str) -> FilterResult:
    """Execute a candidate and independently verify it.

    Args:
        example: an M04 training example (task + expected + subfamily).
        code: the teacher's candidate Python script.

    Returns:
        A FilterResult with status in {accepted, rejected, quarantined}, the
        parsed answer, diagnostics, and the independent-check evidence.
    """
    exec_info = execute_candidate(code)
    if not exec_info["ok"]:
        reason = "timeout" if exec_info["timed_out"] else f"exit {exec_info['returncode']}"
        return FilterResult(
            status="quarantined",
            answer=None,
            diagnostics=[
                f"execution failed ({reason}) after {exec_info['wall_s']:.2f}s",
                exec_info["stderr"].strip()[-2000:],
            ],
            exec_ok=False,
            exec_returncode=exec_info["returncode"],
            exec_wall_s=exec_info["wall_s"],
            exec_stdout=exec_info["stdout"],
            exec_stderr=exec_info["stderr"],
        )

    answer = parse_answer(exec_info["stdout"])
    if answer is None:
        return FilterResult(
            status="quarantined",
            answer=None,
            diagnostics=[
                "no parseable JSON answer on stdout",
                exec_info["stdout"].strip()[-2000:],
            ],
            exec_ok=True,
            exec_returncode=exec_info["returncode"],
            exec_wall_s=exec_info["wall_s"],
            exec_stdout=exec_info["stdout"],
            exec_stderr=exec_info["stderr"],
        )

    subfamily = example["subfamily"]
    if subfamily in FIT_SUBFAMILIES:
        ok, evidence, diags = _check_fit(example, answer)
    elif subfamily == "pf-report":
        ok, evidence, diags = _check_pf_report(example, answer)
    elif subfamily == "mcp-trajectory":
        ok, evidence, diags = _check_mcp_trajectory(example, answer)
    else:
        ok, evidence, diags = False, {}, [f"unknown subfamily {subfamily!r}"]

    return FilterResult(
        status="accepted" if ok else "rejected",
        answer=answer,
        diagnostics=diags,
        exec_ok=True,
        exec_returncode=exec_info["returncode"],
        exec_wall_s=exec_info["wall_s"],
        exec_stdout=exec_info["stdout"],
        exec_stderr=exec_info["stderr"],
        evidence=evidence,
    )
