#!/usr/bin/env python3
"""Lab 2 (Medium) -- Interconnection / Asset-Provisioning Screening.

See README.md in this directory for the full walkthrough. Four steps:

    uv run benchmarks/lab02-medium-interconnection-screening/workflow.py --step base
    uv run benchmarks/lab02-medium-interconnection-screening/workflow.py --step contingencies
    uv run benchmarks/lab02-medium-interconnection-screening/workflow.py --step check-limits
    uv run benchmarks/lab02-medium-interconnection-screening/workflow.py --step memo [--approve APPROVE]

check-limits also (re)renders a committed sample_contingency_chart.png of the
two planning-band numbers per contingency (see _plot_contingency_table); the
--step check gate asserts that chart exists alongside the JSON fixture.

Sandbox note: docs/VISION.md's Lab 2 runs this as a Microsoft Agent Framework
Sequential+Concurrent workflow, calling a podman-hosted PowerMCP pandapower
server for every physics step. A real PowerMCP pandapower pod now exists and
runs in this sandbox (kube/powermcp-pandapower-pod.yaml, podman-verified --
see kube/README.md), but this script was not rewired to call it over MCP --
that rewiring is a named, undone follow-up, not a sandbox impossibility. The
four steps below are the same sequential/concurrent shape (a sequential
base-case solve, a *genuinely* concurrent N-1 fan-out via
ProcessPoolExecutor, a sequential limit-check, then a gated memo step)
implemented as direct pandapower calls in-process instead of MCP tool calls.
The physics, the parallelism, and the human-in-the-loop gate are all real;
only the transport (MCP-over-a-pod vs. an in-process function call) is
swapped, and that swap is the one named in docs/VISION.md section 9 itself
("for a single interactive fit... a container is pure overhead") -- Lab 2 is
the first place a live PowerMCP pod would earn its keep, and this file is
written so dropping in an MCP client call in place of `run_contingency()` is
the only change needed later.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, TypedDict

# Agg (non-interactive) backend before any pyplot import, matching Lab 4/5's
# chart code -- Lab 2 is a headless script, never a GUI window.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


import pandapower as pp
from wattson import fetch  # noqa: E402
from wattson.gridfit import load_case

LAB_DIR = Path(__file__).resolve().parent
DATA_FILE = fetch.case_dir() / "snem1803.m"
EXPECTED_FILE = LAB_DIR / "expected_contingency_table.json"

# Bus 175 is a real bus ID from snem1803.m: 132 kV, graph degree 11 (11
# lines directly incident). Chosen (by a one-off exploration script, not
# shipped) as a 132 kV bus -- a typical sub-transmission interconnection
# voltage -- whose 2-hop neighbourhood contains 21 lines, matching
# docs/VISION.md's "drop each of the ~20 lines local to the connection
# point" spec almost exactly.
CANDIDATE_BUS: int = 175

# "...a hypothetical new 250 MW generator connection..." -- docs/VISION.md
# section 7, Lab 2 spec, verbatim.
CANDIDATE_GEN_MW: float = 250.0

# Simplified planning band: a single flat voltage window and thermal limit,
# used here as a documented approximation of NER Schedule 5.1a's
# per-voltage-level "normal voltage fluctuation limits" -- a real screening
# submission would consult the exact S5.1a table for each nominal voltage
# level (which varies per kV band) rather than this one flat band. Named
# explicitly so nobody mistakes this for the real regulatory table.
# 0.90-1.10 pu is the commonly-cited rule-of-thumb planning window spanning
# most NER S5.1a voltage bands; 100% is "at or under nameplate thermal
# rating", the standard N-1 thermal criterion.
VOLTAGE_BAND_PU: tuple[float, float] = (0.90, 1.10)
THERMAL_LIMIT_PERCENT: float = 100.0

# BFS depth for "lines local to the connection point" (docs/VISION.md
# section 7, Lab 2 spec). 2 hops from CANDIDATE_BUS yields 21 lines for bus
# 175 -- matches the spec's "~20 lines" without hand-picking a line list.
CONTINGENCY_BFS_HOPS: int = 2

# Safety cap on how many lines local_contingency_lines() can return. Not
# reached for CANDIDATE_BUS (21 < 25); it exists so a future re-pointing of
# CANDIDATE_BUS at a very high-degree bus fails safely (fewer contingencies
# than expected) rather than fanning out an unbounded number of processes.
CONTINGENCY_LINE_CAP: int = 25

# The exact string a human must type at the memo_step() prompt, and the
# exact value --approve must be given for the non-interactive proof-run
# path. One named constant so the two call sites can't drift apart.
APPROVE_TOKEN: str = "APPROVE"

# Float-equality slack for expected_contingency_table.json comparison in
# check_step, applied to worst-case bus voltage (pu). Matches Lab 1's
# FIXTURE_FLOAT_ATOL rationale: looser than JSON print precision to absorb
# solver last-bit noise across numpy/BLAS versions, tighter than any
# planning-relevant voltage difference.
FIXTURE_VOLTAGE_ATOL: float = 1e-4

# Committed sample chart: the two planning-band numbers check_limits() already
# evaluates, rendered per contingency -- same convention as Lab 4's
# sample_reconciliation_chart.png / Lab 5's sample_topology_plot.png ("the
# chart is a rendering of an already-verified result, not a new source of
# truth", docs/backlog/0003). Deterministic output (same case + same lines =>
# same numbers => same pixels), (re)written on every --step check-limits run
# and asserted to exist by check_step() -- never pixel-diffed.
CONTINGENCY_CHART_FILE: Path = LAB_DIR / "sample_contingency_chart.png"

# Bar colors for _plot_contingency_table()'s two stacked panels. Same hexes
# as Lab 4/5's charts (dataviz skill categorical palette slot 1 blue / slot 8
# red, validated colorblind-safe as a pair via that skill's
# validate_palette.js -- not a script in this repo; see Lab 4's
# reconcile.py for the measured CVD/normal-vision numbers). The loading panel
# takes the blue main-series slot and the voltage panel the red emphasis
# slot -- each panel plots one quantity, so the two colors just keep the
# stacked panels from blending together.
CONTINGENCY_CHART_LOADING_COLOR: str = "#2a78d6"
CONTINGENCY_CHART_VOLTAGE_COLOR: str = "#e34948"
# Dashed limit/band lines and the footnote ink (dataviz skill "Muted
# (axis/labels)" role), same value as Lab 3/5's footnote/grid colors.
CONTINGENCY_CHART_LIMIT_COLOR: str = "#898781"

# Matches Lab 4/5's savefig dpi=130 convention. Two stacked panels sharing one
# x-axis of 21 contingency-line labels need a taller figure than Lab 4's
# (7, 5) single-panel chart.
CONTINGENCY_CHART_FIGSIZE: tuple[float, float] = (9.0, 7.0)
CONTINGENCY_CHART_DPI: int = 130

# Fixed y-axis limits so the 100% thermal-limit line and the 0.90-1.10 pu
# band are always visible with headroom regardless of the data (real values
# here are ~97.8% loading / ~0.899 pu). Purely cosmetic placement, not a
# planning constant.
CONTINGENCY_CHART_LOADING_YMAX: float = 120.0
CONTINGENCY_CHART_VOLTAGE_YLIM: tuple[float, float] = (0.85, 1.15)


class ContingencyResult(TypedDict):
    """Outcome of dropping one line and re-solving AC power flow."""

    line: int
    from_bus: int
    to_bus: int
    converged: bool
    worst_voltage_pu: Optional[float]
    worst_voltage_bus: Optional[int]
    worst_loading_percent: Optional[float]
    worst_loading_line: Optional[int]


# Functional TypedDict syntax (not the class form used above) because the
# real dict key is the Python keyword "pass" -- check_limits() below sets
# row["pass"], matching the printed table's "pass" column and the
# expected_contingency_table.json field name.
ContingencyCheckRow = TypedDict(
    "ContingencyCheckRow",
    {
        "line": int,
        "from_bus": int,
        "to_bus": int,
        "converged": bool,
        "worst_voltage_pu": Optional[float],
        "worst_voltage_bus": Optional[int],
        "worst_loading_percent": Optional[float],
        "worst_loading_line": Optional[int],
        "pass": bool,
        "reason": str,
    },
)


def _adjacency(net: pp.pandapowerNet) -> dict[int, list[tuple[int, int]]]:
    """Build an undirected bus adjacency list from `net.line`.

    Returns:
        Maps each bus id to a list of (neighbour_bus, line_index) pairs.
    """
    adj: dict[int, list[tuple[int, int]]] = {}
    for i, row in net.line.iterrows():
        adj.setdefault(row.from_bus, []).append((row.to_bus, i))
        adj.setdefault(row.to_bus, []).append((row.from_bus, i))
    return adj


def local_contingency_lines(
    net: pp.pandapowerNet,
    start_bus: int,
    hops: int = CONTINGENCY_BFS_HOPS,
    cap: int = CONTINGENCY_LINE_CAP,
) -> list[int]:
    """Lines within `hops` network hops of `start_bus` -- deterministic
    breadth-first search, returned sorted so run order is reproducible.

    Args:
        net: the pandapower network to search.
        start_bus: the candidate interconnection bus (CANDIDATE_BUS).
        hops: BFS depth; see CONTINGENCY_BFS_HOPS for why 2.
        cap: stop expanding once this many lines are found; see
            CONTINGENCY_LINE_CAP.

    Returns:
        Sorted line indices to screen as N-1 contingencies.
    """
    adj = _adjacency(net)
    seen_buses = {start_bus}
    seen_lines: set[int] = set()
    frontier = [start_bus]
    for _ in range(hops):
        nxt = []
        for b in frontier:
            for nb, li in adj.get(b, []):
                seen_lines.add(li)
                if nb not in seen_buses:
                    seen_buses.add(nb)
                    nxt.append(nb)
        frontier = nxt
        if len(seen_lines) >= cap:
            break
    return sorted(seen_lines)


def build_base_net() -> pp.pandapowerNet:
    """Load snem1803.m and attach the candidate generator (not yet solved).

    Returns:
        A pandapower net with CANDIDATE_GEN_MW added at CANDIDATE_BUS.
    """
    if not DATA_FILE.exists():
        print(
            f"[FAIL] {DATA_FILE} not found -- run "
            f"'uv run scripts/fetch_csiro_nem_data.py' first",
            file=sys.stderr,
        )
        sys.exit(1)
    net, warnings = load_case(DATA_FILE)
    pp.create_gen(
        net,
        bus=CANDIDATE_BUS,
        p_mw=CANDIDATE_GEN_MW,
        vm_pu=1.0,
        name="candidate-interconnection-gen",
    )
    return net


def run_base_case(verbose: bool = True) -> pp.pandapowerNet:
    """Sequential step 1: solve the base-case AC power flow with the
    candidate generator attached.

    Args:
        verbose: if True, print the same progress lines a presenter would
            narrate live.

    Returns:
        The solved pandapower net.
    """
    net = build_base_net()
    # snem1803.m's default DC-flat pre-solve hits a divide-by-zero (a
    # zero-impedance branch typical of MATPOWER bus-merge modeling);
    # init="flat" skips that pre-solve and the AC Newton-Raphson converges
    # cleanly. Documented deviation from a bare pp.runpp(net) call.
    pp.runpp(net, init="flat")
    if verbose:
        print(
            f"Loaded snem1803.m, attached candidate {CANDIDATE_GEN_MW:.0f} MW "
            f"generator at bus {CANDIDATE_BUS}"
        )
        print(f"Base-case power flow converged: {net.converged}")
    return net


def base_case_weak_buses() -> set[int]:
    """Buses already outside VOLTAGE_BAND_PU in the base case, before any
    contingency is applied.

    Real-data finding, not a hypothetical: snem1803.m's bus 1126 sits at
    0.899 pu even in the base case with the candidate generator attached,
    independent of CANDIDATE_BUS or which line is later dropped. check_limits
    uses this set so a contingency's reported voltage breach is labelled
    "pre-existing (base case)" rather than implied to be caused by that
    specific line outage -- an honest screen distinguishes the two per
    docs/VISION.md's "made deterministically against documented criteria,
    not eyeballed."

    Returns:
        Bus ids already outside VOLTAGE_BAND_PU with no contingency applied.
    """
    net = run_base_case(verbose=False)
    lo, hi = VOLTAGE_BAND_PU
    outside = net.res_bus.vm_pu[(net.res_bus.vm_pu < lo) | (net.res_bus.vm_pu > hi)]
    return set(int(b) for b in outside.index)


def base_case_weak_lines() -> set[int]:
    """Lines already above THERMAL_LIMIT_PERCENT in the base case, before
    any contingency is applied.

    Mirror of base_case_weak_buses() for the thermal criterion, added so
    check_limits() can label a loading breach with the same provenance it
    already labels voltage breaches with. Real-data finding: in snem1803.m
    no line breaches the 100% thermal limit in the base case (the highest
    base loadings are line 1070 at ~97.8% and line 79 at ~96.8%), so every
    loading breach this screen reports is genuinely contingency-induced --
    in particular dropping line 151 overloads its parallel twin line 152
    (both [175-608]) to 113.0%, and vice versa to 111.4%, which is exactly
    the double-circuit N-1 case a screening study exists to catch.

    Returns:
        Line indices already above THERMAL_LIMIT_PERCENT with no
        contingency applied.
    """
    net = run_base_case(verbose=False)
    over = net.res_line.loading_percent[
        net.res_line.loading_percent > THERMAL_LIMIT_PERCENT
    ]
    return set(int(li) for li in over.index)


def _run_one_contingency(net_json: str, line_idx: int) -> ContingencyResult:
    """Concurrent-step worker: runs in its own OS process (submitted to a
    ProcessPoolExecutor by run_contingencies), given the base net serialized
    to JSON (pandapowerNet objects cross a process boundary via
    pp.to_json/pp.from_json_string, not raw pickling, since some of their
    internal state -- e.g. compiled numba functions -- is not pickle-safe).

    Args:
        net_json: `pp.to_json(base_net)`.
        line_idx: index into `net.line` to take out of service.

    Returns:
        The contingency's outcome; `converged=False` with an `error` key
        if pandapower's Newton-Raphson solve raised (rather than the caller
        having to catch an exception from inside a worker process).
    """
    net = pp.from_json_string(net_json)
    net.line.at[line_idx, "in_service"] = False
    entry: dict = {
        "line": int(line_idx),
        "from_bus": int(net.line.at[line_idx, "from_bus"]),
        "to_bus": int(net.line.at[line_idx, "to_bus"]),
    }
    try:
        pp.runpp(net, init="flat")
        entry["converged"] = bool(net.converged)
        entry["worst_voltage_pu"] = float(net.res_bus.vm_pu.min())
        entry["worst_voltage_bus"] = int(net.res_bus.vm_pu.idxmin())
        entry["worst_loading_percent"] = float(net.res_line.loading_percent.max())
        entry["worst_loading_line"] = int(net.res_line.loading_percent.idxmax())
    except Exception as exc:  # pandapower raises on non-convergence
        entry["converged"] = False
        entry["error"] = str(exc)
        entry["worst_voltage_pu"] = None
        entry["worst_voltage_bus"] = None
        entry["worst_loading_percent"] = None
        entry["worst_loading_line"] = None
    return entry


def run_contingencies(verbose: bool = True) -> list[ContingencyResult]:
    """Concurrent step: fan out the N-1 screen across real OS processes.

    Uses ProcessPoolExecutor (not a thread pool) so the contingencies
    genuinely run in parallel OS processes rather than being serialized by
    the GIL -- the point named in docs/VISION.md's Lab 2 spec ("run as
    parallel tool calls, not a visible serial loop -- the point is that
    they genuinely overlap").

    Args:
        verbose: if True, print one line per contingency as it completes,
            in completion order (as_completed), not submission order.

    Returns:
        All contingency results, re-sorted by line index for determinism
        (as_completed's arrival order is not itself deterministic).
    """
    net = run_base_case(verbose=False)
    lines = local_contingency_lines(net, CANDIDATE_BUS)
    net_json = pp.to_json(net)  # filename=None -> returns the JSON string

    results: list[dict] = []
    total = len(lines)
    with ProcessPoolExecutor() as pool:
        futures = {
            pool.submit(_run_one_contingency, net_json, li): li for li in lines
        }
        completed = 0
        for future in as_completed(futures):
            completed += 1
            entry = future.result()
            results.append(entry)
            if verbose:
                status = "no violations" if entry["converged"] else "DID NOT CONVERGE"
                print(
                    f"Contingency {completed}/{total} complete "
                    f"(line {entry['line']} [{entry['from_bus']}-{entry['to_bus']}] "
                    f"dropped: {status})"
                )
    results.sort(key=lambda r: r["line"])
    return results


def check_limits(
    results: Optional[list[ContingencyResult]] = None,
    verbose: bool = True,
    refresh_chart: bool = True,
) -> list[ContingencyCheckRow]:
    """Sequential step: score each contingency against VOLTAGE_BAND_PU and
    THERMAL_LIMIT_PERCENT -- deterministic rule evaluation, not an LLM
    judgment call, per docs/VISION.md's "made deterministically against
    documented criteria, not eyeballed."

    Args:
        results: contingency results to check; runs them via
            run_contingencies() if not supplied.
        verbose: if True, print the pass/fail table.
        refresh_chart: if True, (re)render the committed CONTINGENCY_CHART_FILE
            PNG from this run's real table -- mirrors Lab 4/5's gating of their
            committed sample charts. check_step() passes False so a self-check
            re-derivation never mutates the committed chart it is about to
            assert the existence of; memo_step() passes False too (the memo
            doesn't need the chart).

    Returns:
        One ContingencyCheckRow per contingency, each result plus a
        `pass` bool and human-readable `reason`.
    """
    if results is None:
        results = run_contingencies(verbose=False)
    weak_buses = base_case_weak_buses()
    weak_lines = base_case_weak_lines()

    table = []
    for r in results:
        if not r["converged"]:
            table.append({**r, "pass": False, "reason": "non-convergence"})
            continue
        v_ok = VOLTAGE_BAND_PU[0] <= r["worst_voltage_pu"] <= VOLTAGE_BAND_PU[1]
        t_ok = r["worst_loading_percent"] <= THERMAL_LIMIT_PERCENT
        reasons = []
        if not v_ok:
            provenance = (
                "pre-existing in base case, not caused by this contingency"
                if r["worst_voltage_bus"] in weak_buses
                else "contingency-induced"
            )
            reasons.append(
                f"voltage {r['worst_voltage_pu']:.3f} pu at bus "
                f"{r['worst_voltage_bus']} outside {VOLTAGE_BAND_PU} ({provenance})"
            )
        if not t_ok:
            provenance = (
                "pre-existing in base case, not caused by this contingency"
                if r["worst_loading_line"] in weak_lines
                else "contingency-induced"
            )
            reasons.append(
                f"loading {r['worst_loading_percent']:.1f}% on line "
                f"{r['worst_loading_line']} exceeds {THERMAL_LIMIT_PERCENT}% "
                f"({provenance})"
            )
        table.append(
            {**r, "pass": v_ok and t_ok, "reason": "; ".join(reasons) or "within limits"}
        )

    if verbose:
        print(
            f"{'line':>5} {'from':>6} {'to':>6} {'worst_v_pu':>11} "
            f"{'worst_load%':>12} {'pass':>5}  reason"
        )
        for row in table:
            wv = f"{row['worst_voltage_pu']:.3f}" if row["worst_voltage_pu"] is not None else "n/a"
            wl = (
                f"{row['worst_loading_percent']:.1f}"
                if row["worst_loading_percent"] is not None
                else "n/a"
            )
            mark = "PASS" if row["pass"] else "FAIL"
            print(
                f"{row['line']:>5} {row['from_bus']:>6} {row['to_bus']:>6} "
                f"{wv:>11} {wl:>12} {mark:>5}  {row['reason']}"
            )
        breaches = sum(1 for row in table if not row["pass"])
        print(f"{len(table)} contingencies screened, {breaches} breach(es)")

    if refresh_chart:
        _plot_contingency_table(table, CONTINGENCY_CHART_FILE)
        print(f"[chart] wrote {CONTINGENCY_CHART_FILE.name}")

    return table


def _plot_contingency_table(
    table: list[ContingencyCheckRow], path: Path
) -> None:
    """Render the two planning-band numbers check_limits() already evaluates,
    per screened line (docs/backlog/0001 item 4: "Lab 2's N-1 contingency
    screen (loading vs. limit) ... a classic bar/time-series comparison"):
    worst-case line loading % against the 100% thermal limit, and worst-case
    bus voltage pu against the 0.90-1.10 pu normal band.

    Two stacked panels sharing one x-axis (the screened line indices). Not a
    `pandapower.plotting` network overlay (docs/backlog/0002's cheapest tier):
    the N-1 result is a per-contingency table, not per-bus state on one
    network, so a grouped bar is the legible shape -- same choice as Lab 3/4's
    charts. The honest real-data picture (see base_case_weak_lines() and the
    README "Sandbox notes"): 19 of 21 contingencies land on the pre-existing
    network-wide worst point (bus 1126 at ~0.899 pu, line 1070 at ~97.8%
    loading) so those bars are flat -- but dropping line 151 overloads its
    parallel twin 152 (both [175-608]) to 113.0%, and vice versa to 111.4%,
    which the loading panel shows as the only bars crossing the 100% line.
    The footnote states both the pre-existing voltage condition and the
    151/152 finding rather than implying the chart is either flat or clean.

    Matches Lab 4/5's matplotlib convention (Agg backend, fig/ax, tight_layout,
    dpi=CONTINGENCY_CHART_DPI, plt.close(fig)).

    Args:
        table: output of check_limits().
        path: output PNG path.

    Raises:
        ValueError: if every row is non-converged (nothing real to plot).
    """
    rows = [r for r in table if r["converged"]]
    if not rows:
        raise ValueError("no converged contingency rows to plot")

    line_ids = [r["line"] for r in rows]
    loadings = [r["worst_loading_percent"] for r in rows]
    voltages = [r["worst_voltage_pu"] for r in rows]
    x_positions = range(len(rows))

    fig, (ax_loading, ax_voltage) = plt.subplots(
        2, 1, figsize=CONTINGENCY_CHART_FIGSIZE, sharex=True
    )

    ax_loading.bar(x_positions, loadings, color=CONTINGENCY_CHART_LOADING_COLOR)
    ax_loading.axhline(
        THERMAL_LIMIT_PERCENT, ls="--", lw=1.0,
        color=CONTINGENCY_CHART_LIMIT_COLOR,
        label=f"thermal limit {THERMAL_LIMIT_PERCENT:.0f}%",
    )
    ax_loading.set_ylabel("worst line loading (%)")
    ax_loading.set_ylim(0, CONTINGENCY_CHART_LOADING_YMAX)
    ax_loading.legend(loc="lower left", fontsize=8)
    ax_loading.grid(True, axis="y", linewidth=0.4, alpha=0.4)

    band_lo, band_hi = VOLTAGE_BAND_PU
    ax_voltage.bar(x_positions, voltages, color=CONTINGENCY_CHART_VOLTAGE_COLOR)
    ax_voltage.axhspan(
        band_lo, band_hi, color=CONTINGENCY_CHART_LIMIT_COLOR, alpha=0.12,
        label=f"normal band {band_lo:.2f}-{band_hi:.2f} pu",
    )
    ax_voltage.set_ylabel("worst bus voltage (pu)")
    ax_voltage.set_ylim(*CONTINGENCY_CHART_VOLTAGE_YLIM)
    ax_voltage.legend(loc="lower left", fontsize=8)
    ax_voltage.grid(True, axis="y", linewidth=0.4, alpha=0.4)

    ax_voltage.set_xticks(list(x_positions))
    ax_voltage.set_xticklabels([str(li) for li in line_ids], rotation=90, fontsize=7)
    ax_voltage.set_xlabel("line index (dropped in N-1)")

    worst_voltage_row = min(rows, key=lambda r: r["worst_voltage_pu"])
    worst_loading_row = max(rows, key=lambda r: r["worst_loading_percent"])
    induced = [
        r for r in rows
        if "contingency-induced" in r["reason"]
    ]
    if induced:
        induced_desc = "; ".join(
            f"dropping line {r['line']} overloads line {r['worst_loading_line']} "
            f"to {r['worst_loading_percent']:.1f}%"
            for r in sorted(induced, key=lambda r: -r["worst_loading_percent"])
        )
        note = (
            f"{len(induced)} contingency-induced breach(es): {induced_desc}; "
            f"bus {worst_voltage_row['worst_voltage_bus']} at "
            f"{worst_voltage_row['worst_voltage_pu']:.3f} pu is a separate "
            f"pre-existing base-case condition, unchanged by any outage"
        )
    else:
        note = (
            f"worst bus {worst_voltage_row['worst_voltage_bus']} "
            f"({worst_voltage_row['worst_voltage_pu']:.3f} pu) and worst line "
            f"{worst_loading_row['worst_loading_line']} "
            f"({worst_loading_row['worst_loading_percent']:.1f}%) are the same "
            f"pre-existing base-case point for all {len(rows)} contingencies -- "
            f"no contingency introduces a new breach"
        )
    fig.suptitle(
        f"Lab 2 -- N-1 contingency screen, candidate {CANDIDATE_GEN_MW:.0f} MW "
        f"at bus {CANDIDATE_BUS}"
    )
    fig.text(
        0.5, 0.005, note,
        ha="center", fontsize=7, color=CONTINGENCY_CHART_LIMIT_COLOR,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(path, dpi=CONTINGENCY_CHART_DPI)
    plt.close(fig)


def draft_memo(table: list[ContingencyCheckRow]) -> str:
    """Draft the plain-English screening memo from a scored contingency table.

    Sandbox note: docs/VISION.md has the LLM draft this memo; this sandbox
    has no live model server (see module docstring), so the memo is a
    plain Python f-string template instead. The template still only
    reports what `table` already says -- it does not decide pass/fail
    itself, matching the "the agent proposes the memo text, it does not
    sign off on itself" line printed at the end of the memo.

    Args:
        table: output of check_limits().

    Returns:
        The memo text (not yet approved -- see memo_step).
    """
    breaches = [row for row in table if not row["pass"]]
    # A row's `reason` can carry BOTH a pre-existing clause (the network-wide
    # worst voltage is bus 1126, pre-existing) AND a contingency-induced clause
    # (dropping line 151 overloads its parallel twin 152) -- so classify on the
    # explicit "contingency-induced" label, not on the absence of
    # "pre-existing" (the old `row not in pre_existing` logic mislabeled the
    # 151/152 rows as entirely pre-existing and hid the real finding).
    contingency_induced = [
        row for row in breaches if "contingency-induced" in row["reason"]
    ]
    pre_existing = [
        row for row in breaches if "pre-existing" in row["reason"]
    ]
    lines = [
        "SCREENING MEMO (DRAFT) -- candidate interconnection",
        "=" * 60,
        f"Candidate: {CANDIDATE_GEN_MW:.0f} MW generator at bus {CANDIDATE_BUS} "
        f"(snem1803.m, CSIRO Synthetic-NEM-2000-Bus mainland case).",
        f"N-1 contingencies screened: {len(table)} lines within "
        f"{CONTINGENCY_BFS_HOPS} network hops of the connection point.",
        f"Planning criteria: voltage in {VOLTAGE_BAND_PU} pu, thermal loading "
        f"<= {THERMAL_LIMIT_PERCENT:.0f}% (simplified NER S5.1a approximation, "
        f"see workflow.py header).",
        "",
    ]
    if contingency_induced:
        lines.append(
            f"RESULT: {len(contingency_induced)} contingency(ies) BREACH limits "
            f"as a direct result of the outage:"
        )
        for row in contingency_induced:
            lines.append(f"  - line {row['line']} [{row['from_bus']}-{row['to_bus']}]: {row['reason']}")
    else:
        lines.append("RESULT: No contingency introduces a *new* limit breach.")
    if pre_existing:
        lines.append(
            f"NOTE: {len(pre_existing)} contingency(ies) also show a "
            f"pre-existing base-case condition (present with or without the "
            f"outage -- see base_case_weak_buses()/base_case_weak_lines() "
            f"docstrings): "
            + ", ".join(f"line {row['line']}" for row in pre_existing)
        )
    lines.append("")
    lines.append(
        "This memo is a draft. It becomes final only once a human reviewer "
        "explicitly approves it below -- the agent proposes the memo text, "
        "it does not sign off on itself."
    )
    return "\n".join(lines)


def memo_step(approve_token: Optional[str]) -> bool:
    """Final step: draft the memo, then genuinely block on human approval.

    This is the human-in-the-loop checkpoint named in
    docs/DEFINITION_OF_DONE.md ("actually blocks until acknowledged, not a
    no-op"): with no `approve_token` and an interactive TTY, this function
    calls `input()` and will not return True until the operator types
    APPROVE_TOKEN. In a non-interactive context (e.g. this repo's
    scripts/run_labs_1_3.sh proof run) it instead requires `approve_token`
    to be passed explicitly via --approve -- it does not auto-approve.

    Args:
        approve_token: value of --approve, or None if not given.

    Returns:
        True if the memo was approved (finalized), False if it remains a
        draft (either explicitly not approved, or blocked awaiting a human
        in a non-interactive context).
    """
    table = check_limits(verbose=False, refresh_chart=False)
    memo = draft_memo(table)
    print(memo)
    print()

    if approve_token == APPROVE_TOKEN:
        print(f"Human-in-the-loop checkpoint: {APPROVE_TOKEN} received -> MEMO FINALIZED.")
        return True

    if not sys.stdin.isatty():
        print(
            "Human-in-the-loop checkpoint: BLOCKED, awaiting human approval. "
            "No TTY attached, so this run cannot prompt interactively. "
            f"Re-run with --approve {APPROVE_TOKEN} to acknowledge (this is "
            "the documented non-interactive acknowledgment path for "
            "scripted proof runs; a live demo instead pauses on a Gradio "
            "gr.Button('Approve') per docs/VISION.md)."
        )
        return False

    typed = input(f"Type {APPROVE_TOKEN} to finalize this memo: ")
    if typed.strip() == APPROVE_TOKEN:
        print(f"Human-in-the-loop checkpoint: {APPROVE_TOKEN} received -> MEMO FINALIZED.")
        return True
    print("Human-in-the-loop checkpoint: not approved -> memo remains DRAFT.")
    return False


def check_step() -> bool:
    """Self-check gate: re-run the N-1 screen and diff it against
    expected_contingency_table.json.

    Also asserts the committed CONTINGENCY_CHART_FILE PNG artifact exists
    (mirroring Lab 4's reconcile.py / Lab 5's generate_topology.py
    `..._FILE.exists()` pattern) so the self-check gate actually covers the
    chart per AGENTS.md's "every lab is self-checking" rule, not just the
    JSON fixture. check_step() never regenerates the chart itself (it calls
    run_contingencies() directly, not check_limits()) -- the committed PNG is
    (re)written by the normal --step check-limits path, where the data is
    deterministic, so a self-check re-derivation can't clobber it.

    Returns:
        True if every contingency's convergence and worst-case voltage
        match the fixture within FIXTURE_VOLTAGE_ATOL and the committed
        chart PNG exists; False otherwise.
    """
    actual = run_contingencies(verbose=False)
    if not EXPECTED_FILE.exists():
        print(f"[FAIL] no fixture at {EXPECTED_FILE}", file=sys.stderr)
        return False
    expected = json.loads(EXPECTED_FILE.read_text())

    ok = True
    if len(actual) != len(expected):
        print(f"FAIL: expected {len(expected)} contingencies, got {len(actual)}")
        ok = False
    for exp, act in zip(expected, actual):
        if exp["line"] != act["line"] or exp["converged"] != act["converged"]:
            print(f"FAIL: mismatch on line {exp['line']}: expected={exp} actual={act}")
            ok = False
            continue
        if exp["converged"]:
            if abs(exp["worst_voltage_pu"] - act["worst_voltage_pu"]) > FIXTURE_VOLTAGE_ATOL:
                print(f"FAIL: voltage mismatch on line {exp['line']}: {exp} vs {act}")
                ok = False
    if not CONTINGENCY_CHART_FILE.exists():
        print(f"[FAIL] no chart at {CONTINGENCY_CHART_FILE}", file=sys.stderr)
        ok = False
    if ok:
        print(f"MATCH: all {len(actual)} contingencies match expected_contingency_table.json")
    return ok


def main() -> None:
    """CLI entry point: dispatches to each workflow step per --step.

    --step check exits non-zero on fixture mismatch so this doubles as a
    CI/pytest-friendly gate; --step memo exits 2 if the human-in-the-loop
    checkpoint did not finalize the memo (distinct from 1, so callers can
    tell "blocked awaiting approval" apart from "fixture mismatch").
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step",
        choices=["base", "contingencies", "check-limits", "memo", "check"],
        default="check",
    )
    parser.add_argument(
        "--approve",
        default=None,
        help=f'Pass "{APPROVE_TOKEN}" to acknowledge the human-in-the-loop '
        "memo gate non-interactively (for scripted/CI proof runs).",
    )
    args = parser.parse_args()

    if args.step == "base":
        run_base_case()
    elif args.step == "contingencies":
        run_contingencies()
    elif args.step == "check-limits":
        check_limits()
    elif args.step == "memo":
        ok = memo_step(args.approve)
        sys.exit(0 if ok else 2)
    elif args.step == "check":
        ok = check_step()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
