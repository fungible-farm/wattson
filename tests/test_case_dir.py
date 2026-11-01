"""WATTSON_CASE_DIR moves the CSIRO case files for every consumer: the tool server's loader and the labs.

Regression: the container fetches the files into a volume, and lab 1 once still looked in <repo>/data and failed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wattson import fetch
from wattson.inference import powermcp as pm

REPO: Path = Path(__file__).resolve().parents[1]
SOURCE: Path = REPO / "data" / "snemSA.m"
needs_case = pytest.mark.skipif(not SOURCE.exists(), reason="needs data/ (scripts/fetch_csiro_nem_data.py)")


def test_case_dir_defaults_to_the_repository_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(fetch.CASE_DIR_ENV, raising=False)
    assert fetch.case_dir() == REPO / "data"
    assert pm.case_path("snem-sa-2000") == REPO / "data" / "snemSA.m"


def test_case_dir_override_reaches_the_loader_and_the_fetch_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(fetch.CASE_DIR_ENV, str(tmp_path))
    assert pm.case_path("snem-1803") == tmp_path / "snem1803.m"
    assert set(fetch.status().values()) == {"missing"}


@needs_case
def test_lab1_passes_with_the_case_file_somewhere_else(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib", reason="lab 1 draws charts; install the labs extra (uv sync --extra labs)")
    shutil.copy(SOURCE, tmp_path / "snemSA.m")
    env = {**os.environ, fetch.CASE_DIR_ENV: str(tmp_path)}
    result = subprocess.run([sys.executable, str(REPO / "benchmarks/lab01-simple-loadflow-fit/run.py"), "--step", "check"],
                            capture_output=True, text=True, env=env, cwd=tmp_path)
    assert result.returncode == 0 and "MATCH" in result.stdout, result.stdout + result.stderr


def test_a_missing_case_file_says_how_to_get_it(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib", reason="lab 1 draws charts; install the labs extra (uv sync --extra labs)")
    env = {**os.environ, fetch.CASE_DIR_ENV: str(tmp_path)}
    result = subprocess.run([sys.executable, str(REPO / "benchmarks/lab01-simple-loadflow-fit/run.py"), "--step", "check"],
                            capture_output=True, text=True, env=env, cwd=tmp_path)
    assert result.returncode != 0 and "fetch" in (result.stdout + result.stderr)


def test_the_mcp_server_process_receives_wattson_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the MCP library starts servers with a minimal environment, so WATTSON_CASE_DIR never arrived and the server looked in
    <repo>/data. An EMPTY case directory must now make the server fail naming that directory, which proves the variable got through."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from wattson.mcp_env import server_environment

    monkeypatch.setenv(fetch.CASE_DIR_ENV, str(tmp_path))

    async def solve() -> str:
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "wattson.inference.mcp_server", "--study-tools"], env=server_environment())
        async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            reply = await session.call_tool("run_study_trial", {"network_id": "snem-sa-2000", "operation": "none", "max_iter": 50})
            return reply.content[0].text  # an error string, not JSON, when the tool fails before returning a study

    assert str(tmp_path) in asyncio.run(asyncio.wait_for(solve(), timeout=90))


def test_server_environment_includes_wattson_variables_and_the_librarys_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from wattson.mcp_env import server_environment

    monkeypatch.setenv("WATTSON_ANYTHING", "x")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    env = server_environment()
    assert env["WATTSON_ANYTHING"] == "x" and "PATH" in env and "UNRELATED_SECRET" not in env
