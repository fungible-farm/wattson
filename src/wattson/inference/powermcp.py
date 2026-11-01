"""PowerMCP canonical execution contract for the NEM PowerCoder mission (M02).

This module is the in-process canonical execution layer: it runs REAL
pandapower Newton-Raphson AC power flow (``pandapower.runpp``) on the CSIRO
Synthetic-NEM case files and returns a 4-state, solver-grounded result plus a
canonical execution-evidence record (MISSION.md M02 "Canonical execution
evidence").

4-state status contract (explicit, mutually exclusive):

- ``EXECUTION_FAILURE``     -- the solver could not be run at all (unknown
  case, unreadable file, a numerical breakdown such as the divide-by-zero the
  snem1803 case exhibits in pandapower 3.5.4).
- ``NON_CONVERGENCE``       -- the solver ran but did not converge within the
  iteration budget (pandapower raises ``pp.LoadflowNotConverged``).
- ``CONSTRAINT_VIOLATION``  -- the solver converged but the operating point
  violates a declared physical limit (bus voltage outside the screening band).
- ``SUCCESS``               -- the solver converged and no declared limit is
  violated.

Convergence is NOT the same thing as physical validity: a converged solution
with 39 voltage violations is ``CONSTRAINT_VIOLATION``, not ``SUCCESS``
(MISSION.md invariant: "Never treat a successfully converged simulation as
automatically physically valid").

Named stand-ins (per AGENTS.md "Sandbox stand-ins must be named"):

- The two small fixture networks (``fixture-2bus-ok``,
  ``fixture-2bus-nonconverged``) exist solely to exercise the ``SUCCESS`` and
  ``NON_CONVERGENCE`` branches deterministically; the real CSIRO cases cover
  the other two branches (snem-sa-2000 -> CONSTRAINT_VIOLATION, snem-1803 ->
  EXECUTION_FAILURE as measured). They are pandapower-built networks, not
  hand-written physics.
- The streamable-HTTP PowerMCP pod (``kube/powermcp_serve_http.py``) is the
  transport-level MCP surface; this module is the same contract in-process so
  training-data consumers never depend on the pod being up.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import pandapower as pp

# Repo root = two levels up from this file (wattson/inference/ -> repo root).
# Case files live in data/ (gitignored, fetched by scripts/fetch_csiro_nem_data.py). WATTSON_CASE_DIR moves them: the container
# fetches them into a mounted volume on first use instead of shipping a copy of the CSIRO files in the image.
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
CASE_DIR_ENV: str = "WATTSON_CASE_DIR"


def case_path(network_id: str) -> Path:
    """Where the case file for ``network_id`` is expected (it may not exist yet)."""
    directory = os.environ.get(CASE_DIR_ENV)
    name = Path(CASE_FILES[network_id]).name
    return Path(directory) / name if directory else REPO_ROOT / CASE_FILES[network_id]

# Voltage screening band, per-unit. Source: ANSI C84.1 / IEEE 399 "A" range
# (0.95-1.05 pu), the standard screening band for bus-voltage compliance and
# the same band AEMO applies at NEM connection points. Chosen for M02 as the
# declared physical limit for the run_power_flow tool; it is a *screening*
# limit, not a claim that every internal bus of a 2000-bus system must sit in
# it (the snem-sa-2000 base case has 39 buses outside it -- a property of the
# synthetic data, reported as a violation, not hidden).
VOLTAGE_LIMIT_LOW_PU: float = 0.95
VOLTAGE_LIMIT_HIGH_PU: float = 1.05

# network_id -> case file (relative to repo root). The CSIRO
# Synthetic-NEM-2000-Bus dataset, checksum-verified by
# scripts/fetch_csiro_nem_data.py.
CASE_FILES: dict[str, str] = {
    "snem-sa-2000": "data/snemSA.m",
    "snem-1803": "data/snem1803.m",
}


class ExecutionStatus(str, Enum):
    """Canonical execution status codes (see module docstring for contract)."""

    SUCCESS = "success"
    NON_CONVERGENCE = "non_convergence"
    CONSTRAINT_VIOLATION = "constraint_violation"
    EXECUTION_FAILURE = "execution_failure"


@dataclass
class PowerMCPResult:
    """Standardized result from a power systems tool execution.

    Attributes:
        status: One of the four canonical ExecutionStatus values.
        data: Structured, solver-derived result data (voltages, loadings,
            violations). Never contains fabricated values -- every number here
            came out of a real pandapower run.
        error: Human-readable error text for EXECUTION_FAILURE /
            NON_CONVERGENCE results.
        execution_time_s: Wall-clock seconds for the solver call.
        evidence: Canonical execution-evidence record (MISSION.md M02 schema).
    """

    status: ExecutionStatus
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    execution_time_s: float | None = None
    evidence: dict[str, Any] | None = None


def _sha256_bytes(b: bytes) -> str:
    """Return the hex sha256 of a byte string, prefixed for readability."""
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file's bytes (streamed, prefixed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _runpp_counted(net: pp.pandapowerNet, **kwargs: Any) -> int | None:
    """Run the canonical ``pp.runpp`` and capture the NR iteration count.

    pandapower 3.5.4's public ``runpp`` API does not expose the iteration
    count (``net.converged`` is a bare bool), but the underlying
    Newton-Raphson solver ``pandapower.pf.run_newton_raphson_pf.newtonpf``
    returns it as its third value. This wrapper temporarily counts that
    return value, so the iteration count in the evidence record is the
    solver's own number, not an estimate.

    Args:
        net: A pandapower network.
        **kwargs: Forwarded verbatim to ``pp.runpp``.

    Returns:
        The iteration count reported by the solver, or None if the solver
        raised before returning (non-convergence / execution failure).

    A ``FloatingPointError`` from the default DC pre-solve (zero-reactance branches,
    snem-1803) is retried once with ``init="flat"`` unless the caller chose ``init``.

    Raises:
        Whatever ``pp.runpp`` raises (LoadflowNotConverged, etc.).
    """
    import pandapower.pf.run_newton_raphson_pf as nr

    original = nr.newtonpf
    state: dict[str, int | None] = {"it": None}

    def counting_newtonpf(*a: Any, **kw: Any) -> Any:
        out = original(*a, **kw)
        state["it"] = out[2]
        return out

    nr.newtonpf = counting_newtonpf
    try:
        try:
            pp.runpp(net, **kwargs)
        except FloatingPointError:
            # snem-1803 has zero-reactance branches; pandapower's default DC pre-solve
            # (init="auto" -> "dc") divides by x and raises before Newton-Raphson starts.
            # A flat start skips that pre-solve (Lab 2's documented deviation for the same
            # case). Only this failure is retried, so a case that solves is unaffected.
            if "init" in kwargs:
                raise
            pp.runpp(net, init="flat", **kwargs)
    finally:
        nr.newtonpf = original
    return state["it"]


def _make_fixture_2bus_ok() -> pp.pandapowerNet:
    """Build the deterministic 2-bus SUCCESS-branch fixture.

    A 2-bus, 1-line, 1-generator 220 kV system: a 40 km transmission line
    (r/x per km in the typical 220 kV range, ~0.05 ohm/km R, ~0.3 ohm/km X)
    carrying a 20 MW / 5 Mvar load. On the 100 MVA base the line is X ~ 0.025
    pu, so the expected voltage drop is ~0.005 pu (load bus vm ~ 0.995 pu,
    inside the 0.95-1.05 pu screening band) and Newton-Raphson converges in a
    few iterations. Named stand-in: exists only to exercise the SUCCESS
    branch; it is not a claim about any real network.
    """
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=220.0)
    b1 = pp.create_bus(net, vn_kv=220.0)
    pp.create_line_from_parameters(
        net,
        from_bus=b0,
        to_bus=b1,
        length_km=40.0,
        r_ohm_per_km=0.05,
        x_ohm_per_km=0.3,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
        s_mva=500.0,
    )
    pp.create_ext_grid(net, b0, vm_pu=1.0)
    pp.create_load(net, b1, p_mw=20.0, q_mvar=5.0)
    return net


def _make_fixture_2bus_nonconverged() -> pp.pandapowerNet:
    """Build the deterministic 2-bus NON_CONVERGENCE-branch fixture.

    A 2-bus 11 kV system with a deliberately weak 40 km line (X ~ 11.6 pu on
    the 100 MVA base; transfer capability P_max ~ V^2/X ~ 8.6 MW) carrying a
    20 MW load -- ~2.3x the transfer capability -- so the Newton-Raphson
    iteration diverges and pandapower raises ``pp.LoadflowNotConverged``.
    Named stand-in: exists only to exercise the NON_CONVERGENCE branch
    deterministically.
    """
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=11.0)
    b1 = pp.create_bus(net, vn_kv=11.0)
    pp.create_line_from_parameters(
        net,
        from_bus=b0,
        to_bus=b1,
        length_km=40.0,
        r_ohm_per_km=0.126,
        x_ohm_per_km=0.35,
        c_nf_per_km=0.0,
        max_i_ka=2.0,
        s_mva=250.0,
    )
    pp.create_ext_grid(net, b0, vm_pu=1.0)
    pp.create_load(net, b1, p_mw=20.0, q_mvar=5.0)
    return net


# network_id -> builder for the in-memory fixture networks.
FIXTURE_BUILDERS: dict[str, Callable[[], pp.pandapowerNet]] = {
    "fixture-2bus-ok": _make_fixture_2bus_ok,
    "fixture-2bus-nonconverged": _make_fixture_2bus_nonconverged,
}

def _standard_case(name: str) -> Callable[[], pp.pandapowerNet]:
    """A pandapower built-in test case (BSD-3, shipped with the pinned pandapower)."""
    import pandapower.networks as pn

    builder = getattr(pn, name)

    def build() -> pp.pandapowerNet:
        return builder()

    build.__qualname__ = f"pandapower.networks.{name}"
    return build


# Standard test networks served alongside the CSIRO cases (backlog 0017, 2026-10-24): G3's
# procedural pool needs networks other than the two NEM cases, so that one of those can stay
# held out. All solve with the tool's own Newton-Raphson in 3-4 iterations from the default start.
# Training and evaluation roles are fixed in wattson.datasets.procedural.
STANDARD_CASES: dict[str, Callable[[], pp.pandapowerNet]] = {
    "ieee39": _standard_case("case39"),
    "ieee118": _standard_case("case118"),
    "pegase89": _standard_case("case89pegase"),
}
# Every network the tools serve, in schema enum order (the Rust IR's NETWORKS must equal it).
SERVED_NETWORKS: tuple[str, ...] = (*CASE_FILES, *STANDARD_CASES, *FIXTURE_BUILDERS)


def _load_network_object(network_id: str) -> tuple[pp.pandapowerNet, str]:
    """Load a network by ID.

    Returns:
        (net, input_hash) where input_hash is the sha256 of the case file
        bytes for file-backed cases, or a deterministic descriptor hash for
        fixture networks.

    Raises:
        FileNotFoundError: for a known case ID whose file is missing.
        ValueError: for an unknown network_id.
    """
    if network_id in CASE_FILES:
        path = case_path(network_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Case file {path} not found -- run scripts/fetch_csiro_nem_data.py"
            )
        import powerio

        powerio.parse_file(str(path))
        # gridfit.load_case is the repo's canonical powerio -> pandapower loader; reuse it rather than re-deriving.
        from wattson.gridfit import load_case

        net, _notes = load_case(str(path))
        return net, _sha256_file(path)
    if network_id in STANDARD_CASES:
        net = STANDARD_CASES[network_id]()
        # The network's own serialised data is its identity, so a pandapower upgrade that
        # changed a case would change its hash.
        return net, _sha256_bytes(pp.to_json(net).encode("utf-8"))
    if network_id in FIXTURE_BUILDERS:
        builder = FIXTURE_BUILDERS[network_id]
        net = builder()
        # Deterministic descriptor: the builder's fully-qualified name is the
        # fixture's identity (its parameters are module-level constants).
        return net, _sha256_bytes(builder.__qualname__.encode("utf-8"))
    raise ValueError(f"Unknown network ID: {network_id}")


class PowerMCP:
    """PowerMCP canonical tool execution interface (in-process).

    Every public method returns a ``PowerMCPResult`` whose ``status`` is one
    of the four canonical states and whose ``evidence`` field (when the solver
    actually ran) follows the MISSION.md M02 canonical execution-evidence
    schema.
    """

    def load_network(self, network_id: str) -> PowerMCPResult:
        """Load a power network case by identifier.

        Args:
            network_id: Case identifier (e.g. 'snem-sa-2000').

        Returns:
            PowerMCPResult with loaded network metadata, or
            EXECUTION_FAILURE for unknown IDs / unreadable files.
        """
        try:
            net, _input_hash = _load_network_object(network_id)
        except (ValueError, FileNotFoundError) as e:
            return PowerMCPResult(ExecutionStatus.EXECUTION_FAILURE, error=str(e))
        except Exception as e:  # noqa: BLE001 -- parser/backend failures are canonical tool outcomes
            return PowerMCPResult(
                ExecutionStatus.EXECUTION_FAILURE,
                error=f"{type(e).__name__}: {e}",
            )
        return PowerMCPResult(
            ExecutionStatus.SUCCESS,
            data={
                "network_id": network_id,
                "buses": len(net.bus),
                "branches": len(net.line) + len(net.trafo),
                "generators": len(net.gen) + len(net.ext_grid),
                "loads": len(net.load),
            },
        )

    def run_power_flow(
        self,
        network_id: str,
        method: str = "nr",
        max_iter: int = 50,
        check_limits: bool = True,
    ) -> PowerMCPResult:
        """Run AC power flow analysis on a network.

        Runs a real ``pandapower.runpp`` Newton-Raphson AC power flow
        (``method='nr'``). ``method='cg'`` is accepted for schema
        compatibility but the NR solver is what this contract is defined
        against (named stand-in: the method parameter is currently advisory).

        Args:
            network_id: Case identifier.
            method: 'nr' (Newton-Raphson; the real solver used) or 'cg'
                (accepted, mapped to nr -- see docstring).
            max_iter: Positive iteration budget passed as ``max_iteration``.
            check_limits: When True (default), a converged solution with bus
                voltages outside [VOLTAGE_LIMIT_LOW_PU, VOLTAGE_LIMIT_HIGH_PU]
                is reported as CONSTRAINT_VIOLATION. When False, convergence
                alone is SUCCESS (violations are still reported in data).

        Returns:
            PowerMCPResult with one of the four canonical statuses, solver
            measurements in data, and the canonical evidence record.
        """
        if isinstance(max_iter, bool) or not isinstance(max_iter, int) or max_iter <= 0:
            return PowerMCPResult(
                ExecutionStatus.EXECUTION_FAILURE,
                error="max_iter must be a positive integer",
            )
        if not isinstance(method, str) or method not in {"nr", "cg"} or not isinstance(check_limits, bool):
            return PowerMCPResult(
                ExecutionStatus.EXECUTION_FAILURE,
                error="method must be nr/cg and check_limits must be a boolean",
            )
        # --- execution-failure phase: load the network ---------------------
        try:
            net, input_hash = _load_network_object(network_id)
        except (ValueError, FileNotFoundError) as e:
            return PowerMCPResult(ExecutionStatus.EXECUTION_FAILURE, error=str(e))
        except Exception as e:  # noqa: BLE001 -- parser/backend failures are canonical tool outcomes
            return PowerMCPResult(
                ExecutionStatus.EXECUTION_FAILURE,
                error=f"{type(e).__name__}: {e}",
            )

        # --- solver phase ---------------------------------------------------
        t0 = time.monotonic()
        iterations: int | None = None
        try:
            iterations = _runpp_counted(net, numba=True, max_iteration=max_iter)
        except pp.LoadflowNotConverged as e:
            return PowerMCPResult(
                ExecutionStatus.NON_CONVERGENCE,
                error=str(e),
                execution_time_s=time.monotonic() - t0,
            )
        except Exception as e:  # noqa: BLE001 -- numerical backend failures must retain failure status
            # Includes the FloatingPointError (divide by zero) that snem1803
            # exhibits in pandapower 3.5.4 -- a numerical breakdown of the
            # solver run, i.e. an execution failure, not a clean
            # non-convergence.
            return PowerMCPResult(
                ExecutionStatus.EXECUTION_FAILURE,
                error=f"{type(e).__name__}: {e}",
                execution_time_s=time.monotonic() - t0,
            )
        elapsed = time.monotonic() - t0

        # --- measurement phase (real solver output only) --------------------
        vm = net.res_bus["vm_pu"]
        vm_min, vm_max = float(vm.min()), float(vm.max())
        violation_mask = (vm < VOLTAGE_LIMIT_LOW_PU) | (vm > VOLTAGE_LIMIT_HIGH_PU)
        violations = int(violation_mask.sum())
        violating_bus_ids = (
            [int(i) for i in net.bus.index[violation_mask].tolist()][:10]
            if violations
            else []
        )
        max_line_loading_pct = (
            float(net.res_line["loading_percent"].max()) if len(net.line) else 0.0
        )

        data: dict[str, Any] = {
            "status": "converged",
            "iterations": iterations,
            "min_bus_voltage_pu": vm_min,
            "max_bus_voltage_pu": vm_max,
            "max_line_loading_percent": max_line_loading_pct,
            "voltage_violations": violations,
            "violating_bus_ids": violating_bus_ids,
            "voltage_limit_pu": [VOLTAGE_LIMIT_LOW_PU, VOLTAGE_LIMIT_HIGH_PU],
        }

        if violations and check_limits:
            status = ExecutionStatus.CONSTRAINT_VIOLATION
            error = (
                f"Voltage limit violations on {violations} buses "
                f"(band {VOLTAGE_LIMIT_LOW_PU}-{VOLTAGE_LIMIT_HIGH_PU} pu)"
            )
        else:
            status = ExecutionStatus.SUCCESS
            error = None

        evidence = self._canonical_evidence(
            case_id=network_id,
            tool_name="run_power_flow",
            input_hash=input_hash,
            status=status,
            result_payload=data,
            diagnostics=[error] if error else [],
            physical_valid=(violations == 0),
        )
        return PowerMCPResult(
            status=status,
            data=data,
            error=error,
            execution_time_s=elapsed,
            evidence=evidence,
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool schemas for all PowerMCP tools.

        Returns:
            List of tool schema dictionaries (stable across backend
            replacements per the MISSION.md M01 interface contract).
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "load_network",
                    "description": "Load a power network case by identifier",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "network_id": {
                                "type": "string",
                                "description": "Network identifier",
                                "enum": list(SERVED_NETWORKS),
                            }
                        },
                        "required": ["network_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_power_flow",
                    "description": (
                        "Run AC power flow analysis on a network; returns one of "
                        "success / non_convergence / constraint_violation / "
                        "execution_failure with solver measurements"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "network_id": {
                                "type": "string",
                                "description": "Network identifier",
                                "enum": list(SERVED_NETWORKS),
                            },
                            "method": {
                                "type": "string",
                                "description": "Solution method (advisory; nr is the real solver)",
                                "enum": ["nr", "cg"],
                                "default": "nr",
                            },
                            "max_iter": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "Maximum iterations",
                                "default": 50,
                            },
                            "check_limits": {
                                "type": "boolean",
                                "description": (
                                    "Report converged-but-violating solutions as "
                                    "constraint_violation"
                                ),
                                "default": True,
                            },
                        },
                        "required": ["network_id"],
                        # Without this, an MCP client's contingency_line was
                        # silently dropped and base-case results returned as if
                        # the outage had been applied (AGENTS.md FM-1).
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> PowerMCPResult:
        """Execute a named tool with arguments.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments as a dictionary.

        Returns:
            PowerMCPResult with the execution outcome.
        """
        if tool_name == "load_network":
            return self.load_network(arguments["network_id"])
        if tool_name == "run_power_flow":
            return self.run_power_flow(
                arguments["network_id"],
                arguments.get("method", "nr"),
                arguments.get("max_iter", 50),
                arguments.get("check_limits", True),
            )
        return PowerMCPResult(
            ExecutionStatus.EXECUTION_FAILURE,
            error=f"Unknown tool: {tool_name}",
        )

    @staticmethod
    def _canonical_evidence(
        case_id: str,
        tool_name: str,
        input_hash: str,
        status: ExecutionStatus,
        result_payload: dict[str, Any],
        diagnostics: list[str],
        physical_valid: bool,
    ) -> dict[str, Any]:
        """Build the MISSION.md M02 canonical execution-evidence record.

        The result hash covers a canonical (sorted-keys) JSON serialization of
        the solver-derived payload, so identical solver output on the same
        pinned stack hashes identically.
        """
        canonical = json.dumps(result_payload, sort_keys=True, default=str)
        return {
            "case_id": case_id,
            "solver": "pandapower",
            "solver_version": pp.__version__,
            "tool_name": tool_name,
            "input_hash": input_hash,
            "status": status.value,
            "result_hash": _sha256_bytes(canonical.encode("utf-8")),
            "validity": {
                "schema": True,  # payload matches the declared result schema
                "numerical": True,  # values came from a real solver run
                "physical": physical_valid,
            },
            "diagnostics": diagnostics,
        }

    @staticmethod
    def persist_evidence(result: PowerMCPResult, path: str | Path) -> Path:
        """Persist a result's canonical evidence record to a JSON file.

        Args:
            result: A PowerMCPResult carrying an evidence record.
            path: Destination file path (parent dirs created).

        Returns:
            The path written.

        Raises:
            ValueError: if the result carries no evidence (the solver never
                ran, e.g. EXECUTION_FAILURE before the solver phase).
        """
        if result.evidence is None:
            raise ValueError("result carries no canonical evidence record")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result.evidence, indent=2, sort_keys=True) + "\n")
        return p
