#!/usr/bin/env python3
"""Lab 1 (Simple) -- Load-Flow Parameter Fit.

See README.md in this directory for the full walkthrough. Three steps:

    uv run benchmarks/lab01-simple-loadflow-fit/run.py --step load
    uv run benchmarks/lab01-simple-loadflow-fit/run.py --step fit
    uv run benchmarks/lab01-simple-loadflow-fit/run.py --step check

Sandbox note: docs/VISION.md's Lab 1 has a local Phi-4-mini LLM (served by
llama.cpp in a podman pod) choosing each trial load-scaling value over MCP.
A real Phi-4-mini-instruct pod now exists and runs in this sandbox
(kube/llamacpp-phi-pod.yaml, podman-verified -- see kube/README.md), but
this script was not rewired to call it -- docs/VISION.md section 9 names
this lab specifically as the one where "a container is pure overhead," so
the "propose next trial" decision below stays `gridfit.bisection_fit`'s
deterministic bisection policy, named explicitly here and in gridfit.py,
not hidden. The physics on every iteration is a real `pandapower.runpp()`
call either way; that split (LLM/policy proposes, pandapower disposes) is
the actual point of the lab and is unaffected by the swap. Wiring this
script to call the real pod instead is a named, undone follow-up, not a
sandbox impossibility.

The default `--step fit` also (re)writes a network-topology PNG
(sample_network_chart.png) via `pandapower.plotting`
(docs/backlog/0001-topology-and-results-visualization-gap.md item 2 /
docs/backlog/0002's free tier: pandapower.plotting was unused across every
lab despite pandapower being a hard dependency of all of them) -- buses
colored by the fitted network's real solved voltage, TARGET_BUS
highlighted. `--step check` never writes it (it passes
`refresh_chart=False` to `fit_step`, matching Lab 4/5's `refresh_chart`/
`refresh_fixtures` gate); it only asserts the committed PNG exists. See
`_plot_network`'s docstring for the exact pandapower.plotting APIs used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import pandapower as pp  # noqa: E402
import pandapower.plotting as ppl  # noqa: E402


from wattson.gridfit import bisection_fit, load_case, scale_loads  # noqa: E402


class FitStepResult(TypedDict):
    """JSON-serializable result of `fit_step`, also the shape of
    expected_results.json."""

    target_bus: int
    base_case_voltage_pu: float
    field_scada_voltage_pu: float
    fitted_scale: float
    fitted_voltage_pu: float
    residual_pu: float
    iterations: int
    converged: bool
    tolerance_pu: float

LAB_DIR = Path(__file__).resolve().parent
DATA_FILE = LAB_DIR.parent.parent / "data" / "snemSA.m"
EXPECTED_FILE = LAB_DIR / "expected_results.json"

# Bus 2008 is a real bus ID from snemSA.m (11 kV, load 50.0 MW). Chosen (by
# a one-off exploration script, not shipped) as the highest-load bus whose
# base-case voltage sits in [0.9, 1.05] pu -- i.e. a bus whose voltage a
# modeller would plausibly want to calibrate, not one already at 1.0 pu by
# construction. docs/VISION.md's original spec illustrates the mechanic with
# a hypothetical "bus 14"; snemSA.m's real bus IDs are non-sequential
# (986, 1633, 1634, ... not 1..N), so there is no bus literally named 14 --
# 2008 is the real stand-in, named here rather than silently substituted.
TARGET_BUS: int = 2008

# Synthetic "field SCADA reading" for bus 2008. Not a real measurement --
# this lab has no live SCADA feed -- it is a fixed constant chosen so the
# required fit lies strictly inside the +/-10% search bound (SCALE_LO/HI
# below) rather than at its edge, and away from a scale that would make the
# very first bisection probe accidentally exact. The base-case (unscaled)
# voltage at bus 2008 is ~0.9348 pu; 0.9422 is achievable at scale ~0.925x.
FIELD_SCADA_VOLTAGE_PU: float = 0.9422

# "...within 0.002 pu" -- docs/VISION.md section 7, Lab 1 spec, verbatim.
FIT_TOLERANCE_PU: float = 0.002

# "...within +/-10%..." -- docs/VISION.md section 7, Lab 1 spec, verbatim:
# the load-scaling parameter search is bounded to [0.90x, 1.10x] of the
# base-case load.
SCALE_LO: float = 0.90
SCALE_HI: float = 1.10


def load_step(verbose: bool = True) -> tuple[pp.pandapowerNet, float]:
    """Run the "load" step: parse snemSA.m and solve the base-case AC power flow.

    Args:
        verbose: if True, print the same progress lines a presenter would
            narrate live (see README's step-by-step walkthrough).

    Returns:
        The solved pandapower net and the base-case voltage (pu) at
        TARGET_BUS.
    """
    if not DATA_FILE.exists():
        print(
            f"[FAIL] {DATA_FILE} not found -- run "
            f"'uv run scripts/fetch_csiro_nem_data.py' first",
            file=sys.stderr,
        )
        sys.exit(1)

    net, warnings = load_case(DATA_FILE)
    pp.runpp(net)
    base_voltage = float(net.res_bus.at[TARGET_BUS, "vm_pu"])

    if verbose:
        print(
            f"Loaded snemSA.m via powerio: {len(net.bus)} buses, "
            f"{len(net.gen)} generators"
        )
        print(f"Base-case power flow converged: bus {TARGET_BUS} voltage = "
              f"{base_voltage:.3f} pu")
    return net, base_voltage


# Decimal places for values written to JSON (fixture + stdout). This is a
# display/serialization choice only, unrelated to FIT_TOLERANCE_PU (the
# physics convergence tolerance) -- 6 places is comfortably more precision
# than pandapower's Newton-Raphson default tolerance (1e-8 MVA mismatch,
# which maps to ~1e-6 pu voltage precision in practice), so rounding here
# never masks a real difference between runs.
JSON_ROUND_DECIMALS: int = 6

# Float-equality slack for expected_results.json comparison in check_step.
# Deliberately looser than JSON_ROUND_DECIMALS' 1e-6 because pandapower's
# Newton-Raphson iteration count/path can differ by a unit in the last
# place across numpy/BLAS versions without the physics actually changing;
# 1e-4 pu is two orders of magnitude tighter than FIT_TOLERANCE_PU (0.002),
# so it still catches a genuine regression while tolerating solver noise.
FIXTURE_FLOAT_ATOL: float = 1e-4

NETWORK_CHART_FILE = LAB_DIR / "sample_network_chart.png"

# nx.spring_layout()'s force-directed node placement is itself randomized;
# this is a fixed, arbitrary seed for *that* placement RNG only -- same
# convention as Lab 5's generate_topology.py TOPOLOGY_LAYOUT_SEED, kept
# separate from anything physics-related (this lab's load flow has no RNG
# at all) so the plot's node positions are identical across repeated runs.
NETWORK_LAYOUT_SEED: int = 7

# Bus-voltage colormap: "viridis" matches this repo's one other continuous
# (non-categorical) plot, Lab 5's view_spectrogram.py -- perceptually
# uniform and colorblind-safe, unlike a rainbow/jet map (the dataviz
# skill's "sequential = one hue, light->dark" rule; viridis is the closest
# established scientific analogue already in use in this repo).
NETWORK_BUS_CMAP: str = "viridis"

# Line color: the same muted axis/gridline ink as Lab 5's
# generate_topology.py TOPOLOGY_EDGE_COLOR, so lines read as structure, not
# a second data encoding -- this plot's point is bus voltage (what run.py
# calibrates), not line loading.
NETWORK_LINE_COLOR: str = "#898781"

# TARGET_BUS highlight color: the same validated red used for the
# "highlighted / graded-against" role throughout this repo
# (generate_topology.py TOPOLOGY_TAP_NODE_COLOR, reconcile.py
# RECONCILIATION_CHART_ACTUAL_COLOR) -- re-validated for this exact pairing
# via the dataviz skill's own `validate_palette.js "#2a78d6,#e34948"`
# (both `--mode light` and `--mode dark` report ALL CHECKS PASS; see
# reconcile.py's constant comment for the exact command run).
NETWORK_TARGET_BUS_COLOR: str = "#e34948"

# Marker sizes in geodata units (nx.spring_layout's output coordinates sit
# roughly in [-1, 1] per axis -- same convention generate_topology.py notes
# for TOPOLOGY_LABEL_Y_OFFSET). Chosen by visual inspection so this lab's
# 503 ordinary buses stay legible as distinct dots and the highlighted
# TARGET_BUS is unmistakably larger, mirroring generate_topology.py's
# TOPOLOGY_BUS_NODE_SIZE / TOPOLOGY_TAP_NODE_SIZE emphasis pattern.
NETWORK_BUS_SIZE: float = 0.012
NETWORK_TARGET_BUS_SIZE: float = 0.03

# Matches Lab 4/5's savefig dpi convention (reconcile.py
# RECONCILIATION_CHART_DPI / generate_topology.py TOPOLOGY_PLOT_DPI);
# figure sized like generate_topology.py's graph layout (8x6, square-ish),
# not verify_stream.py's wide time-series.
NETWORK_CHART_FIGSIZE: tuple[float, float] = (8.0, 6.0)
NETWORK_CHART_DPI: int = 130


def _plot_network(
    net: pp.pandapowerNet, target_bus: int, load_scale: float, path: Path
) -> None:
    """Render the fitted network's solved topology via `pandapower.plotting`
    -- until now this lab (and every other lab) never called it, despite
    pandapower being a hard dependency of all five
    (docs/backlog/0001-topology-and-results-visualization-gap.md item 2;
    docs/backlog/0002's free tier recommends exactly this).

    Buses are colored by their real solved `vm_pu` (a genuine
    `pandapower.runpp()` result on `net`, never fabricated -- AGENTS.md
    "Real data over synthetic data"), lines are drawn as plain structure
    (this plot's point is bus voltage, not line loading -- see
    NETWORK_LINE_COLOR), and `target_bus` -- the bus run.py's bisection
    search calibrates against the synthetic field-SCADA reading -- is
    highlighted and labelled so a reader can see at a glance where the
    fitted target sits in the wider network.

    `net` carries no real geographic coordinates (snemSA.m has none), so
    this function builds its own artificial layout via
    `networkx.spring_layout` -- the same approach and layout-seed
    convention as Lab 5's generate_topology.py -- rather than pandapower's
    own `pandapower.plotting.create_generic_coordinates`, whose only two
    working backends in this sandbox (`igraph`, or `networkx` via
    `graphviz_layout`/pygraphviz) both raise ImportError here (verified
    directly: neither `igraph` nor `pygraphviz` is installed, and adding
    either would be a new dependency docs/backlog/0002 says to avoid when
    a free-tier option exists). The computed layout is written to
    `net.bus["geo"]` as GeoJSON Point strings via
    `pandapower.plotting.geojson` -- pandapower's own re-exported geojson
    module (a hard transitive dependency of pandapower itself, required by
    pandapower's own `pyproject.toml`, not a new one this file adds) --
    rather than importing the top-level `geojson` package directly, so
    this function stays entirely inside pandapower's own public plotting
    surface.

    Args:
        net: a solved pandapower net (net.converged is True, net.res_bus
            populated by a real runpp() call). Mutated in place (its
            `bus["geo"]` column is (re)written with the computed layout).
        target_bus: the bus index to highlight (run.py's TARGET_BUS).
        load_scale: the fitted load-scale factor, for the plot title only.
        path: output PNG path.
    """
    graph = nx.Graph()
    graph.add_nodes_from(net.bus.index)
    for _, row in net.line.iterrows():
        graph.add_edge(int(row["from_bus"]), int(row["to_bus"]))
    for _, row in net.trafo.iterrows():
        graph.add_edge(int(row["hv_bus"]), int(row["lv_bus"]))
    pos = nx.spring_layout(graph, seed=NETWORK_LAYOUT_SEED)
    net.bus["geo"] = [
        ppl.geojson.dumps(
            ppl.geojson.Point((float(pos[b][0]), float(pos[b][1]))), sort_keys=True
        )
        for b in net.bus.index
    ]

    line_collection = ppl.create_line_collection(
        net, color=NETWORK_LINE_COLOR, use_bus_geodata=True, linewidths=0.8,
    )
    bus_collection = ppl.create_bus_collection(
        net, size=NETWORK_BUS_SIZE, z=net.res_bus.vm_pu, cmap=NETWORK_BUS_CMAP,
        cbar_title="Bus voltage (pu)",
    )
    highlight_collection = ppl.create_bus_collection(
        net, buses=[target_bus], size=NETWORK_TARGET_BUS_SIZE,
        color=NETWORK_TARGET_BUS_COLOR, plot_colormap=False,
    )

    fig, ax = plt.subplots(figsize=NETWORK_CHART_FIGSIZE)
    ppl.draw_collections(
        [line_collection, bus_collection, highlight_collection],
        ax=ax, plot_colorbars=True,
    )
    target_x, target_y = pos[target_bus]
    ax.annotate(
        f"bus {target_bus}\n(calibration target)",
        xy=(target_x, target_y),
        xytext=(target_x + 0.15, target_y + 0.15),
        fontsize=9, fontweight="bold", color=NETWORK_TARGET_BUS_COLOR,
        arrowprops=dict(arrowstyle="->", color=NETWORK_TARGET_BUS_COLOR, lw=1.2),
    )
    ax.set_title(
        f"Lab 1 fitted network -- bus voltages (pu), load scale {load_scale:.4f}x"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=NETWORK_CHART_DPI)
    plt.close(fig)


def fit_step(verbose: bool = True, refresh_chart: bool = True) -> FitStepResult:
    """Run the "fit" step: bisect the load-scaling parameter against the
    synthetic field-SCADA target at TARGET_BUS.

    Args:
        verbose: if True, print one line per bisection iteration plus the
            final converged/failed summary line.
        refresh_chart: if True, (re)write the committed NETWORK_CHART_FILE
            PNG from this run's real fitted-network output -- mirrors Lab
            4's reconcile() / Lab 5's generate_topology.py `refresh_chart`/
            `refresh_fixtures` gate. check_step() passes False so a
            self-check re-derivation never mutates the committed chart it
            is about to assert the existence of. Unlike Lab 4/5, this lab
            has no per-run seed/date parameter that could cause fixture
            drift -- fit_step() is otherwise deterministic -- so this flag
            exists purely to keep `--step check` a read-only assertion, not
            to guard against a varying input.

    Returns:
        A FitStepResult, JSON-serializable and diffable against
        expected_results.json (see check_step).
    """
    net, base_voltage = load_step(verbose=verbose)

    def evaluate(scale: float) -> float:
        """The physics ground truth for one trial `scale`: a real pandapower
        AC power-flow solve, never an LLM guess -- see module docstring."""
        trial_net = scale_loads(net, scale)
        pp.runpp(trial_net)
        return float(trial_net.res_bus.at[TARGET_BUS, "vm_pu"])

    result = bisection_fit(
        evaluate,
        target=FIELD_SCADA_VOLTAGE_PU,
        lo=SCALE_LO,
        hi=SCALE_HI,
        tol=FIT_TOLERANCE_PU,
    )

    if verbose:
        for it in result.iterations:
            print(
                f"iter {it.iteration}: trial={it.trial:.4f}x -> "
                f"{it.observed:.3f} pu (residual {it.residual:+.4f})"
            )
        status = "PASS" if result.converged else "FAIL (did not converge)"
        print(
            f"converged: trial={result.trial:.4f}x, bus {TARGET_BUS} = "
            f"{result.observed:.3f} pu, residual {result.residual:+.4f} "
            f"({status}, tol {FIT_TOLERANCE_PU})"
        )

    if refresh_chart:
        final_net = scale_loads(net, result.trial)
        pp.runpp(final_net)
        _plot_network(final_net, TARGET_BUS, result.trial, NETWORK_CHART_FILE)
        if verbose:
            print(f"[chart] wrote {NETWORK_CHART_FILE.name}")

    return {
        "target_bus": TARGET_BUS,
        "base_case_voltage_pu": round(base_voltage, JSON_ROUND_DECIMALS),
        "field_scada_voltage_pu": FIELD_SCADA_VOLTAGE_PU,
        "fitted_scale": round(result.trial, JSON_ROUND_DECIMALS),
        "fitted_voltage_pu": round(result.observed, JSON_ROUND_DECIMALS),
        "residual_pu": round(result.residual, JSON_ROUND_DECIMALS),
        "iterations": len(result.iterations),
        "converged": result.converged,
        "tolerance_pu": FIT_TOLERANCE_PU,
    }


def check_step() -> bool:
    """Run the "check" step: re-run the fit and diff it against
    expected_results.json.

    Also asserts the committed NETWORK_CHART_FILE PNG artifact exists
    (mirroring Lab 4/5's own CHART_FILE/SAMPLE_TOPOLOGY_PLOT_FILE.exists()
    checks) so the self-check gate actually covers the network-diagram
    artifact per AGENTS.md's "every lab is self-checking" convention, not
    just the JSON fixture.

    Returns:
        True if every compared field matches within FIXTURE_FLOAT_ATOL
        (floats) or exactly (non-floats) and the committed chart PNG
        exists; False otherwise. This is the self-checking gate named in
        docs/DEFINITION_OF_DONE.md ("its printed result matches
        expected_results.json within the documented tolerance on every
        run").
    """
    # refresh_chart=False: a self-check re-derivation must never mutate the
    # committed chart it is about to assert the existence of (mirrors
    # reconcile.py's check_step() passing refresh_chart=False).
    actual = fit_step(verbose=False, refresh_chart=False)

    if not EXPECTED_FILE.exists():
        print(f"[FAIL] no fixture at {EXPECTED_FILE}", file=sys.stderr)
        return False

    expected = json.loads(EXPECTED_FILE.read_text())

    mismatches = []
    for key in ("fitted_scale", "fitted_voltage_pu", "residual_pu", "iterations"):
        exp_val, act_val = expected[key], actual[key]
        if isinstance(exp_val, float):
            ok = abs(exp_val - act_val) <= FIXTURE_FLOAT_ATOL
        else:
            ok = exp_val == act_val
        if not ok:
            mismatches.append((key, exp_val, act_val))

    print(json.dumps(actual, indent=2))
    if mismatches:
        print("FAIL: fixture mismatch")
        for key, exp_val, act_val in mismatches:
            print(f"  {key}: expected={exp_val} actual={act_val}")
        return False

    if not NETWORK_CHART_FILE.exists():
        print(f"FAIL: no chart at {NETWORK_CHART_FILE}")
        return False

    print(
        f"MATCH: fitted_scale={actual['fitted_scale']} "
        f"residual_pu={actual['residual_pu']} vs expected_results.json"
    )
    return True


def main() -> None:
    """CLI entry point: dispatches to load_step / fit_step / check_step
    per --step, exiting non-zero on --step check failure (so this doubles
    as a CI/pytest-friendly gate, not just a demo script)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step", choices=["load", "fit", "check"], default="check"
    )
    args = parser.parse_args()

    if args.step == "load":
        load_step()
    elif args.step == "fit":
        fit_step()
    elif args.step == "check":
        ok = check_step()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
