"""``wattson doctor``: say, check by check, whether this environment can run the labs, serve a model, and evaluate it.

Every check has a name, a status (PASS, WARN, FAIL, SKIP), the evidence, how long it took, and what to do about it. A check that
needs something that was not asked for (a model server, a GPU) is SKIP or WARN, not FAIL: the labs run without either. The exit
code is 1 only if some check FAILs, so the command is usable as a container healthcheck and as a gate in CI.
"""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path

from wattson import datacheck, fetch, log, models

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
MIN_PYTHON: tuple[int, int] = (3, 14)
# A study on the smallest bundled network; if this takes longer something is wrong with the numerical stack.
SOLVER_NETWORK: str = "ieee39"
SOLVER_SLOW_S: float = 30.0
HTTP_TIMEOUT_S: float = 8.0
EXPECTED_TOOLS: frozenset[str] = frozenset({"run_study_trial", "run_python"})
_LOG = log.get("doctor")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    seconds: float = 0.0
    remedy: str = ""


def _timed(name: str, function: Callable[[], tuple[str, str, str]]) -> Check:
    started = time.monotonic()
    try:
        status, detail, remedy = function()
    except Exception as error:  # a crashing check is itself a finding, never a crash of the doctor
        status, detail, remedy = FAIL, f"{type(error).__name__}: {error}", "see the log line for the traceback"
        _LOG.exception("check.crashed", extra={"check": name})
    check = Check(name, status, detail, round(time.monotonic() - started, 2), remedy)
    _LOG.info("check", extra={"check": name, "status": status, "seconds": check.seconds, "detail": detail})
    return check


def python_version() -> tuple[str, str, str]:
    have = sys.version_info[:2]
    ok = have >= MIN_PYTHON
    return (PASS if ok else FAIL, f"python {platform.python_version()} on {platform.machine()}",
            "" if ok else f"needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; use the container")


def pinned_dependencies() -> tuple[str, str, str]:
    """Every ``==`` pin in the package metadata must equal what is installed (the numbers depend on them: scipy changed floats)."""
    wrong: list[str] = []
    count = 0
    for requirement in metadata.requires("wattson") or []:
        if "extra ==" in requirement or ";" in requirement or "==" not in requirement:
            continue
        name, _, version = requirement.partition("==")
        count += 1
        try:
            installed = metadata.version(name.strip())
        except metadata.PackageNotFoundError:
            wrong.append(f"{name.strip()} not installed")
            continue
        if installed != version.strip():
            wrong.append(f"{name.strip()} {installed} != {version.strip()}")
    return (FAIL, "; ".join(wrong), "rebuild the environment from uv.lock") if wrong else (PASS, f"{count} pinned packages match", "")


def data_integrity() -> tuple[str, str, str]:
    root = datacheck.data_dir()
    checked, problems = datacheck.verify(root)
    if problems:
        return FAIL, f"{root}: {len(problems)} problem(s), first: {problems[0]}", "restore data/ (git checkout data) or re-pull the image"
    return PASS, f"{checked} files in {root} match MANIFEST.json", ""


def case_files() -> tuple[str, str, str]:
    state = fetch.status()
    bad = {name: s for name, s in state.items() if s != "ok"}
    if bad:
        return WARN, f"{fetch.case_dir()}: {bad}", "run `wattson fetch-data` (needs network once; the labs need these files)"
    return PASS, f"{len(state)} CSIRO case files verified in {fetch.case_dir()}", ""


def solver() -> tuple[str, str, str]:
    from wattson.inference.studies import run_study_trial

    started = time.monotonic()
    result = run_study_trial(network_id=SOLVER_NETWORK, operation="none", max_iter=50)
    seconds = time.monotonic() - started
    converged = bool((result.data or {}).get("converged"))
    if not converged:
        return FAIL, f"{SOLVER_NETWORK}: status={result.status} converged={converged}", "check pandapower/numba/scipy pins (`wattson doctor` pins check)"
    return (WARN if seconds > SOLVER_SLOW_S else PASS), f"{SOLVER_NETWORK} AC power flow converged in {seconds:.2f}s", "first call JIT-compiles; rerun" if seconds > SOLVER_SLOW_S else ""


async def _tool_server_round_trip(networks: list[str]) -> tuple[list[str], dict[str, str]]:
    """List the tools, then solve each network THROUGH the MCP server: a process started with a different environment than ours."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from wattson.mcp_env import server_environment

    parameters = StdioServerParameters(command=sys.executable, args=["-m", "wattson.inference.mcp_server", "--study-tools", "--python-tools"],
                                       env=server_environment())
    outcomes: dict[str, str] = {}
    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        names = sorted(tool.name for tool in (await session.list_tools()).tools)
        for network in networks:
            reply = await session.call_tool("run_study_trial", {"network_id": network, "operation": "none", "max_iter": 50})
            body = json.loads(reply.content[0].text)
            outcomes[network] = "converged" if (body.get("data") or {}).get("converged") else (body.get("error") or body.get("status") or "failed")
    return names, outcomes


def tool_server() -> tuple[str, str, str]:
    """Regression: the MCP library does not pass our environment on, so WATTSON_CASE_DIR was lost and every case-file network failed
    inside the server while this check, which only listed tools, still passed."""
    networks = [SOLVER_NETWORK] + (["snem-sa-2000"] if all(v == "ok" for v in fetch.status().values()) else [])
    names, outcomes = asyncio.run(asyncio.wait_for(_tool_server_round_trip(networks), timeout=120))
    missing = EXPECTED_TOOLS - set(names)
    if missing:
        return FAIL, f"tools {names}; missing {sorted(missing)}", "the MCP server did not expose the Python shell"
    failed = {n: o for n, o in outcomes.items() if o != "converged"}
    if failed:
        return FAIL, f"the MCP server could not solve: {failed}", "the server process lacks WATTSON_CASE_DIR, or the case files are not where it looks"
    return PASS, f"MCP server over stdio exposes {names} and solved {sorted(outcomes)}", ""


def gpu() -> tuple[str, str, str]:
    if not shutil.which("nvidia-smi"):
        return WARN, "no nvidia-smi in this container", "fine for the labs; the model-server container needs the GPU (CDI: nvidia.com/gpu=all)"
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
    return (PASS, out.stdout.strip().replace("\n", " | "), "") if out.returncode == 0 else (WARN, (out.stderr or out.stdout).strip()[:200], "GPU not visible")


def model_directory(path: Path) -> Callable[[], tuple[str, str, str]]:
    def check() -> tuple[str, str, str]:
        if not path.exists():
            return WARN, f"{path} does not exist", "run `wattson models pull <repo> --dest <dir>`"
        checked, problems = models.verify(path)
        if problems:
            return FAIL, f"{path}: {problems[:3]}", "re-pull with `wattson models pull ... --force`"
        return PASS, f"{checked} files verified against hash_manifest.json in {path}", ""

    return check


def model_endpoint(base_url: str, deep: bool) -> Callable[[], tuple[str, str, str]]:
    def check() -> tuple[str, str, str]:
        import httpx

        try:
            listed = httpx.get(f"{base_url.rstrip('/')}/models", timeout=HTTP_TIMEOUT_S).json()
        except Exception as error:
            return FAIL, f"{base_url}: {type(error).__name__}: {error}", "is the model-server container up? `just logs vllm`"
        ids = [m["id"] for m in listed.get("data", [])]
        if not ids:
            return FAIL, f"{base_url} lists no models", "the server started without a model; see its log"
        detail = f"{base_url} serves {ids}"
        if deep:
            started = time.monotonic()
            reply = httpx.post(f"{base_url.rstrip('/')}/chat/completions", timeout=120, json={
                "model": ids[0], "max_tokens": 16, "temperature": 0, "messages": [{"role": "user", "content": "Reply with the single word OK."}]})
            if reply.status_code != 200:
                return FAIL, f"{detail}; completion HTTP {reply.status_code}: {reply.text[:160]}", "see the model-server log"
            detail += f"; completion ok in {time.monotonic() - started:.1f}s"
        return PASS, detail, ""

    return check


def run(base_url: str | None = None, model_dir: Path | None = None, deep: bool = False) -> list[Check]:
    """Run every applicable check, in dependency order, and return the results."""
    plan: list[tuple[str, Callable[[], tuple[str, str, str]]]] = [
        ("python", python_version), ("pins", pinned_dependencies), ("data", data_integrity), ("cases", case_files),
        ("solver", solver), ("tool-server", tool_server), ("gpu", gpu)]
    results = [_timed(name, function) for name, function in plan]
    results.append(_timed("model-files", model_directory(model_dir)) if model_dir else Check("model-files", SKIP, "no --model-dir given"))
    results.append(_timed("model-endpoint", model_endpoint(base_url, deep)) if base_url else Check("model-endpoint", SKIP, "no --base-url given"))
    return results


def render(results: list[Check]) -> str:
    width = max(len(c.name) for c in results)
    lines = [f"{c.status:<4}  {c.name:<{width}}  {c.detail}" + (f"   [{c.seconds:.1f}s]" if c.seconds >= 0.1 else "") + (f"\n{'':<{width + 8}}-> {c.remedy}" if c.remedy and c.status != PASS else "") for c in results]
    counts = {s: sum(c.status == s for c in results) for s in (PASS, WARN, FAIL, SKIP)}
    return "\n".join(lines + ["", "  ".join(f"{k}={v}" for k, v in counts.items())])


def to_json(results: list[Check]) -> str:
    return json.dumps([asdict(c) for c in results], indent=2)
