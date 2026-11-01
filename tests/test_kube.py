"""The quick-start pod is structurally what the docs promise, and the lessons learnt from running it stay learnt."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO: Path = Path(__file__).resolve().parents[1]
FILLED: dict[str, str] = {
    "@IMAGE@": "img:t", "@VLLM_IMAGE@": "vllm:t", "@MODEL_REPO@": "o/n", "@MODEL_DIR@": "dir", "@REASONING@": "qwen3", "@GPU_UTIL@": "0.5",
    "@MAX_LEN@": "32768", "@LOG_LEVEL@": "INFO", "@HOST_PORT@": "8000", "@WORK@": "/w"}


def pod() -> dict:
    text = (REPO / "kube" / "wattson.yaml").read_text()
    for marker, value in FILLED.items():
        text = text.replace(marker, value)
    return yaml.safe_load(text)


def containers() -> dict[str, dict]:
    return {c["name"]: c for c in pod()["spec"]["containers"]}


def test_every_marker_in_the_template_is_one_the_tests_and_justfile_know() -> None:
    text = (REPO / "kube" / "wattson.yaml").read_text()
    import re

    found = set(re.findall(r"@[A-Z_]+@", text))
    assert found <= set(FILLED), found - set(FILLED)
    justfile = (REPO / "justfile").read_text()
    assert all(f"s|{m}|" in justfile for m in found), [m for m in found if f"s|{m}|" not in justfile]


def test_the_gpu_is_requested_by_cdi_device_name() -> None:
    """Regression: `nvidia.com/gpu: 1` attaches nothing under podman kube play; vLLM then fails with 'Failed to infer device type'."""
    limits = containers()["vllm"]["resources"]["limits"]
    assert "nvidia.com/gpu=all" in limits and "nvidia.com/gpu" not in limits


def test_the_server_and_the_harness_agree_on_where_the_model_is_and_how_to_reach_it() -> None:
    c = containers()
    model_arg = next(a for a in c["vllm"]["args"] if a.startswith("--model="))
    harness_env = {e["name"]: e["value"] for e in c["harness"]["env"]}
    assert model_arg == f"--model={harness_env['WATTSON_MODEL_DIR']}"
    assert harness_env["WATTSON_BASE_URL"] == "http://127.0.0.1:8000/v1"  # same pod, same network namespace
    assert "--port=8000" in c["vllm"]["args"] and c["vllm"]["ports"][0]["containerPort"] == 8000


def test_the_server_is_started_with_the_flags_the_students_were_evaluated_with() -> None:
    args = containers()["vllm"]["args"]
    for flag in ("--dtype=bfloat16", "--max-model-len=32768", "--enforce-eager", "--enable-auto-tool-choice", "--tool-call-parser=qwen3_coder", "--served-model-name=wattson"):
        assert flag in args, flag


def test_the_model_is_pulled_before_the_server_starts_and_is_mounted_read_only_into_it() -> None:
    spec = pod()["spec"]
    init = {c["name"]: c for c in spec["initContainers"]}
    assert init["pull-model"]["command"][:3] == ["wattson", "models", "pull"]
    mounts = {m["name"]: m for m in containers()["vllm"]["volumeMounts"]}
    assert mounts["models"]["readOnly"] is True


def test_the_host_user_is_mapped_to_the_images_user_so_the_work_directory_is_writable() -> None:
    """Regression: rootless podman mapped the host user to root, and uid 1000 in the image could not write to the mounted /work."""
    assert pod()["metadata"]["annotations"]["io.podman.annotations.userns"].startswith("keep-id:uid=1000")


def test_the_server_binds_to_loopback_on_the_host_only() -> None:
    port = containers()["vllm"]["ports"][0]
    assert port["hostIP"] == "127.0.0.1"


def test_the_harness_stops_promptly_on_sigterm() -> None:
    """Regression: `sleep infinity` as PID 1 ignores SIGTERM, so stopping the pod waited out the 10 s grace period and then killed it."""
    command = containers()["harness"]["command"]
    assert command[:2] == ["bash", "-c"] and "trap" in command[2] and "TERM" in command[2]
