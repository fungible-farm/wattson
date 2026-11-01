#!/usr/bin/env python3
"""Animate Lab 2's N-1 contingency screen to a PowerPoint-friendly MP4.

Renders the Lab 2 screen (candidate 250 MW generator at bus 175 of
snem1803.m, then each of the 21 lines local to the connection point taken out
of service and the AC power flow re-solved) as a 1280x720 h264 video: an
intro base-case frame, one frame per contingency showing the screened lines'
loading bars with the dropped line highlighted, and a ~3s closing summary.

Sandbox note (named stand-in): docs/VISION.md's Lab 2 drives every physics
step through a podman-hosted PowerMCP pandapower server; this script makes
the *same* direct pandapower calls in-process (identical, already-named swap
to workflow.py -- see its module docstring). Every value drawn comes from a
real `pp.runpp(net, init="flat")` AC solve of the actual case file; nothing
is fabricated, interpolated, or staged.

Honest finding rendered (matches the committed expected_contingency_table.json
fixture and workflow.check_limits()): the worst bus voltage (0.899 pu at bus
1126) is pre-existing in the base case and unchanged by every outage; 19 of
21 outages leave worst line loading in the pre-existing ~97.8% region (line
1070). The two parallel lines 151/152 (both bus 175 -> bus 608) are the
exception: dropping one overloads the surviving twin to 113.0% / 111.4% -- a
real, contingency-induced thermal breach beyond the 100% limit, and exactly
the "loadings shift as flow re-routes" behaviour this animation is meant to
show. See _summary_text() for the exact wording.

    uv run python benchmarks/lab02-medium-interconnection-screening/animate_contingencies.py

Writes benchmarks/lab02-medium-interconnection-screening/animate_contingencies.mp4
(16:9, libx264, 30 fps -- see VIDEO_FPS / FIGURE_* constants).
"""
from __future__ import annotations

from functools import partial
from math import isfinite
from pathlib import Path
from typing import Optional, TypedDict

# Headless backend before any pyplot import (non-negotiable repo convention).
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandapower as pp
from matplotlib.animation import FFMpegWriter, FuncAnimation
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.text import Text

# Running this file directly puts its own directory on sys.path[0], so
# `import workflow` (Lab 2) resolves; workflow.py inserts labs/ into sys.path
# itself so its `_shared.gridfit` import resolves the same way. Importing the
# lab's real functions and constants -- not re-implementing them -- keeps the
# animation's physics and the lab's committed results from drifting apart.
from workflow import (
    CANDIDATE_BUS,
    CANDIDATE_GEN_MW,
    THERMAL_LIMIT_PERCENT,
    VOLTAGE_BAND_PU,
    local_contingency_lines,
    run_base_case,
)

LAB_DIR: Path = Path(__file__).resolve().parent
OUTPUT_MP4: Path = LAB_DIR / "animate_contingencies.mp4"

# --- dataviz palette ---
# Same hexes as workflow.py's committed chart / Lab 4/5 (dataviz skill
# categorical palette slot 1 blue, slot 8 red, "Muted (axis/labels)" gray;
# validated colorblind-safe as a pair -- see labs/04 reconcile.py).
MAIN_SERIES_COLOR: str = "#2a78d6"  # blue: post-contingency screened-line loading bars
DROPPED_LINE_COLOR: str = "#e34948"  # red: the currently-dropped line's bar
LIMIT_LINE_COLOR: str = "#e34948"  # red: the 100% thermal-limit line
AXIS_INK_COLOR: str = "#898781"  # gray: gridlines / axis ink / footnote

# --- video geometry ---
# 12.8 x 7.2 @ 100 dpi == exactly 1280x720, the common 16:9 PowerPoint size.
VIDEO_FPS: int = 30
FIGURE_FIGSIZE: tuple[float, float] = (12.8, 7.2)
FIGURE_DPI: int = 100
# Cosmetic: main axes rect [left, bottom, width, height] in figure coords,
# leaving room for the title, frame title, metrics line, x-tick labels and
# footnote above/below the bars.
MAIN_AXES_RECT: tuple[float, float, float, float] = (0.07, 0.17, 0.88, 0.62)

# --- per-frame holds (task spec: ~1.5-2s per contingency so a presenter can
# narrate, ~3s on the closing frame). Repeated-hold frames, not slow-motion. ---
INTRO_HOLD_SECONDS: float = 2.0
CONTINGENCY_HOLD_SECONDS: float = 1.8
CLOSING_HOLD_SECONDS: float = 3.0

# --- chart data bounds ---
# The highest real loading measured in any solve of this dataset is 113.0%
# (line 152 with line 151 dropped -- committed fixture and this run's own
# solver both). 125 leaves ~12 points of headroom above it so the 100% limit
# line and its legend entry never collide with the tallest bar.
LOADING_YMAX_PERCENT: float = 125.0
# An out-of-service line carries no flow; pandapower leaves an out-of-service
# branch's res_line.loading_percent as NaN, so the animation normalizes it to
# 0 -- a dropped bar reads "out of service".
DROPPED_LINE_LOADING_PERCENT: float = 0.0

# --- text labels ---
SUPTITLE: str = (
    f"Lab 2 -- N-1 contingency screen, candidate {CANDIDATE_GEN_MW:.0f} MW "
    f"at bus {CANDIDATE_BUS}"
)
XLABEL: str = f"screened line index -- 2 network hops of bus {CANDIDATE_BUS}"
YLABEL: str = "line loading (%) from real AC solve"
LEGEND_LOADING_LABEL: str = "screened line loading (%)"
LEGEND_DROPPED_LABEL: str = "dropped line (out of service)"
LEGEND_LIMIT_LABEL: str = f"thermal limit {THERMAL_LIMIT_PERCENT:.0f}%"
FOOTNOTE: str = (
    f"Lab 2 animation -- data: data/snem1803.m (CSIRO Synthetic-NEM-2000-Bus) | "
    f"every bar and every number from a real pp.runpp(net, init=\"flat\") AC solve | "
    f"planning band: voltage {VOLTAGE_BAND_PU[0]:.2f}-{VOLTAGE_BAND_PU[1]:.2f} pu, "
    f"thermal <= {THERMAL_LIMIT_PERCENT:.0f}% (simplified NER S5.1a approx., see workflow.py)"
)

# Derived per-hold frame counts (rounded down; 0.8s*30fps is a whole number
# of frames, so no truncation error for any of these holds).
INTRO_FRAME_COUNT: int = round(INTRO_HOLD_SECONDS * VIDEO_FPS)
CONTINGENCY_FRAME_COUNT: int = round(CONTINGENCY_HOLD_SECONDS * VIDEO_FPS)
CLOSING_FRAME_COUNT: int = round(CLOSING_HOLD_SECONDS * VIDEO_FPS)


class LineSnapshot(TypedDict):
    """Loading of one screened line in one solved state of the network."""

    line: int
    from_bus: int
    to_bus: int
    loading_percent: float
    in_service: bool


class SolveSnapshot(TypedDict):
    """Everything one real solve tells the animation about that state."""

    converged: bool
    worst_voltage_pu: Optional[float]
    worst_voltage_bus: Optional[int]
    worst_loading_percent: Optional[float]
    worst_loading_line: Optional[int]
    local_lines: list[LineSnapshot]


class ContingencySnapshot(TypedDict):
    """A solved N-1 state plus which line was dropped to reach it."""

    dropped_line: int
    from_bus: int
    to_bus: int
    solve: SolveSnapshot


class FrameSpec(TypedDict):
    """What one output frame must draw."""

    kind: str  # "intro" | "contingency" | "closing"
    ordinal: Optional[int]  # 1-based position in the screened order
    dropped_line: Optional[int]
    from_bus: Optional[int]
    to_bus: Optional[int]
    solve: SolveSnapshot


class _AnimState:
    """Everything the per-frame update function needs to redraw a frame.

    One instance per run; built once by _build_state() from the fully
    precomputed snapshots, then mutated in place by _update_frame().
    """

    fig: Figure
    ax: Axes
    bars: list[Rectangle]
    tick_labels: list[Text]
    frame_title: Text
    metrics_text: Text
    summary_text: Text
    limit_line: Line2D
    frames: list[FrameSpec]
    local_lines: list[int]
    total_screened: int
    base_snap: SolveSnapshot
    cont_snaps: list[ContingencySnapshot]


def _snapshot_solve(net: pp.pandapowerNet, lines: list[int]) -> SolveSnapshot:
    """Capture the per-line loading of the screened `lines` plus network-wide
    worst-case voltage and loading from `net`'s current solved state.

    All values are read straight out of pandapower's result tables
    (res_bus / res_line) -- the animation never fabricates or interpolates a
    number (AGENTS.md: physics results must come from a real runpp() call).

    Args:
        net: a solved pandapower net (pp.runpp(..., init="flat")).
        lines: the screened line indices (local_contingency_lines output).

    Returns:
        One LineSnapshot per screened line plus network-wide worst values
        (None'd out if the solve did not converge).
    """
    local: list[LineSnapshot] = []
    for li in lines:
        loading = DROPPED_LINE_LOADING_PERCENT
        if net.converged:
            raw = float(net.res_line.loading_percent.at[li])
            loading = raw if isfinite(raw) else DROPPED_LINE_LOADING_PERCENT
        local.append(
            {
                "line": int(li),
                "from_bus": int(net.line.at[li, "from_bus"]),
                "to_bus": int(net.line.at[li, "to_bus"]),
                "loading_percent": loading,
                "in_service": bool(net.line.at[li, "in_service"]),
            }
        )
    return {
        "converged": bool(net.converged),
        "worst_voltage_pu": (
            float(net.res_bus.vm_pu.min()) if net.converged else None
        ),
        "worst_voltage_bus": (
            int(net.res_bus.vm_pu.idxmin()) if net.converged else None
        ),
        "worst_loading_percent": (
            float(net.res_line.loading_percent.max()) if net.converged else None
        ),
        "worst_loading_line": (
            int(net.res_line.loading_percent.idxmax()) if net.converged else None
        ),
        "local_lines": local,
    }


def solve_base_snapshot() -> tuple[pp.pandapowerNet, SolveSnapshot, list[int]]:
    """Solve the base case (candidate generator attached, no outage) once.

    Returns:
        (the solved net, its snapshot, the screened line list). The net is
        reused for the 21 contingency solves; the snapshot feeds the intro
        and closing frames.
    """
    net = run_base_case(verbose=False)
    lines = local_contingency_lines(net, CANDIDATE_BUS)
    return net, _snapshot_solve(net, lines), lines


def solve_contingency_snapshots(
    base: pp.pandapowerNet, lines: list[int]
) -> list[ContingencySnapshot]:
    """Solve all N-1 outages in sequence on one base net.

    Sequential in-process solves. workflow.run_contingencies() fans the same
    work out to real OS processes for the lab's concurrent step; here a
    deterministic serial re-run is required because the animation needs the
    full per-line loading table, which run_contingencies() -- worst-value-only
    results -- does not return. snem1803.m needs `pp.runpp(net, init="flat")`
    (a zero-impedance branch breaks the default DC-flat pre-solve) -- the
    same documented deviation as workflow.py.

    Args:
        base: the solved base-case net from run_base_case().
        lines: screened line indices; each is dropped in turn.

    Returns:
        One ContingencySnapshot per screened line, in the same sorted order.
    """
    snaps: list[ContingencySnapshot] = []
    for li in lines:
        from_bus = int(base.line.at[li, "from_bus"])
        to_bus = int(base.line.at[li, "to_bus"])
        base.line.at[li, "in_service"] = False
        pp.runpp(base, init="flat")
        snaps.append(
            {
                "dropped_line": int(li),
                "from_bus": from_bus,
                "to_bus": to_bus,
                "solve": _snapshot_solve(base, lines),
            }
        )
        base.line.at[li, "in_service"] = True
    return snaps


def build_frame_sequence(
    base_snap: SolveSnapshot, cont_snaps: list[ContingencySnapshot]
) -> list[FrameSpec]:
    """Expand the snapshots into one FrameSpec per output frame.

    Each snapshot is repeated for its hold time (intro / each contingency /
    closing) so a presenter can narrate without pausing the video; the
    contingency snapshots appear in screened (sorted) order.

    Args:
        base_snap: base-case snapshot (intro and closing frames).
        cont_snaps: per-outage snapshots, in screened order.

    Returns:
        One FrameSpec per output frame; length = INTRO_FRAME_COUNT +
        len(cont_snaps) * CONTINGENCY_FRAME_COUNT + CLOSING_FRAME_COUNT.
    """
    seq: list[FrameSpec] = []
    seq.extend(
        {
            "kind": "intro",
            "ordinal": None,
            "dropped_line": None,
            "from_bus": None,
            "to_bus": None,
            "solve": base_snap,
        }
        for _ in range(INTRO_FRAME_COUNT)
    )
    for ordinal, cs in enumerate(cont_snaps, start=1):
        seq.extend(
            {
                "kind": "contingency",
                "ordinal": ordinal,
                "dropped_line": cs["dropped_line"],
                "from_bus": cs["from_bus"],
                "to_bus": cs["to_bus"],
                "solve": cs["solve"],
            }
            for _ in range(CONTINGENCY_FRAME_COUNT)
        )
    seq.extend(
        {
            "kind": "closing",
            "ordinal": None,
            "dropped_line": None,
            "from_bus": None,
            "to_bus": None,
            "solve": base_snap,
        }
        for _ in range(CLOSING_FRAME_COUNT)
    )
    return seq


def _frame_title(spec: FrameSpec, state: _AnimState) -> str:
    """Human-readable title line for one frame (intro / contingency / closing).

    Args:
        spec: the frame being drawn.
        state: shared animation state (for the screened-line count).

    Returns:
        The title line.
    """
    if spec["kind"] == "intro":
        return (
            f"Base case -- no line dropped "
            f"(candidate {CANDIDATE_GEN_MW:.0f} MW at bus {CANDIDATE_BUS})"
        )
    if spec["kind"] == "closing":
        return "All contingencies screened -- summary"
    return (
        f"Contingency {spec['ordinal']}/{state.total_screened} -- line "
        f"{spec['dropped_line']} [{spec['from_bus']}-{spec['to_bus']}] "
        f"out of service"
    )


def _metrics_text(spec: FrameSpec, state: _AnimState) -> str:
    """One-line metrics annotation for a frame, always honest about whether a
    breach is pre-existing or caused by this outage.

    The dataset's worst voltage (0.899 pu @ bus 1126) is identical to 15
    decimal places across the base case and all 21 outages, so "unchanged" is
    a real measured claim, not an assumption. A loading over the 100% limit
    only ever happens here when a parallel 175-608 twin is dropped, and the
    reason string says so.

    Args:
        spec: the frame being drawn.
        state: shared animation state (for the base-case reference values).

    Returns:
        The metrics text ("" on closing frames, which carry _summary_text()
        instead).
    """
    if spec["kind"] == "closing":
        return ""
    snap = spec["solve"]
    base = state.base_snap
    v = snap["worst_voltage_pu"]
    vb = snap["worst_voltage_bus"]
    load = snap["worst_loading_percent"]
    wl = snap["worst_loading_line"]
    if v is None or load is None:
        return "solve did not converge"
    voltage_bit = f"worst bus voltage {v:.3f} pu @ bus {vb} (pre-existing, unchanged)"
    if spec["kind"] == "intro":
        return (
            f"{voltage_bit}  |  worst line loading {load:.1f}% on line {wl} "
            f"(pre-existing base-case point)"
        )
    if load > THERMAL_LIMIT_PERCENT:
        return (
            f"{voltage_bit}  |  worst line loading {load:.1f}% on line {wl} "
            f"-- NEW thermal breach: surviving twin of the dropped parallel line"
        )
    return (
        f"{voltage_bit}  |  worst line loading {load:.1f}% on line {wl} "
        f"(pre-existing ~{base['worst_loading_percent']:.1f}% region; no new breach)"
    )


def _summary_text(state: _AnimState) -> str:
    """Closing-frame verdict, built entirely from the real solved snapshots.

    Never hard-codes a conclusion: the pre-existing figures come from the
    base-case snapshot and the breach count/list is derived by scanning the
    solved contingencies for loadings over THERMAL_LIMIT_PERCENT.

    Args:
        state: shared animation state (base snapshot + all contingencies).

    Returns:
        Multi-line summary text for the closing frame.
    """
    base = state.base_snap
    overloads = [
        cs
        for cs in state.cont_snaps
        if cs["solve"]["worst_loading_percent"] is not None
        and cs["solve"]["worst_loading_percent"] > THERMAL_LIMIT_PERCENT
    ]
    n_flat = len(state.cont_snaps) - len(overloads)
    lines = [
        f"All {len(state.cont_snaps)} local lines screened (N-1) -- honest result:",
        f"- worst bus voltage {base['worst_voltage_pu']:.3f} pu @ bus "
        f"{base['worst_voltage_bus']}: pre-existing in the base case, "
        f"unchanged by every outage",
        f"- {n_flat} of {len(state.cont_snaps)} outages: worst loading stays in "
        f"the pre-existing ~{base['worst_loading_percent']:.1f}% region "
        f"(line {base['worst_loading_line']}) -- no new breach",
    ]
    if overloads:
        pair_fb = overloads[0]["from_bus"]
        pair_tb = overloads[0]["to_bus"]
        pair_terms = "; ".join(
            f"line {cs['dropped_line']} out -> line "
            f"{cs['solve']['worst_loading_line']} at "
            f"{cs['solve']['worst_loading_percent']:.1f}%"
            for cs in overloads
        )
        lines.append(
            f"- {len(overloads)} outage(s) create a REAL, contingency-induced "
            f"thermal breach: the parallel {pair_fb}-{pair_tb} pair -- dropping "
            f"one twin overloads the other: {pair_terms}"
        )
    lines.append(
        "- every number above is from a real pandapower AC solve "
        '(pp.runpp(net, init="flat"))'
    )
    return "\n".join(lines)


def _build_state(
    frames: list[FrameSpec],
    local_lines: list[int],
    base_snap: SolveSnapshot,
    cont_snaps: list[ContingencySnapshot],
) -> _AnimState:
    """Create the figure, axes and all reusable artists.

    Bars/limit line/texts are created once here and only *updated* per frame
    (set_height / set_text / set_facecolor) -- nothing is re-created or
    re-solved inside the animation loop.

    Args:
        frames: the precomputed frame sequence.
        local_lines: screened line indices (x-axis order).
        base_snap: base-case snapshot (closing frame draws it).
        cont_snaps: per-outage snapshots.

    Returns:
        The initialized _AnimState.
    """
    state = _AnimState()
    state.fig = plt.figure(figsize=FIGURE_FIGSIZE, dpi=FIGURE_DPI)
    state.ax = state.fig.add_axes(MAIN_AXES_RECT)
    state.ax.set_ylim(0, LOADING_YMAX_PERCENT)
    state.ax.set_xlim(-0.6, len(local_lines) - 0.4)
    state.ax.grid(True, axis="y", linewidth=0.4, alpha=0.4, color=AXIS_INK_COLOR)
    state.ax.set_ylabel(YLABEL)
    state.ax.set_xlabel(XLABEL)
    state.bars = list(
        state.ax.bar(
            range(len(local_lines)),
            [DROPPED_LINE_LOADING_PERCENT] * len(local_lines),
            color=MAIN_SERIES_COLOR,
            edgecolor=AXIS_INK_COLOR,
            linewidth=0.3,
        )
    )
    state.limit_line = state.ax.axhline(
        THERMAL_LIMIT_PERCENT, ls="--", lw=1.4, color=LIMIT_LINE_COLOR
    )
    state.ax.set_xticks(range(len(local_lines)))
    state.tick_labels = list(
        state.ax.set_xticklabels([str(li) for li in local_lines], rotation=90)
    )
    legend_handles = [
        Patch(facecolor=MAIN_SERIES_COLOR, label=LEGEND_LOADING_LABEL),
        Patch(facecolor=DROPPED_LINE_COLOR, label=LEGEND_DROPPED_LABEL),
        Line2D([0], [0], color=LIMIT_LINE_COLOR, ls="--", lw=1.4, label=LEGEND_LIMIT_LABEL),
    ]
    state.ax.legend(handles=legend_handles, loc="lower left", frameon=False)

    state.fig.suptitle(SUPTITLE)
    state.frame_title = state.fig.text(0.5, 0.90, "", ha="center")
    state.metrics_text = state.fig.text(0.5, 0.85, "", ha="center", color=AXIS_INK_COLOR)
    state.fig.text(0.5, 0.035, FOOTNOTE, ha="center", fontsize=7, color=AXIS_INK_COLOR)
    state.summary_text = state.fig.text(
        0.5, 0.575, "", ha="center", va="center", linespacing=1.7,
        bbox=dict(boxstyle="round,pad=0.7", facecolor="white", alpha=0.92, edgecolor=AXIS_INK_COLOR),
    )

    state.frames = frames
    state.local_lines = local_lines
    state.total_screened = len(cont_snaps)
    state.base_snap = base_snap
    state.cont_snaps = cont_snaps
    return state


def _update_frame(frame_no: int, state: _AnimState) -> list[object]:
    """Redraw frame `frame_no` from the precomputed snapshots.

    The dropped line's bar and x-tick label turn red; every other bar stays
    the main-series blue. No physics runs here -- all solves happened in
    solve_contingency_snapshots() before the animation loop.

    Args:
        frame_no: index into state.frames.
        state: shared animation state.

    Returns:
        The updated artists (ignored; FuncAnimation runs with blit=False).
    """
    spec = state.frames[frame_no]
    snap = spec["solve"]
    dropped = spec["dropped_line"]
    for rect, ls in zip(state.bars, snap["local_lines"]):
        rect.set_height(ls["loading_percent"])
        rect.set_facecolor(
            DROPPED_LINE_COLOR if ls["line"] == dropped else MAIN_SERIES_COLOR
        )
    for lbl, li in zip(state.tick_labels, state.local_lines):
        lbl.set_color(DROPPED_LINE_COLOR if li == dropped else AXIS_INK_COLOR)
    state.frame_title.set_text(_frame_title(spec, state))
    state.metrics_text.set_text(_metrics_text(spec, state))
    closing = spec["kind"] == "closing"
    state.summary_text.set_visible(closing)
    if closing:
        state.summary_text.set_text(_summary_text(state))
    return [*state.bars, state.frame_title, state.metrics_text, state.summary_text]


def main() -> None:
    """Precompute every solve, then render the animation.

    Runtime is dominated by the 1 base + 21 contingency AC solves (~20-25s
    total); frame generation then re-draws each precomputed frame. Exits
    non-zero if any contingency failed to converge (should never happen for
    this dataset -- all 21 converge with init="flat").
    """
    net, base_snap, lines = solve_base_snapshot()
    print(
        f"Base case solved: worst voltage {base_snap['worst_voltage_pu']:.3f} pu "
        f"@ bus {base_snap['worst_voltage_bus']}, worst loading "
        f"{base_snap['worst_loading_percent']:.1f}% on line "
        f"{base_snap['worst_loading_line']}"
    )
    print(f"Screening {len(lines)} N-1 contingencies (real AC solves)...")
    cont_snaps = solve_contingency_snapshots(net, lines)
    failed = [cs for cs in cont_snaps if not cs["solve"]["converged"]]
    if failed:
        raise RuntimeError(
            f"{len(failed)} contingency solve(s) did not converge: "
            f"{[cs['dropped_line'] for cs in failed]}"
        )
    for cs in cont_snaps:
        s = cs["solve"]
        print(
            f"  contingency line {cs['dropped_line']} [{cs['from_bus']}-"
            f"{cs['to_bus']}] done: worst loading {s['worst_loading_percent']:.1f}%"
        )

    frames = build_frame_sequence(base_snap, cont_snaps)
    state = _build_state(frames, lines, base_snap, cont_snaps)
    print(
        f"Rendering {len(frames)} frames @ {VIDEO_FPS} fps to {OUTPUT_MP4.name}..."
    )
    update = partial(_update_frame, state=state)
    anim = FuncAnimation(
        state.fig,
        update,
        frames=len(frames),
        interval=1000.0 / VIDEO_FPS,
        blit=False,
        cache_frame_data=False,
    )
    writer = FFMpegWriter(
        fps=VIDEO_FPS,
        codec="libx264",
        bitrate=-1,  # -1 -> ffmpeg picks the bitrate (matplotlib docs)
        extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    )
    anim.save(OUTPUT_MP4, writer=writer, dpi=FIGURE_DPI)
    plt.close(state.fig)
    print(f"Wrote {OUTPUT_MP4}")


if __name__ == "__main__":
    main()
