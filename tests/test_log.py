"""Structured logging: JSON lines carry the event and its fields; text is readable; the file sink is always JSON."""

from __future__ import annotations

import json

import pytest

from wattson import log


def test_json_lines_carry_event_component_and_fields(capsys: pytest.CaptureFixture[str]) -> None:
    log.configure("INFO", "json")
    log.get("unit").info("thing.happened", extra={"count": 3, "path": "/x"})
    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["event"] == "thing.happened" and line["component"] == "wattson.unit"
    assert line["count"] == 3 and line["path"] == "/x" and line["level"] == "INFO" and line["ts"].endswith("Z")


def test_level_filters_and_text_format_is_readable(capsys: pytest.CaptureFixture[str]) -> None:
    log.configure("WARNING", "text")
    log.get("unit").info("quiet")
    log.get("unit").warning("loud", extra={"k": "v"})
    err = capsys.readouterr().err
    assert "quiet" not in err and "loud" in err and "k=v" in err


def test_file_sink_is_json_even_when_stderr_is_text(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "deep" / "run.log"
    log.configure("INFO", "text", str(target))
    log.get("unit").info("to.file", extra={"n": 1})
    assert json.loads(target.read_text().strip())["n"] == 1


def test_bad_format_is_refused() -> None:
    with pytest.raises(ValueError):
        log.configure("INFO", "yaml")


def test_environment_configures_without_code(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(log.LEVEL_ENV, "ERROR")
    monkeypatch.setenv(log.FORMAT_ENV, "json")
    log.configure()
    log.get("unit").warning("hidden")
    assert capsys.readouterr().err == ""


def test_an_unwritable_log_file_is_a_warning_not_a_crash(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: a container bind mount owned by another user made `wattson doctor` die with PermissionError before it ran any check."""
    blocker = tmp_path / "file-not-a-directory"
    blocker.write_text("x")
    log.configure("INFO", "json", str(blocker / "out" / "run.log"))  # parent is a file: mkdir raises NotADirectoryError (an OSError)
    log.get("unit").info("still.works")
    err = capsys.readouterr().err
    assert "log.file.unavailable" in err and "still.works" in err
