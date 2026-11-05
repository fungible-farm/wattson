"""Executor for the typed command IR's requests (PRD-0010 G1, PC-REQ-001; backlog 0015).

``rust/command-ir`` compiles the DSL to ``{"ir_version", "command", "args"}`` requests. This runs
each of the ten commands on the tool that backs it:

| Command | Executor |
|---|---|
| SOLVE, SIMULATE | ``studies.run_study_trial`` (the evaluation's tool, same arguments) |
| RECALL | cached AEMO regional demand, only what was available ``as_of`` (``hindcast.AsOf``) |
| RESOLVE | the walkable namespace (``namespace.get`` / ``ls``) |
| ESTIMATE | WLS state estimation from synthetic measurements (``engineering.state_estimation``) |
| OPTIMIZE | DC OPF with its LP/QP cross-check, or AC OPF with its feasibility re-check |
| FORECAST, HINDCAST | the leakage-free harness (``forecasting.hindcast``) |
| COMPARE | candidate outages re-solved and ranked (``datasets.judgement`` solvers) |
| EXPLAIN | every number in a claim resolved against the path it cites (``provenance.CITATION``) |

A request with another ``ir_version`` is refused: the IR and the executor change together.
"""

from __future__ import annotations

import dataclasses
import json
import math
from typing import Any, Callable

import pandas as pd
import pandapower as pp

IR_VERSION: str = "0.3.0"  # rust/command-ir IR_VERSION
# Fields hidden from model-facing study output (mcp_server.MODEL_HIDDEN_FIELDS, FM-3).
HIDDEN: frozenset[str] = frozenset({"execution_time_s"})


def _study(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.inference.studies import run_study_trial

    result = dataclasses.asdict(run_study_trial(**args))
    return {k: (v.value if hasattr(v, "value") else v) for k, v in result.items() if k not in HIDDEN}


def _recall(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.forecasting.hindcast import AsOf, load_region

    series = load_region(args["region"])
    window = series[(series.index >= pd.Timestamp(args["from"])) & (series.index <= pd.Timestamp(args["to"]))]
    known = AsOf(window, pd.Timestamp(args["as_of"])).history()
    return {"region": args["region"], "unit": "MW", "as_of": args["as_of"],
            "values": [[str(t), float(v)] for t, v in known.items()], "withheld_after_as_of": int(len(window) - len(known))}


def _resolve(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.inference.namespace import NamespaceError, call, parse

    try:
        kind = "get" if parse(args["path"])["attribute"] else "ls"
    except NamespaceError as error:
        return {"status": "execution_failure", "error": str(error)}
    return call(kind, {"path": args["path"]})


def _estimate(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.engineering.state_estimation import estimate_state, synthesize_measurements
    from wattson.inference import powermcp as pm

    net, _ = pm._load_network_object(args["network_id"])
    pm._runpp_counted(net, numba=True)
    result = estimate_state(net, synthesize_measurements(net, args["seed"]))
    return {k: result[k] for k in ("observable", "measurements", "bad_data_suspected", "max_abs_voltage_error_pu",
                                   "max_abs_angle_error_deg")} | {"removed_as_bad": len(result["removed_as_bad"]),
                                                                  "feasibility": result["proof"]["feasibility_status"]}


def _optimize(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.engineering.cases import CASES, load_case
    from wattson.inference import powermcp as pm

    net = load_case(args["case"]) if args["case"] in CASES else pm._load_network_object(args["case"])[0]
    if args["formulation"] == "ac":
        from wattson.engineering.ac_opf import run_ac_opf

        result = run_ac_opf(net)
        return {"solved": result["solved"], "objective": result["objective"], "binding": len(result["binding"]),
                "feasibility": result["proof"]["feasibility_status"], "optimality": "local; no certificate"}
    from wattson.engineering.dc_opf import run_dc_opf

    result = run_dc_opf(net)
    return {"solved": result["solved"], "objective": result["objective_eur_per_h"], "binding": len(result["binding_constraints"]),
            "lmp_range_eur_per_mwh": result["lmp_range_eur_per_mwh"], "cross_check": result["cross_check"]["status"]}


def _forecast(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.forecasting.hindcast import forecast, load_region

    f = forecast(load_region(args["region"]), args["model"], pd.Timestamp(args["issue_time"]), tuple(args["horizons"]))
    return {"model": f.model, "version": f.version, "issue_time": str(f.issue_time),
            "valid_times": [str(t) for t in f.valid_times], "quantiles": {str(q): list(v) for q, v in f.quantiles.items()}}


def _hindcast(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.forecasting.hindcast import hindcast, load_region

    series = load_region(args["region"])
    first = series.index[0].normalize() + pd.Timedelta(days=args["start_day"] - 1)
    return hindcast(series, tuple(args["horizons"]), first, args["every"])


def _compare(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.datasets.judgement import max_loading_after
    from wattson.inference import powermcp as pm

    def min_voltage_after(case: str, line: int) -> float | None:
        net, _ = pm._load_network_object(case)
        net.line.loc[line, "in_service"] = False
        try:
            pm._runpp_counted(net, numba=True)
        except pp.LoadflowNotConverged:
            return None
        vm = net.res_bus.vm_pu.astype(float)
        return float(vm[vm.apply(math.isfinite)].min())

    by_loading = args["metric"] == "max_line_loading"
    measure = max_loading_after if by_loading else min_voltage_after
    values = {line: measure(args["network_id"], line) for line in args["candidate_lines"]}
    solved = {k: v for k, v in values.items() if v is not None}
    ranked = sorted(solved, key=lambda k: solved[k], reverse=by_loading)
    return {"metric": args["metric"], "values": {str(k): v for k, v in values.items()}, "worst_first": ranked,
            "nonconvergent": [k for k, v in values.items() if v is None]}


def _explain(args: dict[str, Any]) -> dict[str, Any]:
    from wattson.inference.namespace import NamespaceError, get
    from wattson.inference.provenance import CITATION, matches

    evidence = []
    for match in CITATION.finditer(args["claim"]):
        printed, path = match.group(1), match.group("path")
        try:
            held = get(path)["value"]
        except NamespaceError as error:
            evidence.append({"number": printed, "path": path, "held": None, "verified": False, "error": str(error)})
            continue
        ok = isinstance(held, (int, float)) and not isinstance(held, bool) and matches(printed, float(held))
        evidence.append({"number": printed, "path": path, "held": held, "verified": ok})
    return {"evidence": evidence, "all_verified": bool(evidence) and all(e["verified"] for e in evidence)}


EXECUTORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "SOLVE": _study, "SIMULATE": _study, "RECALL": _recall, "RESOLVE": _resolve, "ESTIMATE": _estimate,
    "OPTIMIZE": _optimize, "FORECAST": _forecast, "HINDCAST": _hindcast, "COMPARE": _compare, "EXPLAIN": _explain,
}


def execute(request: dict[str, Any]) -> dict[str, Any]:
    """Run one IR request.

    Raises:
        ValueError: for another IR version or an unknown command.
    """
    if request.get("ir_version") != IR_VERSION:
        raise ValueError(f"IR version {request.get('ir_version')!r} is not this executor's {IR_VERSION}")
    if request.get("command") not in EXECUTORS:
        raise ValueError(f"unknown command {request.get('command')!r}")
    return EXECUTORS[request["command"]](request["args"])


if __name__ == "__main__":
    import sys

    for line in sys.stdin:
        if line.strip():
            print(json.dumps(execute(json.loads(line)), default=str))
