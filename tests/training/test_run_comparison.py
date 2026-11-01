"""CPU gates for the comparison driver: memory admission and server readiness."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from wattson.training import run_comparison as driver

GIB: int = 1 << 30


def meminfo(path: Path, available_gib: int) -> Path:
    """Write a /proc/meminfo-shaped file with a 121 GiB total, like the GB10."""
    path.write_text(f"MemTotal:       {121 * GIB // 1024} kB\n"
                    f"MemFree:        {GIB // 1024} kB\n"
                    f"MemAvailable:   {available_gib * GIB // 1024} kB\n")
    return path


def events(path: Path) -> list[dict]:
    """Read back the fsynced journal the gate wrote."""
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_meminfo_bytes_converts_kib(tmp_path: Path) -> None:
    info = meminfo(tmp_path / "meminfo", 21)
    assert driver.meminfo_bytes("MemTotal", info) == 121 * GIB
    assert driver.meminfo_bytes("MemAvailable", info) == 21 * GIB
    with pytest.raises(KeyError):
        driver.meminfo_bytes("SwapTotal", info)


def test_admits_when_floor_plus_margin_is_available(tmp_path: Path) -> None:
    info = meminfo(tmp_path / "meminfo", 40)
    journal = tmp_path / "pipeline.jsonl"
    with journal.open("x") as stream:
        driver.await_memory("granite-training", driver.GRANITE_WEIGHT_BYTES, stream, meminfo=info,
                            sleep=lambda _s: pytest.fail("must not wait"))
    assert [e["kind"] for e in events(journal)] == ["memory_admitted"]


def test_waits_for_a_gateway_unload_then_admits(tmp_path: Path) -> None:
    # The 2026-10-21 condition: a resident llama-swap model left ~21 GiB free.
    info = meminfo(tmp_path / "meminfo", 21)
    journal = tmp_path / "pipeline.jsonl"
    sleeps: list[float] = []

    def unload(seconds: float) -> None:
        sleeps.append(seconds)
        meminfo(info, 110)

    with journal.open("x") as stream:
        driver.await_memory("granite-base", int(0.22 * 121 * GIB), stream, meminfo=info, sleep=unload)
    assert [e["kind"] for e in events(journal)] == ["memory_wait", "memory_admitted"]
    assert sleeps == [driver.POLL_INTERVAL_S]


def test_refuses_after_timeout_without_starting_the_stage(tmp_path: Path) -> None:
    info = meminfo(tmp_path / "meminfo", 3)
    journal = tmp_path / "pipeline.jsonl"
    now = iter([0.0, 0.0, 10.0])
    with journal.open("x") as stream, pytest.raises(RuntimeError, match="MemAvailable"):
        driver.await_memory("qwen-base", int(0.12 * 121 * GIB), stream, meminfo=info,
                            timeout=5.0, sleep=lambda _s: None, clock=lambda: next(now))
    assert [e["kind"] for e in events(journal)] == ["memory_wait", "memory_refused"]


def test_serve_waits_through_connection_resets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Rootless podman port forwarding resets connections before vLLM listens.
    answers = iter([httpx.ReadError("Connection reset by peer"), httpx.ConnectError("refused"),
                    httpx.Response(200, json={"data": [{"id": "wattson-qwen-base"}]})])

    def respond(request: httpx.Request) -> httpx.Response:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    real_client = httpx.Client
    monkeypatch.setattr(driver.httpx, "Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(driver, "command", lambda *_args: None)
    monkeypatch.setattr(driver, "container_info", lambda _name: {"state": {"Running": True}})
    monkeypatch.setattr(driver, "await_memory", lambda *_args: 0)
    monkeypatch.setattr(driver, "meminfo_bytes", lambda _field: 121 * GIB)
    monkeypatch.setattr(driver.time, "sleep", lambda _s: None)
    journal = tmp_path / "pipeline.jsonl"
    with journal.open("x") as stream:
        driver.serve(tmp_path, "wattson-test-qwen-base", "wattson-qwen-base", "qwen3", tmp_path, stream)
    assert [e["kind"] for e in events(journal)] == ["server_ready"]


def test_parallel_runs_every_stage_before_reporting_a_failure(tmp_path: Path) -> None:
    # A fast failure (Granite training) must not cut short a slow evaluation.
    marker = tmp_path / "slow-finished"
    stages = [(["sh", "-c", "exit 3"], tmp_path / "fail.log"),
              (["sh", "-c", f"sleep 0.3 && touch {marker}"], tmp_path / "slow.log")]
    journal = tmp_path / "pipeline.jsonl"
    with journal.open("x") as stream, pytest.raises(RuntimeError, match="exited 3"):
        driver.parallel(stages, stream)
    assert marker.exists()
    kinds = [e["kind"] for e in events(journal)]
    assert kinds.count("stage_start") == 2 and kinds.count("stage_exit") == 2


def test_a_stage_past_its_deadline_is_killed_and_journaled_as_a_failure(tmp_path: Path) -> None:
    # GRPO pilot r7 iteration 1: a hung stage held the driver and the GPU for hours.
    journal = tmp_path / "pipeline.jsonl"
    with journal.open("x") as stream, pytest.raises(RuntimeError, match=f"exited {driver.STAGE_TIMEOUT_EXIT}"):
        driver.command(["sleep", "30"], tmp_path / "hung.log", stream, timeout_s=0.5)
    exits = [e["payload"]["exit_code"] for e in events(journal) if e["kind"] == "stage_exit"]
    assert exits == [driver.STAGE_TIMEOUT_EXIT]


def test_granite_only_run_reuses_qwen_and_serves_one_granite_at_a_time(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    # r4's side-by-side Granite servers decoded ~4 tok/s per stream, past the timeout.
    served: list[list[str]] = []
    verified: list[Path] = []
    student = tmp_path / "granite-out" / "merged-bf16"
    student.mkdir(parents=True)
    (student / "config.json").write_text("{}")
    config = tmp_path / "config.yaml"
    config.write_text(f"model: {{name: ibm-granite/granite-4.2-8b}}\noutput: {{dir: {student.parent}}}\n")
    monkeypatch.setattr(driver, "GRANITE_CONFIG", config)
    monkeypatch.setattr(driver, "container_info", lambda _name: {"state": {"Running": False, "ExitCode": 0}})
    monkeypatch.setattr(driver, "verify_base", lambda _model: {})
    monkeypatch.setattr(driver, "verify_evaluation", lambda directory, _events: verified.append(directory))
    monkeypatch.setattr(driver, "evaluate_pair", lambda pair, *_a, **_k: served.append([p[0] for p in pair]))
    monkeypatch.setattr(driver, "write_comparison", lambda *_a: {"domain_threshold_met": False})
    monkeypatch.setattr(driver, "write_numerical_comparison", lambda *_a: {"domain_threshold_met": False})
    previous = tmp_path / "agent-domain-r4"
    args = driver.argparse.Namespace(root=tmp_path / "r5", download_container="dl", qwen_from=previous)
    journal = tmp_path / "pipeline.jsonl"
    with journal.open("x") as stream:
        driver.run(args, stream)
    assert verified == [previous / "base", previous / "student"]
    assert served == [["granite-base"], ["granite-student"]]
    assert events(journal)[-1]["kind"] == "pipeline_complete"
