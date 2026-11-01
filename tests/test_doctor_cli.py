"""The doctor reports PASS/FAIL with evidence and a usable exit code; the CLI wires subcommands; pi is configured without clobbering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattson import cli, datacheck, doctor


def test_python_and_pins_pass_in_this_environment() -> None:
    assert doctor._timed("python", doctor.python_version).status == doctor.PASS
    assert doctor._timed("pins", doctor.pinned_dependencies).status == doctor.PASS


def test_data_check_fails_on_a_tampered_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    shutil.copytree(datacheck.data_dir(), tmp_path / "d", ignore=shutil.ignore_patterns("*.m"))
    suite = next((tmp_path / "d" / "suites").glob("*/examples.jsonl"))
    suite.write_text(suite.read_text() + "{}\n")
    monkeypatch.setenv(datacheck.DATA_ENV, str(tmp_path / "d"))
    check = doctor._timed("data", doctor.data_integrity)
    assert check.status == doctor.FAIL and "changed" in check.detail


def test_endpoint_check_fails_on_a_closed_port() -> None:
    check = doctor._timed("endpoint", doctor.model_endpoint("http://127.0.0.1:9/v1", deep=False))
    assert check.status == doctor.FAIL and "ConnectError" in check.detail and check.remedy


def test_a_crashing_check_becomes_a_fail_not_a_crash() -> None:
    def broken() -> tuple[str, str, str]:
        raise RuntimeError("boom")

    check = doctor._timed("broken", broken)
    assert check.status == doctor.FAIL and "boom" in check.detail


def test_render_and_json_cover_every_check() -> None:
    checks = [doctor.Check("a", doctor.PASS, "fine"), doctor.Check("b", doctor.FAIL, "bad", remedy="fix it")]
    text = doctor.render(checks)
    assert "FAIL=1" in text and "-> fix it" in text
    assert [c["name"] for c in json.loads(doctor.to_json(checks))] == ["a", "b"]


def test_cli_doctor_exit_code_is_one_only_on_fail(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(doctor, "run", lambda *a, **k: [doctor.Check("x", doctor.WARN, "careful")])
    assert cli.main(["doctor"]) == 0
    monkeypatch.setattr(doctor, "run", lambda *a, **k: [doctor.Check("x", doctor.FAIL, "broken")])
    assert cli.main(["doctor"]) == 1


def test_every_subcommand_parses() -> None:
    parser = cli.build_parser()
    for argv in (["doctor"], ["fetch-data"], ["verify-data"], ["models", "pull", "o/n", "--dest", "d"], ["models", "verify", "d"],
                 ["lab", "1"], ["lab", "3", "--step", "report"], ["eval", "smoke", "--base-url", "u", "--model", "m"],
                 ["ask", "q", "--base-url", "u", "--model", "m"], ["pi-setup", "--base-url", "u", "--model", "m"]):
        assert parser.parse_args(argv).run


def test_every_suite_the_cli_names_exists_in_data() -> None:
    for suite, directory in cli.SUITES.items():
        assert (datacheck.data_dir() / "suites" / directory / "examples.jsonl").exists(), suite
    assert (datacheck.data_dir() / cli.TRAIN_ROWS).exists()


def test_pi_setup_adds_a_provider_and_mcp_server_and_keeps_existing_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "models.json").write_text(json.dumps({"providers": {"other": {"baseUrl": "http://x"}}}))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    assert cli.main(["pi-setup", "--base-url", "http://127.0.0.1:8000/v1", "--model", "wattson"]) == 0
    models = json.loads((tmp_path / "models.json").read_text())["providers"]
    assert models["other"] == {"baseUrl": "http://x"} and models["wattson"]["models"][0]["id"] == "wattson"
    server = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["wattson"]
    assert "--python-tools" in server["args"] and server["args"][:2] == ["-m", "wattson.inference.mcp_server"]
    # Regression: pi's default hides MCP tools in a codemode wrapper and offers bash; the students then invented a command and a number.
    assert server["exposure"] == "direct" and json.loads((tmp_path / "mcp.json").read_text())["autoEnableCodemode"] is False


def test_final_answer_reads_the_last_message_and_counts_tool_calls(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    events = [{"kind": "model_response", "payload": {"choices": [{"message": {"tool_calls": [{"id": "1"}, {"id": "2"}]}}]}},
              {"kind": "model_response", "payload": {"choices": [{"message": {"content": "0.9250"}}]}}]
    journal.write_text("\n".join(json.dumps(e) for e in events))
    assert cli.final_answer(journal) == ("0.9250", 2)
    assert cli.final_answer(tmp_path / "missing.jsonl") == (None, 0)


def test_server_root_accepts_both_url_forms() -> None:
    """Regression: `.../v1` was passed through and the harness requested /v1/v1/chat/completions (404)."""
    for given in ("http://h:8000", "http://h:8000/", "http://h:8000/v1", "http://h:8000/v1/"):
        assert cli.server_root(given) == "http://h:8000"


def test_ask_reaches_a_model_server_given_an_openai_style_url_and_prints_its_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """End to end without a GPU: a stub chat-completions server stands in for vLLM; the real harness, MCP server and journal run."""
    import threading

    from wattson.training.parity_check import stub_server

    captured: list = []
    server = stub_server([{"role": "assistant", "content": "The scale factor is 0.9250."}], captured)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("WATTSON_OUT", str(tmp_path))
    try:
        code = cli.main(["ask", "What scale?", "--base-url", f"http://127.0.0.1:{server.server_address[1]}/v1", "--model", "stub"])
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert code == 0 and "The scale factor is 0.9250." in out and "0 tool call(s)" in out
    assert captured and captured[0]["messages"][-1]["content"] == "What scale?"
    assert captured[0]["tools"], "the harness must offer the tools to the model"


def test_pi_is_launched_the_way_the_students_were_trained() -> None:
    from wattson.inference.tool_probe import SYSTEM_PROMPT

    command = cli.pi_command("wattson", ["--print", "hello"])
    assert "--no-builtin-tools" in command, "no bash: offered bash, the students fabricate a command"
    assert command[command.index("--system-prompt") + 1] == SYSTEM_PROMPT and command[-2:] == ["--print", "hello"]


def test_a_guard_refusal_is_not_echoed_as_an_answer(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    events = [{"kind": "model_response", "payload": {"choices": [{"message": {"content": "The scale is 0.987654."}}]}},
              {"kind": "guard", "payload": {"action": "refused", "attempt": 2, "untraced": ["0.987654"]}},
              {"kind": "terminal", "payload": {"state": "guard_refused", "actual_tool_calls": 0}}]
    journal.write_text("\n".join(json.dumps(e) for e in events))
    assert cli.final_answer(journal) == (None, 0)


def test_enforce_reaches_the_evaluator_and_the_probe_command_lines() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["eval", "smoke", "--base-url", "u", "--model", "m", "--enforce"]).enforce is True
    assert parser.parse_args(["eval", "smoke", "--base-url", "u", "--model", "m"]).enforce is False
    assert parser.parse_args(["ask", "q", "--base-url", "u", "--model", "m", "--enforce"]).enforce is True
