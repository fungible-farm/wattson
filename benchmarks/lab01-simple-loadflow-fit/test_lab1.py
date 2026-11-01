"""pytest wrapper around Lab 1's --step check, for scripts/run_labs_1_3.sh."""
import subprocess
import sys
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent


def test_lab1_fit_matches_fixture():
    result = subprocess.run(
        [sys.executable, str(LAB_DIR / "run.py"), "--step", "check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MATCH" in result.stdout


def test_lab1_network_chart_exists():
    """docs/backlog/0001 item 2: pandapower.plotting was unused across
    every lab. run.py's --step check (exercised above) already asserts
    NETWORK_CHART_FILE exists as part of its self-check gate; this test
    covers the same committed artifact directly, mirroring Lab 5's
    test_lab5_spectrogram_renders / test_lab5_topology_matches_fixture
    style -- a dedicated assertion on the chart file, not just an implicit
    side effect of the fixture-match test above."""
    chart = LAB_DIR / "sample_network_chart.png"
    assert chart.exists(), (
        f"{chart} missing -- run 'uv run benchmarks/lab01-simple-loadflow-fit/run.py "
        f"--step fit' to (re)generate it"
    )
    assert chart.stat().st_size > 0
