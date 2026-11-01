#!/usr/bin/env python3
"""Animate Lab 1's load-flow parameter-fit bisection search converging.

Headless: renders benchmarks/lab01-simple-loadflow-fit/animate_convergence.mp4 (a
PowerPoint-friendly H.264 + yuv420p MP4) from the REAL iteration log of a
fresh `gridfit.bisection_fit` run -- every point animated is a genuine
`pandapower.runpp()` AC power-flow solve on snemSA.m, nothing fabricated
(AGENTS.md: "never hand-roll a toy network", "never fabricate" a physics
result). Run:

    uv run benchmarks/lab01-simple-loadflow-fit/animate_convergence.py

The physics constants (TARGET_BUS, the 0.9422 pu synthetic SCADA target,
the [0.90, 1.10] search bound, the 0.002 pu tolerance) are imported from
run.py, the lab's single source of truth, so this script cannot drift from
the lab it illustrates.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Final, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandapower as pp
from matplotlib.animation import FFMpegWriter, FuncAnimation
from matplotlib.axes import Axes
from matplotlib.figure import Figure


from wattson.gridfit import (
    FitIteration,
    FitResult,
    bisection_fit,
    load_case,
    scale_loads,
)
from run import (
    DATA_FILE,
    FIELD_SCADA_VOLTAGE_PU,
    FIT_TOLERANCE_PU,
    SCALE_HI,
    SCALE_LO,
    TARGET_BUS,
)

LAB_DIR: Final[Path] = Path(__file__).resolve().parent

# Rendered artifact. The MP4 is gitignored (*.mp4); this script is the
# committed artifact that re-derives it from a real fit on a clean checkout.
OUTPUT_MP4: Final[Path] = LAB_DIR / "animate_convergence.mp4"

# Dataviz palette: the repo's validated hexes, same blue/red/gray roles as
# Labs 3/4/5 (reconcile.py RECONCILIATION_CHART_MODELLED/ACTUAL_COLOR,
# generate_topology.py TOPOLOGY_BUS_NODE/TAP_NODE/EDGE_COLOR). Blue = the
# measured/trial series, red = the target the fit is graded against, muted
# gray = ink/gridlines/labels.
ANIM_TRIAL_COLOR: Final[str] = "#2a78d6"
ANIM_TARGET_COLOR: Final[str] = "#e34948"
ANIM_INK_COLOR: Final[str] = "#898781"

# Raster geometry: 12.8 x 7.2 in at 100 dpi -> exactly 1280x720, a 16:9
# PowerPoint-friendly size, with no post-render rescale step.
ANIM_FIGSIZE: Final[tuple[float, float]] = (12.8, 7.2)
ANIM_FIG_DPI: Final[int] = 100

# MP4 encoding. H.264 + yuv420p is the universally PowerPoint-compatible
# pixel format; +faststart front-loads the moov atom so the file starts
# instantly when a presenter opens it. fps=30 is smooth and small enough
# for a slide deck. bitrate=-1 is the mandated value: matplotlib's
# FFMpegBase.output_args only adds `-b` for bitrate > 0, so this falls
# back to libx264's default (CRF) rate control.
ANIM_FPS: Final[int] = 30
ANIM_FFMPEG_CODEC: Final[str] = "libx264"
ANIM_FFMPEG_BITRATE: Final[int] = -1
ANIM_FFMPEG_EXTRA_ARGS: Final[tuple[str, ...]] = (
    "-pix_fmt",
    "yuv420p",
    "-movflags",
    "+faststart",
)

# Frame holds (at 30 fps): 2 s on the "start" frame and on each real
# bisection iteration so a presenter can narrate a step per frame, then
# 3 s on the final converged frame carrying the result summary.
ANIM_FRAMES_PRE_FIT: Final[int] = 60
ANIM_FRAMES_PER_ITER: Final[int] = 60
ANIM_FRAMES_FINAL: Final[int] = 90
ANIM_FRAME_INTERVAL_MS: Final[int] = 1000 // ANIM_FPS  # only used by non-FFmpeg writers

# Band alphas: the search-range / tolerance / bracket spans are large flat
# areas, rendered at low alpha with the palette hexes so the blue data
# markers stay legible on top.
ANIM_SEARCH_BAND_ALPHA: Final[float] = 0.08
ANIM_BRACKET_BAND_ALPHA: Final[float] = 0.18
ANIM_TOLERANCE_BAND_ALPHA: Final[float] = 0.20

# Line/marker/grid weights, tuned for the 1280x720 raster (checked
# visually, matching the weights Lab 5's verify_stream.py uses).
ANIM_TRIAL_LINE_WIDTH: Final[float] = 2.0
ANIM_TARGET_LINE_WIDTH: Final[float] = 1.5
ANIM_TARGET_LINE_ALPHA: Final[float] = 0.8
ANIM_TRIAL_MARKER_SIZE: Final[float] = 7.0
ANIM_MARKER_EDGE_WIDTH: Final[float] = 1.5
ANIM_GRID_LINE_WIDTH: Final[float] = 0.5
ANIM_GRID_ALPHA: Final[float] = 0.4

# Axis padding so bands, points and annotations keep breathing room.
ANIM_SCALE_YLIM_PAD: Final[float] = 0.02
ANIM_VOLTAGE_YLIM_PAD: Final[float] = 0.003
ANIM_X_LIM_LO: Final[float] = -0.5
ANIM_X_LIM_HI: Final[float] = 0.5
ANIM_VOLTAGE_TEXT_OFFSET: Final[float] = 0.0008
ANIM_BASE_LABEL_X: Final[float] = 0.18

# Font sizes in points on the 1280x720 raster.
ANIM_TITLE_FONT_SIZE: Final[int] = 16
ANIM_PANEL_TITLE_FONT_SIZE: Final[int] = 11
ANIM_LABEL_FONT_SIZE: Final[int] = 11
ANIM_ANNOTATION_FONT_SIZE: Final[int] = 11
ANIM_SUMMARY_FONT_SIZE: Final[int] = 13
ANIM_FOOTNOTE_FONT_SIZE: Final[int] = 9
ANIM_LEGEND_FONT_SIZE: Final[int] = 10

# In-figure text placement: transAxes fractions for panel captions and the
# summary box, figure fractions for the footnote and the tight_layout rect
# that leaves room for suptitle + footnote.
ANIM_TRANS_AXES_TEXT_X: Final[float] = 0.02
ANIM_TRANS_AXES_TOP_Y: Final[float] = 0.95
ANIM_TRANS_AXES_DETAIL_Y: Final[float] = 0.87
ANIM_SUMMARY_X: Final[float] = 0.5
ANIM_SUMMARY_Y: Final[float] = 0.5
ANIM_FOOTNOTE_X: Final[float] = 0.5
ANIM_FOOTNOTE_Y: Final[float] = 0.015
ANIM_TIGHT_LAYOUT_RECT: Final[tuple[float, float, float, float]] = (
    0.0,
    0.05,
    1.0,
    0.94,
)

ANIM_TITLE: Final[str] = (
    "Lab 1 — bisection fit: load scale x to hit "
    f"{FIELD_SCADA_VOLTAGE_PU} pu at bus {TARGET_BUS}"
)
ANIM_FOOTNOTE: Final[str] = (
    "snemSA.m (CSIRO Synthetic-NEM-2000-Bus) · every point is a real pandapower "
    "runpp() AC power-flow solve · deterministic bisection stands in for the LLM "
    f"'propose next trial' step (docs/VISION.md §9) · tolerance ±{FIT_TOLERANCE_PU} pu"
)


def _check_data_file() -> None:
    """Exit(1) with the fetch-script pointer if snemSA.m is missing."""
    if not DATA_FILE.exists():
        print(
            f"[FAIL] {DATA_FILE} not found -- run "
            f"'uv run scripts/fetch_csiro_nem_data.py' first",
            file=sys.stderr,
        )
        sys.exit(1)


def _replay_brackets(
    evaluate: Callable[[float], float],
    target: float,
    lo: float,
    hi: float,
    iterations: Sequence[FitIteration],
) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """Reconstruct each iteration's search bracket from the real trial log.

    `bisection_fit` records only each trial/observed/residual, not the
    evolving [a, b] bracket. This replays the exact a/b update rule from
    gridfit.bisection_fit over the real residuals (plus one extra real
    `evaluate(lo)` solve for the initial f_lo sign, which the log does not
    contain) so the animation's shrinking bracket is faithful to the actual
    search, not reconstructed cosmetically.

    Args:
        evaluate: the lab's physics ground truth (real runpp solve per call).
        target: the voltage target the fit converges toward.
        lo, hi: the initial search bounds.
        iterations: the real FitIteration log.

    Returns:
        ``(brackets, final_bracket)`` where ``brackets[k]`` is the bracket
        in effect when iteration k+1's trial was chosen, and
        ``final_bracket`` is the bracket after the last recorded trial.
    """
    f_lo = evaluate(lo) - target
    a, b, fa = lo, hi, f_lo
    brackets: list[tuple[float, float]] = []
    for it in iterations:
        brackets.append((a, b))
        if (it.residual > 0) == (fa > 0):
            a, fa = it.trial, it.residual
        else:
            b = it.trial
    return brackets, (a, b)


def run_fit() -> tuple[
    FitResult, float, list[tuple[float, float]], tuple[float, float]
]:
    """Re-run Lab 1's bisection fit and print the real iteration log.

    Mirrors run.py's fit_step: load snemSA.m via powerio, solve the base
    case, then bisect the load-scale parameter so bus TARGET_BUS's voltage
    hits FIELD_SCADA_VOLTAGE_PU. Every trial is a real `pp.runpp()` solve
    on a `scale_loads` deep copy.

    Returns:
        ``(result, base_voltage_pu, brackets, final_bracket)`` -- the
        FitResult with the full iteration log, the real base-case voltage
        at TARGET_BUS, and the per-iteration search brackets from
        _replay_brackets.
    """
    _check_data_file()
    net, _ = load_case(DATA_FILE)
    pp.runpp(net)
    base_voltage = float(net.res_bus.at[TARGET_BUS, "vm_pu"])

    def evaluate(scale: float) -> float:
        """Physics ground truth for one trial `scale`: a real pandapower
        AC power-flow solve -- see the module docstring and run.py."""
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
    brackets, final_bracket = _replay_brackets(
        evaluate, FIELD_SCADA_VOLTAGE_PU, SCALE_LO, SCALE_HI, result.iterations
    )

    print(
        f"Loaded snemSA.m via powerio; base-case bus {TARGET_BUS} = "
        f"{base_voltage:.4f} pu"
    )
    for it in result.iterations:
        print(
            f"iter {it.iteration}: trial={it.trial:.4f}x -> "
            f"{it.observed:.4f} pu (residual {it.residual:+.4f})"
        )
    status = "PASS" if result.converged else "FAIL (did not converge)"
    print(
        f"converged: trial={result.trial:.4f}x, bus {TARGET_BUS} = "
        f"{result.observed:.4f} pu, residual {result.residual:+.4f} "
        f"({status}, tol {FIT_TOLERANCE_PU})"
    )
    return result, base_voltage, brackets, final_bracket


def build_frame_scenes(iter_count: int) -> list[int]:
    """Map each animation frame to a scene id.

    Scene ids: -1 = pre-fit "start" frame (initial bracket + base case,
    ANIM_FRAMES_PRE_FIT hold), 0..iter_count-1 = one
    ANIM_FRAMES_PER_ITER-hold frame per real bisection iteration, and
    iter_count = the final ANIM_FRAMES_FINAL-hold frame showing the full
    converged state.

    Args:
        iter_count: number of iterations in the real fit log.

    Returns:
        A list whose i-th entry is the scene id for animation frame i.
    """
    scenes: list[int] = [-1] * ANIM_FRAMES_PRE_FIT
    for i in range(iter_count):
        scenes += [i] * ANIM_FRAMES_PER_ITER
    scenes += [iter_count] * ANIM_FRAMES_FINAL
    return scenes


def _visible_iterations(
    iterations: Sequence[FitIteration], scene: int
) -> list[FitIteration]:
    """Iterations revealed by the current scene, in log order.

    Args:
        iterations: the real fit log.
        scene: scene id; see build_frame_scenes.

    Returns:
        [] for the pre-fit scene, iterations[:scene+1] for an iteration
        scene, or the full log for the final scene.
    """
    if scene < 0:
        return []
    if scene >= len(iterations):
        return list(iterations)
    return list(iterations[: scene + 1])


def _scene_bracket(
    brackets: Sequence[tuple[float, float]],
    final_bracket: tuple[float, float],
    scene: int,
    n: int,
) -> tuple[float, float]:
    """Bracket to display for the given scene.

    Args:
        brackets: brackets[k] = bracket when iteration k+1 was tried.
        final_bracket: bracket after the last recorded trial.
        scene: scene id; see build_frame_scenes.
        n: number of iterations in the fit log.

    Returns:
        The initial [lo, hi] bracket for the pre-fit scene, the bracket in
        effect at that scene's iteration, or the final bracket for the
        last scene.
    """
    if scene < 0:
        return brackets[0]
    if scene >= n:
        return final_bracket
    return brackets[scene]


def _scene_caption(
    scene: int, n: int, bracket: tuple[float, float], converged: bool
) -> tuple[str, str]:
    """Headline + detail caption strings for a scene's top panel.

    Args:
        scene: scene id; see build_frame_scenes.
        n: number of iterations in the fit log.
        bracket: the bracket to caption.
        converged: whether the real fit converged.

    Returns:
        ``(headline, detail)``, e.g. ("iter 2", "bracket [0.900, 1.000]x
        — width 0.100").
    """
    lo, hi = bracket
    if scene < 0:
        return (
            "start",
            f"search range [{SCALE_LO:.2f}, {SCALE_HI:.2f}]x — "
            f"width {SCALE_HI - SCALE_LO:.2f}",
        )
    if scene >= n:
        status = "converged" if converged else "did not converge"
        return (
            status,
            f"final bracket [{lo:.3f}, {hi:.3f}]x — width {hi - lo:.3f}",
        )
    return (
        f"iter {scene + 1}",
        f"bracket [{lo:.3f}, {hi:.3f}]x — width {hi - lo:.3f}",
    )


def _draw_scene(
    fig: Figure,
    axes: Sequence[Axes],
    scene: int,
    iterations: Sequence[FitIteration],
    brackets: Sequence[tuple[float, float]],
    final_bracket: tuple[float, float],
    base_voltage_pu: float,
    result: FitResult,
) -> None:
    """Redraw both panels for one animation scene.

    Top panel: the load-scale search -- full search range as a pale band,
    the current bisection bracket as a tighter band, blue trial points, a
    red dashed line at the final fitted scale, and a caption naming the
    iteration and current bracket width. Bottom panel: the observed
    voltage at TARGET_BUS per iteration converging to the red dashed
    FIELD_SCADA_VOLTAGE_PU target with the +/- FIT_TOLERANCE_PU band
    shaded.

    Args:
        fig: the figure (suptitle/footnote already placed by
            make_animation; not cleared here).
        axes: ``(top_ax, bottom_ax)``.
        scene: scene id; see build_frame_scenes.
        iterations: the real bisection trial log.
        brackets: brackets[k] = bracket when iteration k+1 was tried.
        final_bracket: bracket after the last recorded trial.
        base_voltage_pu: real base-case voltage at TARGET_BUS.
        result: the FitResult the animation is built from.
    """
    top_ax, bottom_ax = axes[0], axes[1]
    top_ax.clear()
    bottom_ax.clear()

    n = len(iterations)
    visible = _visible_iterations(iterations, scene)
    bracket_lo, bracket_hi = _scene_bracket(brackets, final_bracket, scene, n)

    # ---------------- top panel: load-scale search ----------------
    top_ax.axhspan(
        SCALE_LO,
        SCALE_HI,
        color=ANIM_TRIAL_COLOR,
        alpha=ANIM_SEARCH_BAND_ALPHA,
        zorder=0,
    )
    top_ax.axhspan(
        bracket_lo,
        bracket_hi,
        color=ANIM_TRIAL_COLOR,
        alpha=ANIM_BRACKET_BAND_ALPHA,
        zorder=1,
    )
    top_ax.axhline(
        result.trial,
        color=ANIM_TARGET_COLOR,
        linestyle="--",
        linewidth=ANIM_TARGET_LINE_WIDTH,
        alpha=ANIM_TARGET_LINE_ALPHA,
        zorder=2,
    )
    trial_line = None
    if visible:
        (trial_line,) = top_ax.plot(
            [it.iteration for it in visible],
            [it.trial for it in visible],
            marker="o",
            markersize=ANIM_TRIAL_MARKER_SIZE,
            color=ANIM_TRIAL_COLOR,
            linewidth=ANIM_TRIAL_LINE_WIDTH,
            zorder=4,
            label="trial scale (real runpp solve)",
        )
    top_ax.plot(
        [0],
        [1.0],
        marker="o",
        markerfacecolor="none",
        markeredgecolor=ANIM_TRIAL_COLOR,
        markersize=ANIM_TRIAL_MARKER_SIZE,
        markeredgewidth=ANIM_MARKER_EDGE_WIDTH,
        zorder=4,
        label="base case (scale 1.00x)",
    )

    top_ax.set_xlim(ANIM_X_LIM_LO, n + ANIM_X_LIM_HI)
    top_ax.set_ylim(
        SCALE_LO - ANIM_SCALE_YLIM_PAD, SCALE_HI + ANIM_SCALE_YLIM_PAD
    )
    top_ax.set_ylabel("load scale x", fontsize=ANIM_LABEL_FONT_SIZE, color=ANIM_INK_COLOR)
    top_ax.set_title(
        "Top: search bracket + trial load scale",
        fontsize=ANIM_PANEL_TITLE_FONT_SIZE,
        color=ANIM_INK_COLOR,
    )
    top_ax.grid(
        True,
        axis="y",
        linewidth=ANIM_GRID_LINE_WIDTH,
        alpha=ANIM_GRID_ALPHA,
        color=ANIM_INK_COLOR,
    )
    caption_head, caption_detail = _scene_caption(
        scene, n, (bracket_lo, bracket_hi), result.converged
    )
    top_ax.text(
        ANIM_TRANS_AXES_TEXT_X,
        ANIM_TRANS_AXES_TOP_Y,
        caption_head,
        transform=top_ax.transAxes,
        fontsize=ANIM_ANNOTATION_FONT_SIZE,
        color=ANIM_TRIAL_COLOR,
        fontweight="bold",
        va="top",
    )
    top_ax.text(
        ANIM_TRANS_AXES_TEXT_X,
        ANIM_TRANS_AXES_DETAIL_Y,
        caption_detail,
        transform=top_ax.transAxes,
        fontsize=ANIM_ANNOTATION_FONT_SIZE,
        color=ANIM_INK_COLOR,
        va="top",
    )
    if trial_line is not None:
        top_ax.legend(
            handles=[trial_line],
            loc="upper right",
            fontsize=ANIM_LEGEND_FONT_SIZE,
        )

    if scene >= n:
        status = "PASS" if result.converged else "FAIL (no convergence)"
        summary = (
            f"fitted scale = {result.trial:.4f}x -> {result.observed:.4f} pu\n"
            f"residual = {result.residual:+.4f} pu  ({status}, "
            f"tol {FIT_TOLERANCE_PU:.3f})"
        )
        top_ax.text(
            ANIM_SUMMARY_X,
            ANIM_SUMMARY_Y,
            summary,
            transform=top_ax.transAxes,
            ha="center",
            va="center",
            fontsize=ANIM_SUMMARY_FONT_SIZE,
            color=ANIM_TRIAL_COLOR,
            fontweight="bold",
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor="white",
                edgecolor=ANIM_TRIAL_COLOR,
                alpha=0.85,
            ),
        )

    # ---------------- bottom panel: observed voltage ----------------
    bottom_ax.axhspan(
        FIELD_SCADA_VOLTAGE_PU - FIT_TOLERANCE_PU,
        FIELD_SCADA_VOLTAGE_PU + FIT_TOLERANCE_PU,
        color=ANIM_TARGET_COLOR,
        alpha=ANIM_TOLERANCE_BAND_ALPHA,
        zorder=0,
    )
    target_line = bottom_ax.axhline(
        FIELD_SCADA_VOLTAGE_PU,
        color=ANIM_TARGET_COLOR,
        linestyle="--",
        linewidth=ANIM_TARGET_LINE_WIDTH,
        alpha=ANIM_TARGET_LINE_ALPHA,
        zorder=2,
        label="target",
    )
    volt_xs = [0] + [it.iteration for it in visible]
    volt_ys = [base_voltage_pu] + [it.observed for it in visible]
    (volt_line,) = bottom_ax.plot(
        volt_xs,
        volt_ys,
        marker="o",
        markersize=ANIM_TRIAL_MARKER_SIZE,
        color=ANIM_TRIAL_COLOR,
        linewidth=ANIM_TRIAL_LINE_WIDTH,
        zorder=3,
        label="observed voltage (real runpp solve)",
    )
    bottom_ax.text(
        ANIM_BASE_LABEL_X,
        base_voltage_pu,
        "base case",
        fontsize=ANIM_ANNOTATION_FONT_SIZE,
        color=ANIM_INK_COLOR,
        va="center",
    )

    if visible:
        last = visible[-1]
        bottom_ax.text(
            last.iteration,
            last.observed + ANIM_VOLTAGE_TEXT_OFFSET,
            f"iter {last.iteration}: {last.observed:.4f} pu  "
            f"(residual {last.residual:+.4f})",
            fontsize=ANIM_ANNOTATION_FONT_SIZE,
            color=ANIM_INK_COLOR,
            va="bottom",
            ha="center",
        )

    observed_all = [it.observed for it in iterations]
    voltage_lo = min([base_voltage_pu] + observed_all) - ANIM_VOLTAGE_YLIM_PAD
    voltage_hi = (
        max([FIELD_SCADA_VOLTAGE_PU + FIT_TOLERANCE_PU] + observed_all)
        + ANIM_VOLTAGE_YLIM_PAD
    )
    bottom_ax.set_ylim(voltage_lo, voltage_hi)
    bottom_ax.set_xlabel(
        "bisection iteration", fontsize=ANIM_LABEL_FONT_SIZE, color=ANIM_INK_COLOR
    )
    bottom_ax.set_ylabel(
        f"voltage at bus {TARGET_BUS} (pu)",
        fontsize=ANIM_LABEL_FONT_SIZE,
        color=ANIM_INK_COLOR,
    )
    bottom_ax.set_title(
        "Bottom: observed voltage vs target",
        fontsize=ANIM_PANEL_TITLE_FONT_SIZE,
        color=ANIM_INK_COLOR,
    )
    bottom_ax.set_xticks(list(range(0, n + 1)))
    bottom_ax.grid(
        True,
        axis="y",
        linewidth=ANIM_GRID_LINE_WIDTH,
        alpha=ANIM_GRID_ALPHA,
        color=ANIM_INK_COLOR,
    )
    band_patch = mpatches.Patch(
        facecolor=ANIM_TARGET_COLOR,
        alpha=ANIM_TOLERANCE_BAND_ALPHA,
        label=f"±{FIT_TOLERANCE_PU} pu tolerance",
    )
    bottom_ax.legend(
        handles=[volt_line, target_line, band_patch],
        loc="lower right",
        fontsize=ANIM_LEGEND_FONT_SIZE,
    )


def make_animation(
    result: FitResult,
    base_voltage_pu: float,
    brackets: Sequence[tuple[float, float]],
    final_bracket: tuple[float, float],
    output: Path,
) -> None:
    """Build and save the FuncAnimation MP4 from the real fit log.

    One figure-level title + footnote, then one scene per animation state
    (pre-fit, each real iteration, final), held per the ANIM_FRAMES_*
    constants, written with FFMpegWriter (libx264 + yuv420p + faststart).

    Args:
        result: the FitResult whose iterations are animated.
        base_voltage_pu: real base-case voltage at TARGET_BUS.
        brackets: per-iteration search brackets from _replay_brackets.
        final_bracket: bracket after the last trial.
        output: path to write the MP4 to.
    """
    fig, (top_ax, bottom_ax) = plt.subplots(
        2, 1, sharex=True, figsize=ANIM_FIGSIZE, dpi=ANIM_FIG_DPI
    )
    fig.suptitle(ANIM_TITLE, fontsize=ANIM_TITLE_FONT_SIZE, color=ANIM_INK_COLOR)
    fig.text(
        ANIM_FOOTNOTE_X,
        ANIM_FOOTNOTE_Y,
        ANIM_FOOTNOTE,
        ha="center",
        va="bottom",
        fontsize=ANIM_FOOTNOTE_FONT_SIZE,
        color=ANIM_INK_COLOR,
    )

    frame_scenes = build_frame_scenes(len(result.iterations))

    def update(frame: int) -> tuple[Axes, Axes]:
        """Redraw both panels for animation frame `frame`'s scene."""
        scene = frame_scenes[frame]
        _draw_scene(
            fig,
            (top_ax, bottom_ax),
            scene,
            result.iterations,
            brackets,
            final_bracket,
            base_voltage_pu,
            result,
        )
        return top_ax, bottom_ax

    _draw_scene(
        fig,
        (top_ax, bottom_ax),
        frame_scenes[0],
        result.iterations,
        brackets,
        final_bracket,
        base_voltage_pu,
        result,
    )
    fig.tight_layout(rect=ANIM_TIGHT_LAYOUT_RECT)

    anim = FuncAnimation(
        fig,
        update,
        frames=len(frame_scenes),
        interval=ANIM_FRAME_INTERVAL_MS,
        repeat=False,
        blit=False,
    )
    writer = FFMpegWriter(
        fps=ANIM_FPS,
        codec=ANIM_FFMPEG_CODEC,
        bitrate=ANIM_FFMPEG_BITRATE,
        extra_args=ANIM_FFMPEG_EXTRA_ARGS,
    )
    print(
        f"Rendering {len(frame_scenes)} frames "
        f"({len(frame_scenes) / ANIM_FPS:.1f}s at {ANIM_FPS} fps) -> {output} ..."
    )
    anim.save(output, writer=writer)
    plt.close(fig)
    print(f"Wrote {output}")


def main() -> None:
    """Re-run the fit, then render the MP4 from the real iteration log."""
    result, base_voltage_pu, brackets, final_bracket = run_fit()
    if not result.converged:
        print(
            "[WARN] fit did not converge; animating the actual "
            f"{len(result.iterations)}-iteration log",
            file=sys.stderr,
        )
    make_animation(result, base_voltage_pu, brackets, final_bracket, OUTPUT_MP4)


if __name__ == "__main__":
    main()
