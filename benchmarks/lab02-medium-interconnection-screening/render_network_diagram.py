#!/usr/bin/env python3
"""Render the real local N-1 screening neighbourhood (`workflow.py`'s `CANDIDATE_BUS` and its
21 real contingency lines) as a deterministic SVG network diagram.

Scope, deliberately: the full `snem1803.m` case (1803 buses, 2795 branches, no geographic
coordinates) would render as an unreadable hairball, so this draws only the real, legible,
directly relevant subgraph -- the same 14-bus/21-line neighbourhood `workflow.py`'s own N-1 screen
already covers, matching this lab's existing "the chart is a rendering of an already-verified
result, not a new source of truth" convention (docs/backlog/0003, same as `sample_contingency_chart.png`).

Real topology, not simplified: several bus pairs in this neighbourhood carry genuine parallel
lines (175-249, 175-275, 175-328, 185-254, and the real N-1 finding's own 175-608 pair) -- each is
drawn as two separate curved edges, not collapsed into one, so the diagram matches the real case
data exactly.

Usage:
    uv run benchmarks/lab02-medium-interconnection-screening/render_network_diagram.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow import (  # noqa: E402  (needs sys.path insert above)
    CANDIDATE_BUS,
    CANDIDATE_GEN_MW,
    local_contingency_lines,
    run_base_case,
)

LAB_DIR: Final[Path] = Path(__file__).resolve().parent
DIAGRAM_FILE: Final[Path] = LAB_DIR / "sample_network_diagram.svg"

# The real finding this diagram exists to make visible at a glance (see README.md's "Design
# notes" and docs/POWERFLOW_ENGINE_SHOOTOUT.md): dropping either of this parallel pair overloads
# its twin to 113.0%/111.4%, cross-validated exactly by an independent pypowsybl solve.
BREACH_LINES: Final[frozenset[int]] = frozenset({151, 152})

CANDIDATE_COLOR: Final[str] = "#f59e0b"
BREACH_COLOR: Final[str] = "#dc2626"
NODE_COLOR: Final[str] = "#1e3a5f"
NODE_TEXT_COLOR: Final[str] = "white"
FIG_SIZE: Final[tuple[float, float]] = (10.0, 8.0)
NODE_SIZE: Final[float] = 900.0
CANDIDATE_NODE_SIZE: Final[float] = 1400.0
# Curvature step (matplotlib "arc3,rad=" units) between successive parallel edges on the same
# bus pair, so real duplicate branches (e.g. 175-608's two real lines) render as two visibly
# distinct arcs instead of one line hiding the other.
PARALLEL_EDGE_RAD_STEP: Final[float] = 0.15


def _edge_groups(lines: list[int], net) -> dict[int, tuple[int, int, int]]:
    """Assigns each line index a `(from_bus, to_bus, parallel_index)` triple, where
    `parallel_index` is 0 for the first line found between a given bus pair, 1 for the second
    real parallel line on that same pair, etc. -- so real duplicate branches get distinct
    curvature instead of overlapping."""
    seen: dict[frozenset[int], int] = {}
    out: dict[int, tuple[int, int, int]] = {}
    for li in lines:
        row = net.line.loc[li]
        fb, tb = int(row.from_bus), int(row.to_bus)
        key = frozenset({fb, tb})
        idx = seen.get(key, 0)
        seen[key] = idx + 1
        out[li] = (fb, tb, idx)
    return out


def render() -> None:
    net = run_base_case(verbose=False)
    lines = local_contingency_lines(net, CANDIDATE_BUS)
    edge_info = _edge_groups(lines, net)

    graph = nx.Graph()
    for li in lines:
        fb, tb, _ = edge_info[li]
        graph.add_edge(fb, tb)
    graph.add_node(CANDIDATE_BUS)
    pos = nx.kamada_kawai_layout(graph)

    loadings = {li: float(net.res_line.loading_percent.loc[li]) for li in lines}
    lo, hi = min(loadings.values()), max(loadings.values())
    # Blues, not a red-family map: several non-breach lines in this neighbourhood (e.g. 181/182,
    # a real parallel pair between buses 185-254) load up near the top of this range too, and a
    # red-family colormap would render them visually indistinguishable from BREACH_COLOR below --
    # red is reserved exclusively for the two genuinely contingency-induced lines.
    cmap = plt.get_cmap("Blues")

    fig, ax = plt.subplots(figsize=FIG_SIZE)

    for li in lines:
        fb, tb, idx = edge_info[li]
        x1, y1 = pos[fb]
        x2, y2 = pos[tb]
        is_breach = li in BREACH_LINES
        rad = PARALLEL_EDGE_RAD_STEP * (idx if idx % 2 == 0 else -idx)
        norm = (loadings[li] - lo) / (hi - lo) if hi > lo else 0.5
        color = BREACH_COLOR if is_breach else cmap(0.25 + 0.65 * norm)
        width = 3.5 if is_breach else 1.2 + 1.8 * norm
        arrow = FancyArrowPatch(
            (x1, y1),
            (x2, y2),
            connectionstyle=f"arc3,rad={rad}",
            color=color,
            linewidth=width,
            zorder=1,
            arrowstyle="-",
        )
        ax.add_patch(arrow)

    node_colors = [CANDIDATE_COLOR if n == CANDIDATE_BUS else NODE_COLOR for n in graph.nodes]
    node_sizes = [
        CANDIDATE_NODE_SIZE if n == CANDIDATE_BUS else NODE_SIZE for n in graph.nodes
    ]
    nx.draw_networkx_nodes(
        graph, pos, ax=ax, node_color=node_colors, node_size=node_sizes, edgecolors="black"
    )
    nx.draw_networkx_labels(
        graph, pos, ax=ax, font_size=9, font_color=NODE_TEXT_COLOR, font_weight="bold"
    )

    legend_elements = [
        Line2D(
            [0], [0], marker="o", color="none", markerfacecolor=CANDIDATE_COLOR,
            markeredgecolor="black", markersize=14,
            label=f"bus {CANDIDATE_BUS}: candidate {CANDIDATE_GEN_MW:.0f} MW connection",
        ),
        Line2D([0], [0], color=BREACH_COLOR, linewidth=3.5, label="lines 151/152: real N-1 breach (parallel pair, 113.0%/111.4%)"),
        Line2D([0], [0], color=cmap(0.7), linewidth=2, label="other lines (color/width = base-case loading %)"),
    ]
    ax.legend(handles=legend_elements, loc="lower center", bbox_to_anchor=(0.5, -0.12), ncol=1, frameon=True, fontsize=8)

    ax.set_title(
        f"Lab 2 N-1 screening neighbourhood -- snem1803.m, bus {CANDIDATE_BUS} candidate "
        f"connection\n{len(graph.nodes)} buses, {len(lines)} lines "
        f"(real base-case loading {lo:.1f}%-{hi:.1f}%)",
        fontsize=11,
    )
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(DIAGRAM_FILE, format="svg")
    plt.close(fig)
    print(f"wrote {DIAGRAM_FILE} ({len(graph.nodes)} buses, {len(lines)} lines)")


if __name__ == "__main__":
    render()
