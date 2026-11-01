"""pytest wrapper around Lab 2's workflow steps, for scripts/run_labs_1_3.sh."""
import subprocess
import sys
from pathlib import Path

import pytest

LAB_DIR = Path(__file__).resolve().parent


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LAB_DIR / "workflow.py"), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def test_lab2_contingency_table_matches_fixture():
    result = _run("--step", "check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MATCH" in result.stdout


def test_lab2_memo_blocks_without_approval():
    result = _run("--step", "memo")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED" in result.stdout
    assert "MEMO FINALIZED" not in result.stdout


def test_lab2_memo_finalizes_with_approval():
    result = _run("--step", "memo", "--approve", "APPROVE")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MEMO FINALIZED" in result.stdout


import platform


@pytest.mark.skipif(
    platform.system() == "Linux" and platform.machine() == "aarch64",
    reason="pypowsybl has no aarch64 wheel; skipped on this platform",
)
def test_pypowsybl_n1_cross_check_matches_fixture():
    """pypowsybl_cross_check.py is a real second-engine cross-validation of workflow.py's own
    N-1 screen (see its module docstring) -- this only checks its own --step check gate, the
    same discipline every other self-checking step in this repo follows."""
    result = subprocess.run(
        [sys.executable, str(LAB_DIR / "pypowsybl_cross_check.py"), "--step", "check"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MATCH" in result.stdout


def test_lab2_render_network_diagram_writes_svg():
    """render_network_diagram.py is a rendering of the same 14-bus/21-line neighbourhood and
    loading numbers workflow.py's own N-1 screen already produces (not a new source of truth) --
    this checks it actually runs and regenerates a real, non-empty SVG file."""
    diagram_path = LAB_DIR / "sample_network_diagram.svg"
    diagram_path.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, str(LAB_DIR / "render_network_diagram.py")],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert diagram_path.exists()
    assert diagram_path.stat().st_size > 0
    assert diagram_path.read_text(encoding="utf-8").startswith("<?xml")


def test_lab2_memo_reports_contingency_induced_breaches():
    """Regression gate for draft_memo()'s classification bug: a row whose
    reason carries BOTH a pre-existing voltage clause AND a
    contingency-induced thermal clause (lines 151/152, the [175-608] parallel
    pair) must be reported as a real breach, not swallowed by the
    pre-existing label. The memo must say the outages break limits and name
    line 151 -- not the old, false "No contingency introduces a *new* limit
    breach."."""
    result = _run("--step", "memo", "--approve", "APPROVE")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BREACH limits as a direct result of the outage" in result.stdout
    assert "line 151" in result.stdout
    assert "contingency-induced" in result.stdout
    assert "No contingency introduces a *new* limit breach" not in result.stdout
