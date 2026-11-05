"""Exercise actual shell boundaries with hostile literal arguments, without a model."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT: Path = Path(__file__).resolve().parents[1]
# Shell dispatch must finish without acquiring images or contacting a server.
TIMEOUT_SECONDS: int = 15


@pytest.mark.parametrize("client,key", [("generic", "mcpServers"), ("vscode", "servers")])
def test_generated_excel_config_uses_the_same_absolute_launch_contract(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, client: str, key: str,
) -> None:
    """Workbook paths are data in JSON argv, including spaces and shell syntax."""
    from wattson.cli import main

    workbook = "/approved input/2026 ISP $(literal).xlsm"
    monkeypatch.setenv("WATTSON_TAG", "reviewed-commit")
    monkeypatch.setenv("EXCEL_MCP_IMAGE", "localhost/excel:reviewed")
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-copy")
    assert main(["mcp-config", "excel-mcp", "--launcher", "/tools/wattson", "--client", client,
                 "--workbook", workbook]) == 0
    config = json.loads(capsys.readouterr().out)
    entry = config[key]["excel-mcp"]
    assert entry["command"] == "/tools/wattson"
    assert entry["args"] == ["service", "excel-mcp", "stdio", workbook]
    assert (entry.get("type") == "stdio") == (client == "vscode")
    assert entry["env"]["WATTSON_TAG"] == "reviewed-commit"
    assert entry["env"]["EXCEL_MCP_IMAGE"] == "localhost/excel:reviewed"
    assert "must-never-copy" not in json.dumps(config)


@pytest.fixture
def fake_tools(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Capture exact worker arguments; never run Podman or scientific commands."""
    log = tmp_path / "arguments.jsonl"
    for name in ("podman", "wattson"):
        command = tmp_path / name
        command.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                           "with open(os.environ['ARGUMENT_LOG'], 'a') as f: "
                           "f.write(json.dumps(sys.argv[1:]) + '\\n')\n")
        command.chmod(0o755)
    return os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "ARGUMENT_LOG": str(log)}, log


@pytest.mark.parametrize("layer", ["host", "container"])
def test_question_and_flags_are_literal_at_each_shell_boundary(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path], layer: str,
) -> None:
    """A question may contain quotes/substitution syntax without executing it."""
    just = shutil.which("just")
    if not just:
        pytest.skip("host just absent; container recipe checks need just")
    env, log = fake_tools
    marker = tmp_path / "executed"
    payload = f"a quote ' and \"; $(touch {marker}) `touch {marker}`"
    justfile = ROOT / ("justfile" if layer == "host" else "containers/justfile")
    result = subprocess.run([just, "--justfile", str(justfile), "ask", payload, "--enforce"],
                            cwd=tmp_path, env=env, text=True, capture_output=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert not marker.exists()
    assert any(payload in args and "--enforce" in args for args in calls), calls


def test_service_dispatch_does_not_depend_on_caller_directory(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path],
) -> None:
    """IDE launchers can call the absolute wrapper with no configured cwd."""
    env, log = fake_tools
    result = subprocess.run([str(ROOT / "wattson"), "service", "dbt-mcp", "stdio"],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    run = next(call for call in calls if call[0] == "run")
    assert "--network=none" in run and "--read-only" in run
    assert f"{ROOT}/integrations/dbt-mcp/sample:/project:ro,Z" in run
    assert result.stdout == ""


def test_unknown_service_fails_without_starting_a_container(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path],
) -> None:
    """A typo cannot silently select another tool or report success."""
    env, log = fake_tools
    result = subprocess.run([str(ROOT / "wattson"), "service", "missing", "stdio"],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode == 2 and not log.exists()


def test_power_mcp_uses_the_trained_report_protocol(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path],
) -> None:
    """The generic IDE adapter must expose the same trained Python tool contract."""
    from wattson.cli import TOOL_FLAGS

    env, log = fake_tools
    result = subprocess.run([str(ROOT / "wattson"), "service", "power-mcp", "stdio"],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode == 0, result.stderr
    args = json.loads(log.read_text().splitlines()[-1])
    assert set(TOOL_FLAGS) <= set(args)
    assert "--network=none" in args and "--read-only" in args


def test_container_delegation_mounts_only_explicit_task_and_readonly_source(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path],
) -> None:
    """The minimal-host review path must not require host pi or writable code."""
    env, log = fake_tools
    task = tmp_path / "task with spaces.txt"
    task.write_text("Read wattson; report one concrete interface defect.")
    output = tmp_path / "new evidence"
    result = subprocess.run([str(ROOT / "wattson"), "delegate", str(task), str(output)],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode == 0, result.stderr
    args = json.loads(log.read_text().splitlines()[-1])
    assert f"{ROOT}:/review:ro,Z" in args
    assert f"{task}:/task.txt:ro,Z" in args and f"{output}:/evidence:rw,Z" in args
    assert "--workdir" in args and "/review" in args
    assert "--read-only" in args and "--memory-swap=2g" in args
    assert "podman.sock" not in json.dumps(args)


def test_delegate_passes_base_url_and_model_defaults(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path],
) -> None:
    """Default URL and model are passed to the container command."""
    env, log = fake_tools
    task = tmp_path / "task.txt"
    task.write_text("Read wattson; report one concrete interface defect.")
    output = tmp_path / "evidence"
    result = subprocess.run([str(ROOT / "wattson"), "delegate", str(task), str(output)],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode == 0, result.stderr
    args = json.loads(log.read_text().splitlines()[-1])
    assert "--base-url" in args and "http://192.168.1.137:8002/v1" in args
    assert "--model" in args and "neo-coder" in args


def test_delegate_overrides_url_and_model_via_env(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path],
) -> None:
    """WATTSON_DELEGATE_URL and WATTSON_DELEGATE_MODEL override defaults."""
    env, log = fake_tools
    env["WATTSON_DELEGATE_URL"] = "http://10.0.0.5:9000/v1"
    env["WATTSON_DELEGATE_MODEL"] = "granite-4.2-8b"
    task = tmp_path / "task.txt"
    task.write_text("Read wattson; report one concrete interface defect.")
    output = tmp_path / "evidence"
    result = subprocess.run([str(ROOT / "wattson"), "delegate", str(task), str(output)],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode == 0, result.stderr
    args = json.loads(log.read_text().splitlines()[-1])
    assert "--base-url" in args and "http://10.0.0.5:9000/v1" in args
    assert "--model" in args and "granite-4.2-8b" in args


def test_delegate_missing_task_file_fails_without_creating_output(
    tmp_path: Path, fake_tools: tuple[dict[str, str], Path],
) -> None:
    """A missing task file must exit non-zero without creating the output directory."""
    env, log = fake_tools
    task = tmp_path / "missing.txt"
    output = tmp_path / "evidence"
    result = subprocess.run([str(ROOT / "wattson"), "delegate", str(task), str(output)],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    assert result.returncode != 0
    assert not output.exists()
