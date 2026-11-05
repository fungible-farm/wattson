"""WLS state estimation with observability and bad-data checks, under the proof contract (PC-REQ-014, G2).

pandapower's ``estimation`` (BSD) does the weighted-least-squares solve, the chi-square
bad-data test and the largest-normalised-residual removal. This module adds what G2
requires around them: a typed result, the proof contract (``proof.make_proof``), an
explicit observability verdict, and the identity of any measurement removed as bad data.

Named stand-in (AGENTS.md): **the measurements are synthetic.** G5 (SCADA, PMU) is blocked,
so :func:`synthesize_measurements` takes a solved AC power flow as the truth and adds seeded
Gaussian noise. The standard deviations in ``MEASUREMENT_STD`` are *assumed* accuracy
classes, not a measured SCADA specification; change them there. Because the truth is
known, a result also reports its error against it, which a real estimate cannot.
"""

from __future__ import annotations

import copy
import math
from typing import TypedDict

import numpy as np
import pandapower as pp
import pandapower.estimation as est

from wattson.engineering.proof import NumericalProof, make_proof

# Assumed measurement accuracy (one standard deviation) for the synthetic SCADA set.
MEASUREMENT_STD: dict[str, float] = {"v": 0.002, "p": 0.5, "q": 0.5}  # pu, MW, Mvar
# pandapower's own defaults for the bad-data tests: 5% false-alarm probability for the
# chi-square test, 3.0 for the largest normalised residual.
CHI2_FALSE_ALARM: float = 0.05
LNR_THRESHOLD: float = 3.0
WLS_TOLERANCE: float = 1e-6
WLS_MAX_ITERATIONS: int = 50


class Measurement(TypedDict):
    """One synthetic measurement and the true value it was drawn around."""

    meas_type: str  # "v", "p" or "q"
    element_type: str  # "bus", "line" or "trafo"
    element: int
    side: str | None  # "from"/"to" (lines) or "hv"/"lv" (transformers); None for bus voltages
    true_value: float
    value: float
    std_dev: float


class StateEstimate(TypedDict):
    """A WLS estimate, its bad-data verdicts and its proof."""

    observable: bool
    measurements: int
    bad_data_suspected: bool  # chi-square test on the full set
    removed_as_bad: list[Measurement]
    max_abs_voltage_error_pu: float | None  # against the synthetic truth
    max_abs_angle_error_deg: float | None
    proof: NumericalProof


def synthesize_measurements(net: pp.pandapowerNet, seed: int) -> list[Measurement]:
    """Voltage at every bus and P/Q at both ends of every line and transformer.

    Both ends, as SCADA usually measures a branch: with one end only, each flow can be a
    *critical* measurement (nothing else observes that branch), whose residual is zero, so
    no bad-data test can see an error in it (measured on case9, 2026-10-23).

    Args:
        net: A solved network (``pp.runpp`` already run); its results are the truth.
        seed: Seed of the noise generator, so a set is reproducible.

    Raises:
        ValueError: if the network has no power-flow results.
    """
    if not len(net.res_bus):
        raise ValueError("synthesize_measurements needs a solved network (pp.runpp)")
    rng = np.random.default_rng(seed)
    out: list[Measurement] = []

    def add(kind: str, element_type: str, element: int, side: str | None, truth: float) -> None:
        if math.isfinite(truth):
            std = MEASUREMENT_STD[kind]
            out.append(Measurement(meas_type=kind, element_type=element_type, element=int(element), side=side,
                                   true_value=float(truth), value=float(truth + rng.normal(0.0, std)), std_dev=std))

    for bus in net.bus.index[net.bus.in_service]:
        add("v", "bus", bus, None, float(net.res_bus.vm_pu[bus]))
    for line in net.line.index[net.line.in_service]:
        for side, end in (("from", "from"), ("to", "to")):
            add("p", "line", line, side, float(net.res_line[f"p_{end}_mw"][line]))
            add("q", "line", line, side, float(net.res_line[f"q_{end}_mvar"][line]))
    for trafo in net.trafo.index[net.trafo.in_service]:
        for side in ("hv", "lv"):
            add("p", "trafo", trafo, side, float(net.res_trafo[f"p_{side}_mw"][trafo]))
            add("q", "trafo", trafo, side, float(net.res_trafo[f"q_{side}_mvar"][trafo]))
    return out


def _load(net: pp.pandapowerNet, measurements: list[Measurement]) -> pp.pandapowerNet:
    """A copy of ``net`` holding exactly ``measurements``."""
    work = copy.deepcopy(net)
    work.measurement.drop(work.measurement.index, inplace=True)
    for m in measurements:
        pp.create_measurement(work, m["meas_type"], m["element_type"], m["value"], m["std_dev"], m["element"],
                              side=m["side"])
    return work


def estimate_state(net: pp.pandapowerNet, measurements: list[Measurement], remove_bad: bool = True) -> StateEstimate:
    """WLS estimate from ``measurements``; chi-square test; optionally remove bad data by LNR.

    Unobservable (pandapower fails to estimate) is reported as ``observable = False`` with an
    ``indeterminate`` proof, never as an estimate.
    """
    work = _load(net, measurements)
    suspected = False
    removed: list[Measurement] = []
    warnings: list[str] = ["measurements are synthetic (seeded noise around a solved AC power flow)"]
    try:
        ok = est.estimate(work, algorithm="wls", init="flat", tolerance=WLS_TOLERANCE,
                          maximum_iterations=WLS_MAX_ITERATIONS)
    except Exception as error:  # noqa: BLE001 -- pandapower raises on singular gain matrices
        ok = False
        warnings.append(f"WLS failed: {type(error).__name__}: {error}")
    if not ok:
        proof = make_proof(converged=False, feasibility_status="indeterminate", solver_name="pandapower.estimation (WLS)",
                           solver_distribution="pandapower", validation_warnings=warnings + ["not observable or not converged"])
        return StateEstimate(observable=False, measurements=len(measurements), bad_data_suspected=False,
                             removed_as_bad=[], max_abs_voltage_error_pu=None, max_abs_angle_error_deg=None, proof=proof)
    probe = _load(net, measurements)
    suspected = bool(est.chi2_analysis(probe, init="flat", tolerance=WLS_TOLERANCE, chi2_prob_false=CHI2_FALSE_ALARM))
    if remove_bad and suspected:
        before = list(probe.measurement.index)
        est.remove_bad_data(probe, init="flat", tolerance=WLS_TOLERANCE, rn_max_threshold=LNR_THRESHOLD)
        removed = [measurements[before.index(i)] for i in before if i not in set(probe.measurement.index)]
        work = probe
        warnings.append(f"{len(removed)} measurement(s) removed by the largest-normalised-residual test")
    truth = net.res_bus.vm_pu
    est_vm, est_va = work.res_bus_est.vm_pu, work.res_bus_est.va_degree
    common = [b for b in net.bus.index if b in est_vm.index and math.isfinite(float(truth[b])) and math.isfinite(float(est_vm[b]))]
    v_error = max((abs(float(est_vm[b]) - float(truth[b])) for b in common), default=None)
    a_error = max((abs(float(est_va[b]) - float(net.res_bus.va_degree[b])) for b in common), default=None)
    proof = make_proof(converged=True, feasibility_status="feasible", solver_name="pandapower.estimation (WLS)",
                       solver_distribution="pandapower",
                       equation_residuals={"max_abs_voltage_error_vs_truth_pu": v_error if v_error is not None else math.nan},
                       validation_warnings=warnings + (["chi-square test suspects bad data"] if suspected else []))
    return StateEstimate(observable=True, measurements=len(measurements), bad_data_suspected=suspected,
                         removed_as_bad=removed, max_abs_voltage_error_pu=v_error, max_abs_angle_error_deg=a_error,
                         proof=proof)
