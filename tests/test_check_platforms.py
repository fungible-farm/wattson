"""The publish guard: an index missing a required platform fails, including the arm64-only index CI once published."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

SCRIPT: Path = Path(__file__).resolve().parents[1] / "scripts" / "check_platforms.py"


def load():
    spec = importlib.util.spec_from_file_location("check_platforms", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(arch: str, os_: str = "linux") -> dict:
    return {"mediaType": "application/vnd.oci.image.manifest.v1+json", "platform": {"os": os_, "architecture": arch}}


# What the first publish actually produced: one arm64 image and its attestation (unknown/unknown).
ARM64_ONLY: dict = {"manifests": [entry("arm64"), entry("unknown", "unknown")]}
BOTH: dict = {"manifests": [entry("amd64"), entry("arm64"), entry("unknown", "unknown"), entry("unknown", "unknown")]}


def run(index: dict, monkeypatch: pytest.MonkeyPatch, *required: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(index)))
    return load().main(list(required))


def test_the_arm64_only_index_that_was_published_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(ARM64_ONLY, monkeypatch, "linux/amd64", "linux/arm64") == 1
    assert "missing platform linux/amd64" in capsys.readouterr().out


def test_an_index_with_both_platforms_passes_and_attestations_do_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(BOTH, monkeypatch, "linux/amd64", "linux/arm64") == 0


def test_an_attestation_alone_is_not_a_platform() -> None:
    assert load().platforms({"manifests": [entry("unknown", "unknown")]}) == set()
