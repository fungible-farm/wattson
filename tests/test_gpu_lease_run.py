"""`scripts/gpu_lease_run.py` runs a command under the exclusive GPU lease and refuses a second engine (#29)."""
import os
import subprocess
import sys
import time

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "gpu_lease_run.py")


def run(lock_dir, purpose, *command, timeout="0"):
    return subprocess.run([sys.executable, SCRIPT, purpose, "--timeout", timeout, "--lock-dir", str(lock_dir), "--", *command],
                          capture_output=True, text=True)


def test_runs_the_command_and_passes_its_exit_status_through(tmp_path):
    assert run(tmp_path, "llm", "true").returncode == 0
    failed = run(tmp_path, "llm", sys.executable, "-c", "raise SystemExit(3)")
    assert failed.returncode == 3
    assert "lease released" in failed.stderr


def test_a_second_engine_is_refused_with_75_and_the_holder_is_named(tmp_path):
    holder = subprocess.Popen([sys.executable, SCRIPT, "llm-chat", "--lock-dir", str(tmp_path), "--", sys.executable, "-c",
                               "import time; time.sleep(30)"], stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):  # wait until the first engine really holds the lease
            if (tmp_path / "gpu.lock").exists() and (tmp_path / "gpu.lock").read_text().startswith("llm-chat:"):
                break
            time.sleep(0.1)
        refused = run(tmp_path, "image-gen", sys.executable, "-c", "print('must not run')")
        assert refused.returncode == 75
        assert "llm-chat:" in refused.stderr and "nothing was started" in refused.stderr
        assert "must not run" not in refused.stdout
    finally:
        holder.kill()
        holder.wait()


def test_the_lease_is_free_again_after_the_first_engine_ends_even_when_it_fails(tmp_path):
    assert run(tmp_path, "a", sys.executable, "-c", "raise SystemExit(7)").returncode == 7
    second = run(tmp_path, "b", "true")
    assert second.returncode == 0 and "lease acquired for 'b'" in second.stderr


def test_a_command_is_required(tmp_path):
    result = subprocess.run([sys.executable, SCRIPT, "x", "--lock-dir", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 2
