"""Shared helpers for Labs 1-3: powerio -> pandapower loading, the
deterministic "agent proposes, pandapower disposes" search loop, and a
pandapower <-> pypowsybl bridge (Lab 2's N-1 cross-check, Lab 3's solver
spike).

Sandbox note (see each lab's README for the full version): the real
architecture in docs/VISION.md has an LLM (Phi-4-mini via a llama.cpp pod)
choosing the next trial value in the loop below, over MCP. This sandbox has
no podman and no budget to download/serve a GGUF model, so `propose_next`
below is a plain bisection policy standing in for that decision. It is
swapped out, not hidden: every call site names the swap explicitly.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandapower as pp
import powerio as pio

# Upper bound on bisection iterations. Derived, not arbitrary: bisection
# halves the search interval every iteration, so for the widest interval
# used anywhere in these labs (+/-10% -> width 0.20, see SCALE_LO/SCALE_HI
# in labs/01.../run.py) and the tightest tolerance used (0.002 pu, also
# Lab 1), reaching interval width < tolerance needs
# ceil(log2(0.20 / 0.002)) = 7 iterations. 25 is a >3x safety margin over
# that worst case so a genuine non-convergence (bad bracket, non-monotonic
# `evaluate`) fails fast instead of looping silently.
DEFAULT_MAX_ITER: int = 25


def load_case(matpower_path: str | Path) -> tuple[pp.pandapowerNet, list[str]]:
    """Parse a MATPOWER .m case with powerio and convert to a pandapower net.

    powerio is the Rust-backed parser named in docs/VISION.md section 8 (the
    repo's Rust requirement). Its `convert_file(..., "pandapower-json")`
    path does the .m -> pandapower translation directly; pandapower's own
    `from_ppc`/`from_mpc` converters are the documented fallback in
    docs/VISION.md section 8 if a given .m variant isn't covered -- not
    needed here, powerio parses both snemSA.m and snem1803.m cleanly.

    Args:
        matpower_path: path to a MATPOWER ``.m`` case file.

    Returns:
        A tuple of ``(net, warnings)`` where ``net`` is the resulting
        pandapower network and ``warnings`` is powerio's list of
        lossy-conversion notices (e.g. dropped angle limits).
    """
    matpower_path = Path(matpower_path)
    pp_json, warnings = pio.convert_file(str(matpower_path), "pandapower-json")
    net = pp.from_json_string(pp_json)
    return net, warnings


@dataclass
class FitIteration:
    """One bisection trial: the value tried and what pandapower measured."""

    iteration: int
    trial: float
    observed: float
    residual: float


@dataclass
class FitResult:
    """Outcome of a `bisection_fit` run, including the full iteration log."""

    converged: bool
    trial: float
    observed: float
    residual: float
    iterations: list[FitIteration] = field(default_factory=list)


def bisection_fit(
    evaluate: Callable[[float], float],
    target: float,
    lo: float,
    hi: float,
    tol: float,
    max_iter: int = DEFAULT_MAX_ITER,
) -> FitResult:
    """Deterministic bisection search for a scalar trial parameter.

    `evaluate(trial)` must return the observed physical quantity for that
    trial value (a real pandapower.runpp() call downstream, every time --
    this function never fabricates a physics result). Requires
    evaluate(lo)-target and evaluate(hi)-target to have opposite signs
    (checked, not assumed) and `evaluate` to be monotonic on [lo, hi].

    This is the stand-in named in the module docstring for the LLM's
    "propose the next trial value" step; a live agent would call the same
    `evaluate` function as an MCP tool instead of this closed-form bisection.

    Args:
        evaluate: maps a trial value to the observed physical quantity.
        target: the value `evaluate(trial)` should converge to.
        lo: lower bound of the search interval.
        hi: upper bound of the search interval.
        tol: convergence tolerance on ``abs(observed - target)``.
        max_iter: hard cap on iterations; see DEFAULT_MAX_ITER for how the
            default value was derived.

    Returns:
        A FitResult; ``converged`` is False if `max_iter` was exhausted
        without reaching `tol`.

    Raises:
        ValueError: if `target` is not bracketed by `evaluate(lo)` and
            `evaluate(hi)` (i.e. they don't straddle it in sign).
    """
    iterations: list[FitIteration] = []

    f_lo = evaluate(lo) - target
    f_hi = evaluate(hi) - target
    if f_lo == 0:
        obs = evaluate(lo)
        iterations.append(FitIteration(1, lo, obs, obs - target))
        return FitResult(True, lo, obs, obs - target, iterations)
    if f_hi == 0:
        obs = evaluate(hi)
        iterations.append(FitIteration(1, hi, obs, obs - target))
        return FitResult(True, hi, obs, obs - target, iterations)
    if (f_lo > 0) == (f_hi > 0):
        raise ValueError(
            f"bisection_fit: target {target} is not bracketed by "
            f"[{lo}, {hi}] -> observed [{evaluate(lo)}, {evaluate(hi)}]"
        )

    a, b = lo, hi
    fa = f_lo
    for i in range(1, max_iter + 1):
        mid = (a + b) / 2.0
        observed = evaluate(mid)
        residual = observed - target
        iterations.append(FitIteration(i, mid, observed, residual))
        if abs(residual) <= tol:
            return FitResult(True, mid, observed, residual, iterations)
        if (residual > 0) == (fa > 0):
            a, fa = mid, residual
        else:
            b = mid
    last = iterations[-1]
    return FitResult(False, last.trial, last.observed, last.residual, iterations)


def to_pypowsybl_network(net: pp.pandapowerNet, mat_path: str | Path) -> Any:
    """Round-trip a solved pandapower net through a real MATPOWER `.mat` file into a pypowsybl
    Network object.

    pypowsybl's MATPOWER importer only accepts the binary `.mat` MATLAB serialization, not the
    `.m` script format every case file in this repo (and MATPOWER itself) actually ships --
    confirmed against PowSyBl's own docs and by testing both a real case here and a textbook
    `case9.m` (see labs/03-advanced-provider-bakeoff/README.md's "pypowsybl spike" section).
    pandapower's own `to_mpc()` (a real `scipy.io.savemat`-backed writer) produces that `.mat`
    file directly from an already-solved net, so both engines end up solving the exact same
    network rather than two independently-parsed copies.

    Real finding, not assumed: pypowsybl's MATPOWER importer defaults
    `matpower.import.ignore-base-voltage` to `true` (confirmed via
    `pypowsybl.network.get_import_parameters("MATPOWER")`), silently discarding the real
    per-bus base-kV column the `.mat` file actually carries (this repo's real cases have
    genuine NEM voltage levels -- 132kV, 66kV, 33kV, 11kV, etc.) and reporting every bus at
    `nominal_v=1.0` instead -- voltages and currents both then come back in a meaningless
    normalized unit, not kV/A. This function always passes that parameter as `false` so
    results are physically comparable to pandapower's own kV/kA/pu conventions. Verified this
    is purely a reporting-convention fix, not a physics change: total system generation/load/
    loss figures are bit-identical with the parameter either way (checked directly on
    snem1803.m).

    Args:
        net: a solved pandapower net (`pp.runpp()` already called).
        mat_path: where to write the intermediate `.mat` file (caller's own gitignored scratch
            output -- this function does not clean it up).

    Returns:
        The equivalent pypowsybl `Network`, loaded from that `.mat` file with real base
        voltages preserved.
    """
    from pandapower.converter.matpower import to_mpc
    import pypowsybl.network as pn

    to_mpc(net, str(mat_path))
    return pn.load(str(mat_path), {"matpower.import.ignore-base-voltage": "false"})


def pypowsybl_element_id_map(net: pp.pandapowerNet, pypowsybl_net: Any) -> dict[int, str]:
    """Map every pandapower `net.line` row index to its corresponding pypowsybl element id.

    Real finding, not assumed: pandapower's own bus index is an arbitrary, often
    non-sequential id (this repo's cases preserve the original MATPOWER bus numbers via
    powerio, e.g. Lab 1's "bus 2008"); the `.mat` file `to_mpc()` writes uses a completely
    different, internally-renumbered 1-based sequence (pandapower's own `to_ppc()` bus
    compaction, exposed via `net._pd2ppc_lookups["bus"]`), which is what pypowsybl's own
    `LINE-<a>-<b>` / `TWT-<a>-<b>` element ids are actually keyed to. Naively assuming
    `pandapower_bus_id + 1` gives the wrong answer almost everywhere (verified against
    snem1803.m: only 14/1215 lines matched that way vs. 1215/1215 via this lookup). Also a real
    finding: pandapower and pypowsybl classify some of the *same* MATPOWER branches into
    "line" vs. "transformer" differently (snem1803.m: pandapower's own 1215/1580 line/trafo
    split vs. pypowsybl's 1744/1051 line/TWT split on the identical 2795 branches) -- both
    pypowsybl element collections are searched, not just lines.

    Requires `net._pd2ppc_lookups["bus"]` to already be populated -- call
    `pandapower.converter.pypower.to_ppc(net)` (or `to_pypowsybl_network`, which calls
    `to_mpc()` -> `to_ppc()` internally) on `net` before this function.

    Args:
        net: a pandapower net that `to_ppc()`/`to_mpc()` has already been run on.
        pypowsybl_net: the pypowsybl Network loaded from that same net's `.mat` export.

    Returns:
        Maps each `net.line` row index to its pypowsybl element id. Parallel lines sharing the
        same bus pair (this repo's real cases have genuine ones -- e.g. snem1803.m lines
        151/152, both `[175-608]`) are disambiguated by encounter order (ascending `net.line`
        index), matching PowSyBl's own "#0"/"#1" suffixing of duplicate bus-pair names in the
        same branch-matrix row order `to_mpc()` writes them in.
    """
    lookup = net._pd2ppc_lookups["bus"]
    all_ids = set(pypowsybl_net.get_lines().index) | set(
        pypowsybl_net.get_2_windings_transformers().index
    )
    seen_count: dict[tuple[int, int], int] = {}
    id_map: dict[int, str] = {}
    for i, row in net.line.iterrows():
        fb = int(lookup[int(row.from_bus)]) + 1
        tb = int(lookup[int(row.to_bus)]) + 1
        key = (min(fb, tb), max(fb, tb))
        occurrence = seen_count.get(key, 0)
        seen_count[key] = occurrence + 1
        for prefix in ("LINE", "TWT"):
            for base in (f"{prefix}-{fb}-{tb}", f"{prefix}-{tb}-{fb}"):
                candidate = base if occurrence == 0 else f"{base}#{occurrence - 1}"
                if candidate in all_ids:
                    id_map[int(i)] = candidate
                    break
            if int(i) in id_map:
                break
    return id_map


def scale_loads(net: pp.pandapowerNet, scale: float) -> pp.pandapowerNet:
    """Return a deep copy of `net` with every load's p_mw/q_mvar scaled.

    This is the "shunt/load scaling parameter" named in docs/VISION.md's
    Lab 1 spec -- a single scalar the fit loop searches over.
    """
    scaled = copy.deepcopy(net)
    scaled.load["p_mw"] = scaled.load["p_mw"] * scale
    scaled.load["q_mvar"] = scaled.load["q_mvar"] * scale
    return scaled
