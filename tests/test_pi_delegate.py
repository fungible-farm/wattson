"""Process-boundary regressions for local pi delegation; no real model is needed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import pytest

from scripts import pi_delegate as delegate

EVENTS: list[dict] = [
    {"type": "turn_start"},
    {"type": "tool_execution_start", "toolCallId": "r1", "toolName": "read", "args": {"path": "example.py"}},
    {"type": "tool_execution_end", "toolCallId": "r1", "toolName": "read", "isError": False,
     "result": {"content": [{"type": "text", "text": "actual fixture file content"}]}},
    {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
                                           "content": [{"type": "text", "text": "Read example.py; no finding in this fixture."}]}},
    {"type": "agent_settled", "aborted": False},
]


def stub(tmp_path: Path, code: str) -> Path:
    """Create an executable subprocess fixture using the current interpreter."""
    executable = tmp_path / "pi-stub"
    executable.write_text(f"#!{sys.executable}\n" + code)
    executable.chmod(0o755)
    return executable


def options(tmp_path: Path, executable: Path, *extra: str) -> object:
    """Use explicit scratch work/evidence paths through the actual CLI parser."""
    return delegate.arguments(["--workdir", str(tmp_path), "--task", "Read example.py and review it.",
                               "--out-dir", str(tmp_path / "evidence"), "--pi", str(executable), *extra])


def emitting(events: list[dict]) -> str:
    """Emit actual LF-framed JSON records from a separate process."""
    return "import json\nfor event in " + repr(events) + ":\n print(json.dumps(event), flush=True)\n"


def test_real_boundary_uses_allowlist_and_isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "/private/personal/agent")
    executable = stub(tmp_path, f"import json, os, sys\nfrom pathlib import Path\nPath({str(capture)!r}).write_text(json.dumps({{'argv':sys.argv, 'env':dict(os.environ)}}))\n" + emitting(EVENTS))
    report = delegate.run(options(tmp_path, executable))
    captured = json.loads(capture.read_text())
    assert report["status"] == "complete" and report["correctness_verified"] is False
    assert captured["argv"][captured["argv"].index("--tools") + 1] == "read,grep,find,ls"
    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["HOME"].startswith(str(tmp_path / "evidence/isolated"))
    assert captured["env"]["PI_CODING_AGENT_DIR"] != "/private/personal/agent"
    assert {"--no-context-files", "--no-extensions", "--no-mcp", "--no-approve", "--no-session"} <= set(captured["argv"])
    assert len((tmp_path / "evidence/events.jsonl").read_text().splitlines()) == len(EVENTS)
    model_config = json.loads((tmp_path / "evidence/isolated/agent/models.json").read_text())
    assert set(model_config["providers"]) == {delegate.PROVIDER}
    assert model_config["providers"][delegate.PROVIDER]["baseUrl"] == delegate.DEFAULT_URL


@pytest.mark.parametrize("events, expected", [
    ([EVENTS[0], *EVENTS[3:]], "no_read_evidence"),
    ([EVENTS[0], EVENTS[1], {**EVENTS[2], "isError": True}, *EVENTS[3:]], "no_read_evidence"),
    ([*EVENTS[:3], {**EVENTS[3], "message": {**EVENTS[3]["message"], "stopReason": "length"}}, EVENTS[4]], "incomplete"),
    ([*EVENTS[:3], {**EVENTS[3], "message": {**EVENTS[3]["message"], "stopReason": "error"}}, EVENTS[4]], "failed"),
    ([*EVENTS[:4], {"type": "agent_settled", "aborted": True}], "incomplete"),
    ([{**EVENTS[1], "toolName": "bash"}], "protocol_error"),
    ([EVENTS[2]], "protocol_error"),
])
def test_model_claim_or_partial_completion_is_not_success(tmp_path: Path, events: list[dict], expected: str) -> None:
    report = delegate.run(options(tmp_path, stub(tmp_path, emitting(events))))
    assert report["status"] == expected


@pytest.mark.parametrize("events, flags", [
    ([{"type": "turn_start"}] * 3, ["--max-turns", "2"]),
    ([{**EVENTS[1], "toolCallId": f"r{i}"} for i in range(3)], ["--max-tool-calls", "2"]),
])
def test_event_budgets_stop_the_process(tmp_path: Path, events: list[dict], flags: list[str]) -> None:
    executable = stub(tmp_path, emitting(events) + "import time\ntime.sleep(30)\n")
    report = delegate.run(options(tmp_path, executable, *flags))
    assert report["status"] == "budget_exceeded" and report["elapsed_seconds"] < 5


def test_output_cap_bounds_evidence_bytes(tmp_path: Path) -> None:
    executable = stub(tmp_path, "import os, time\nos.write(1,b'x'*4096)\ntime.sleep(30)\n")
    report = delegate.run(options(tmp_path, executable, "--max-output-bytes", "128"))
    assert report["status"] == "output_limit"
    assert (tmp_path / "evidence/events.jsonl").stat().st_size <= 128


def process_running(pid: int) -> bool:
    """True while the process can still run. It may be reaped between any two /proc accesses, so read once and treat a vanished entry as dead."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_deadline_cleans_up_a_descendant_ignoring_sigterm(tmp_path: Path) -> None:
    pidfile = tmp_path / "descendant.pid"
    child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
    executable = stub(tmp_path, f"import subprocess,time\nfrom pathlib import Path\np=subprocess.Popen([{sys.executable!r},'-c',{child!r}])\nPath({str(pidfile)!r}).write_text(str(p.pid))\ntime.sleep(30)\n")
    report = delegate.run(options(tmp_path, executable, "--timeout", "1"))
    assert report["status"] == "timed_out" and report["elapsed_seconds"] < 5
    pid = int(pidfile.read_text())
    # A briefly unreaped zombie is dead; it cannot run or retain model/tool work.
    for _ in range(20):
        if not process_running(pid):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"owned descendant {pid} is still running")


def test_task_file_remains_data_and_existing_evidence_is_not_overwritten(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-execute"
    payload = f"Read example.py. Literal data: ' $(touch {marker}) --no-tools"
    task = tmp_path / "task.txt"
    task.write_text(payload)
    capture = tmp_path / "argv.json"
    executable = stub(tmp_path, f"import sys,json\nfrom pathlib import Path\nPath({str(capture)!r}).write_text(json.dumps(sys.argv))\n" + emitting(EVENTS))
    args = delegate.arguments(["--workdir", str(tmp_path), "--task-file", str(task), "--out-dir", str(tmp_path / "evidence"), "--pi", str(executable)])
    assert delegate.run(args)["status"] == "complete"
    assert json.loads(capture.read_text())[-1].endswith(payload)
    assert not marker.exists()
    original = (tmp_path / "evidence/report.json").read_bytes()
    with pytest.raises(FileExistsError):
        delegate.run(args)
    assert (tmp_path / "evidence/report.json").read_bytes() == original


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "https://8.8.8.8/v1", "http://user:secret@127.0.0.1/v1", "http://localhost/v1?api_key=secret"])
def test_cloud_and_credential_bearing_endpoint_is_rejected(url: str) -> None:
    with pytest.raises(Exception):
        delegate.local_url(url)


def test_actions_need_explicit_isolated_workdir(tmp_path: Path) -> None:
    executable = stub(tmp_path, emitting(EVENTS))
    with pytest.raises(SystemExit):
        options(tmp_path, executable, "--allow-actions")
    with pytest.raises(SystemExit):
        delegate.arguments(["--action-workdir", str(delegate.ROOT), "--allow-actions", "--task", "edit", "--out-dir", str(tmp_path / "evidence"), "--pi", str(executable)])
    args = delegate.arguments(["--action-workdir", str(tmp_path), "--allow-actions", "--task", "edit", "--out-dir", str(tmp_path / "evidence"), "--pi", str(executable)])
    assert delegate.run(args)["status"] == "complete"


def test_literal_task_text_is_not_a_path(tmp_path: Path) -> None:
    executable = stub(tmp_path, emitting(EVENTS))
    args = options(tmp_path, executable, "--task", "literal text not a path")
    assert args.prompt == "literal text not a path"


def test_existing_output_directory_is_refused(tmp_path: Path) -> None:
    executable = stub(tmp_path, emitting(EVENTS))
    out = tmp_path / "evidence"
    out.mkdir()
    args = options(tmp_path, executable)
    with pytest.raises(FileExistsError):
        delegate.run(args)


def test_invalid_timeout_env_gives_argparse_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATTSON_DELEGATE_TIMEOUT", "zero")
    executable = stub(tmp_path, emitting(EVENTS))
    with pytest.raises(SystemExit) as raised:
        delegate.arguments(["--workdir", str(tmp_path), "--task", "read",
                            "--out-dir", str(tmp_path / "evidence"), "--pi", str(executable)])
    assert raised.value.code == 2


def test_oversized_task_is_rejected_before_launch(tmp_path: Path) -> None:
    executable = stub(tmp_path, emitting(EVENTS))
    task = tmp_path / "large-task.txt"
    task.write_text("x" * (delegate.MAX_TASK_BYTES + 1))
    with pytest.raises(SystemExit):
        delegate.arguments(["--workdir", str(tmp_path), "--task-file", str(task),
                            "--out-dir", str(tmp_path / "evidence"), "--pi", str(executable)])
    assert not (tmp_path / "evidence").exists()
