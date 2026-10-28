"""Bounded, stateless AC study trials for model-driven parameter fitting.

Only case identifiers and numeric study parameters are accepted, never code or
paths. Each call loads a fresh network. No target, reference answer, search
algorithm, or training/benchmark dataset is available to this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandapower as pp

from wattson.inference import powermcp as pm

# Match the existing fitting task interval; cap solver effort for tool calls.
SCALE_MIN: float = 0.9
SCALE_MAX: float = 1.1
# Operating point (IR 0.3.0, backlog 0017): every load times load_level before the study. The same
# interval as scale, over which the served networks' discovered elements were checked to solve.
LOAD_LEVEL_MIN: float = SCALE_MIN
LOAD_LEVEL_MAX: float = SCALE_MAX
MAX_ITERATIONS: int = 100
# How many de-energized bus ids a response lists (the count is always given);
# matches run_power_flow's violating_bus_ids cap.
DE_ENERGIZED_IDS_SHOWN: int = 10
# How many valid ids an unknown-element error lists, lowest first (the count is
# always given); the same cap. Element ids are sparse (snem-sa-2000's 503 buses
# run 985..10106), so a range would name ids that do not exist, and load_network
# returns counts only: without this, a model that sent a wrong id could only
# guess (r6b Qwen student, three report attempts, bus 999 then 1, 2, 3, ...).
VALID_IDS_SHOWN: int = 10
# Plural names for the pandapower tables an id can refer to, as error messages say them.
ELEMENT_KINDS: dict[str, str] = {"bus": "buses", "line": "lines", "trafo": "transformers", "gen": "generators"}
# Nameplate loading screening, in percent; not a full operational safety study.
THERMAL_LIMIT_PERCENT: float = 100.0


# The typed command IR emits this schema (rust/command-ir: `study_trial_schema()`; its test
# asserts the emitted bytes equal this file). Python serves it rather than restating it, so the
# schema has one source (PRD-0010 G1, backlog 0015).
IR_STUDY_SCHEMA: Path = Path(__file__).resolve().parent / "served_run_study_trial.json"


def tool_schema() -> dict[str, Any]:
    """Describe measurements and modifications without exposing a solution oracle.

    Served from the IR's emitted schema (``IR_STUDY_SCHEMA``); ``test_command_ir`` checks its
    enums and bounds still equal this module's ``SCALE_MIN``/``SCALE_MAX``/``MAX_ITERATIONS``
    and the loader's cases. ``contingency_line`` is optional: when it was required, models
    filled it with 0 (line 0 out) or "None" to mean "no contingency" (AGENTS.md FM-7, FM-18).
    """
    return json.loads(IR_STUDY_SCHEMA.read_text())


@lru_cache(maxsize=None)
def base_case_voltage_violations(network_id: str) -> int | None:
    """Count bus-voltage band violations in the unmodified, intact case.

    snem-sa-2000 already has 39 such buses, so every trial on it is
    ``constraint_violation`` whatever the trial value (AGENTS.md FM-17). Reporting
    the base count lets a model tell an inherited violation from one its trial
    caused, without weakening the canonical status contract. Cached because each
    MCP server process serves one attempt and the case files never change.

    Returns:
        The violation count, or None when the unmodified case does not converge
        (a converged trial on such a case must not be reported as non-convergence).
    """
    net, _ = pm._load_network_object(network_id)
    try:
        pm._runpp_counted(net, numba=True, max_iteration=MAX_ITERATIONS)
    except pp.LoadflowNotConverged:
        return None
    vm = net.res_bus.vm_pu
    return int(((vm < pm.VOLTAGE_LIMIT_LOW_PU) | (vm > pm.VOLTAGE_LIMIT_HIGH_PU)).sum())


def unknown_element(role: str, element_id: int, index: Any, kind: str) -> ValueError:
    """An unknown-id error that names the valid ids, so a model can recover from the response.

    Args:
        role: What the id was for ("observation element", "modification element", "contingency line").
        element_id: The id the model sent.
        index: The pandapower table index the id had to be in.
        kind: The table's element name, plural ("buses", "lines", ...).

    Returns:
        The ValueError to raise; its message is the tool's ``error`` field.
    """
    shown = ", ".join(str(int(value)) for value in sorted(index)[:VALID_IDS_SHOWN])
    return ValueError(f"Unknown {role} {element_id}: the network has {len(index)} {kind}; "
                      f"the lowest {min(len(index), VALID_IDS_SHOWN)} ids are {shown}")


def run_study_trial(
    network_id: str, operation: str, scale: float | None = None, element_id: int | None = None,
    observation: str | None = None, observation_id: int | None = None, max_iter: int | None = None,
    contingency_line: int | None = None, load_level: float | None = None,
) -> pm.PowerMCPResult:
    """Apply one explicit perturbation and measure a real, independently solved network.

    Non-convergence and numerical failure never return usable metric values.
    Physical validity covers bus voltage and line/transformer nameplate screening
    only; convergence does not establish operational safety.

    An argument the operation does not use is optional (IR 0.2.0, backlog 0015): a report
    (``operation="none"``) needs no scale, element or observation, since its network-wide
    fields are always returned, and only ``line_rating``/``gen_dispatch`` modify an element.
    Requiring them made a model invent an id the task never named (r6b Qwen student: bus 999,
    then 1, 2, 3, ...). Unused arguments that are sent are accepted and ignored. A call with
    every argument echoes and answers exactly as before.

    ``load_level`` (IR 0.3.0) sets the operating point: every load's P/Q times it before the
    study. It is refused with ``load_scale``, which already scales every load. Omitted, the base
    load is studied and the response is byte-identical to 0.2.0.
    """
    arguments = {k: v for k, v in locals().items() if v is not None or k == "contingency_line"}
    start = time.monotonic()
    try:
        expected = {"load_scale": "bus_voltage", "line_rating": "line_loading", "gen_dispatch": "trafo_loading"}
        if operation not in {"none", *expected}:
            raise ValueError("Unknown operation or observation")
        missing = [name for name, value in (("max_iter", max_iter),) + (
            (("scale", scale), ("observation", observation), ("observation_id", observation_id)) if operation != "none" else ()) + (
            (("element_id", element_id),) if operation in {"line_rating", "gen_dispatch"} else ()) if value is None]
        if observation_id is not None and observation is None:
            missing.append("observation")
        if missing:
            raise ValueError(f"operation={operation} requires {', '.join(missing)}")
        if load_level is not None:
            if isinstance(load_level, bool) or not isinstance(load_level, (float, int)) or not math.isfinite(load_level) \
                    or not LOAD_LEVEL_MIN <= load_level <= LOAD_LEVEL_MAX:
                raise ValueError("load_level must be finite and within the declared interval")
            if operation == "load_scale":
                raise ValueError("load_scale already scales every load; omit load_level")
        scale = 1.0 if scale is None else scale
        if isinstance(scale, bool) or not isinstance(scale, (float, int)) or not math.isfinite(scale) or not SCALE_MIN <= scale <= SCALE_MAX:
            raise ValueError("scale must be finite and within the declared interval")
        for value in (element_id, observation_id, max_iter):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError("element/observation IDs and max_iter must be integers")
        if not 1 <= max_iter <= MAX_ITERATIONS:
            raise ValueError("max_iter outside the declared budget")
        if contingency_line is not None and (isinstance(contingency_line, bool) or not isinstance(contingency_line, int)):
            raise ValueError("contingency_line must be an integer or null")
        if observation is not None and observation not in {"bus_voltage", "line_loading", "trafo_loading"}:
            raise ValueError("Unknown operation or observation")
        if operation != "none" and observation != expected[operation]:
            raise ValueError("Observation incompatible with modification")
        if operation == "none" and scale != 1.0:
            raise ValueError("operation=none requires scale=1")
        net, case_hash = pm._load_network_object(network_id)
        if load_level is not None:
            net.load.loc[:, ["p_mw", "q_mvar"]] *= load_level
        observes = observation_id is not None
        if observes:
            table, column = {"bus_voltage": ("res_bus", "vm_pu"), "line_loading": ("res_line", "loading_percent"), "trafo_loading": ("res_trafo", "loading_percent")}[observation]
            element_table = {"bus_voltage": "bus", "line_loading": "line", "trafo_loading": "trafo"}[observation]
            if observation_id not in net[element_table].index:
                raise unknown_element("observation element", observation_id, net[element_table].index,
                                      ELEMENT_KINDS[element_table])
        if operation == "load_scale":
            net.load.loc[:, ["p_mw", "q_mvar"]] *= scale
        elif operation in {"line_rating", "gen_dispatch"}:
            modified_table, modified_column = ("line", "max_i_ka") if operation == "line_rating" else ("gen", "p_mw")
            if element_id not in net[modified_table].index:
                raise unknown_element("modification element", element_id, net[modified_table].index,
                                      ELEMENT_KINDS[modified_table])
            net[modified_table].loc[element_id, modified_column] *= scale
        if contingency_line is not None:
            if contingency_line not in net.line.index:
                raise unknown_element("contingency line", contingency_line, net.line.index, ELEMENT_KINDS["line"])
            net.line.loc[contingency_line, "in_service"] = False
        iterations = pm._runpp_counted(net, numba=True, max_iteration=max_iter)
        vm = net.res_bus.vm_pu
        # An outage can isolate buses: pandapower leaves them NaN (de-energized).
        # The reference solver (datasets.generate._solve) skips NaN in min/max and
        # violation counts, so this does too, and lists them. Refusing the whole
        # study made 8 of the 25 reports in the 100-task benchmark unsolvable
        # (AGENTS.md FM-25). The intact network has no isolated bus, so a NaN
        # there is still a solver failure.
        de_energized = [int(bus) for bus in net.bus.index[net.bus.in_service & ~vm.apply(
            lambda value: math.isfinite(float(value)))]]
        if de_energized and contingency_line is None:
            raise ValueError("Unsolved or nonfinite voltage on an in-service bus")
        voltage_violations = int(((vm < pm.VOLTAGE_LIMIT_LOW_PU) | (vm > pm.VOLTAGE_LIMIT_HIGH_PU)).sum())
        line_loading = float(net.res_line.loading_percent.max()) if len(net.line) else 0.0
        trafo_loading = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
        observed = float(net[table].loc[observation_id, column]) if observes else math.nan
        data = {
            "study": arguments, "converged": True, "iterations": iterations,
            # None when the observed element is itself de-energized by the outage.
            "metric": observed if math.isfinite(observed) else None,
            "metric_unit": "pu" if observation == "bus_voltage" else "percent",
            "min_bus_voltage_pu": float(vm.min()), "max_bus_voltage_pu": float(vm.max()),
            "voltage_violations": voltage_violations,
            "base_case_voltage_violations": base_case_voltage_violations(network_id),
            "metric_valid": math.isfinite(observed),
            "contingency": f"line {contingency_line} out of service" if contingency_line is not None else "none (intact network)",
            "max_line_loading_percent": line_loading, "max_trafo_loading_percent": trafo_loading,
            "voltage_limit_pu": [pm.VOLTAGE_LIMIT_LOW_PU, pm.VOLTAGE_LIMIT_HIGH_PU],
            "thermal_limit_percent": THERMAL_LIMIT_PERCENT,
            "physical_scope": "bus voltage band and line/transformer nameplate loading only",
        }
        if load_level is not None:
            data["operating_point"] = f"every load at {load_level} times its base value"
        if not observes:
            # A network-wide report observes no element: no metric fields at all, rather
            # than metric_valid=false, which reads as a failed study.
            for key in ("metric", "metric_unit", "metric_valid"):
                del data[key]
            data["observation"] = "none (network-wide report)"
        if de_energized:
            # Only when an outage isolates buses, so every other response stays
            # byte-identical to the ones the students were trained on.
            data["de_energized_buses"] = len(de_energized)
            data["de_energized_bus_ids"] = de_energized[:DE_ENERGIZED_IDS_SHOWN]
        for value in (data["min_bus_voltage_pu"], data["max_bus_voltage_pu"], line_loading, trafo_loading):
            if not math.isfinite(value):
                raise ValueError("Nonfinite solver measurement")
        valid = not voltage_violations and max(line_loading, trafo_loading) <= THERMAL_LIMIT_PERCENT
        status = pm.ExecutionStatus.SUCCESS if valid else pm.ExecutionStatus.CONSTRAINT_VIOLATION
        source = json.dumps({"case_hash": case_hash, "arguments": arguments}, sort_keys=True)
        evidence = pm.PowerMCP._canonical_evidence(
            case_id=network_id, tool_name="run_study_trial",
            input_hash="sha256:" + hashlib.sha256(source.encode()).hexdigest(),
            status=status, result_payload=data, diagnostics=[], physical_valid=valid,
        )
        return pm.PowerMCPResult(status, data, execution_time_s=time.monotonic() - start, evidence=evidence)
    except pp.LoadflowNotConverged as error:
        return pm.PowerMCPResult(pm.ExecutionStatus.NON_CONVERGENCE, error=str(error), execution_time_s=time.monotonic() - start)
    except Exception as error:  # noqa: BLE001 -- preserve actual backend failures as explicit outcomes
        return pm.PowerMCPResult(pm.ExecutionStatus.EXECUTION_FAILURE, error=f"{type(error).__name__}: {error}", execution_time_s=time.monotonic() - start)
