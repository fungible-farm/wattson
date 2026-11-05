"""The GPU lease admits one exclusive engine at a time, whatever its purpose is called (#29)."""
import subprocess
import sys

from wattson.inference.gpu_lease import GPULease


def test_second_exclusive_engine_with_a_different_purpose_is_refused(tmp_path):
    first, second = GPULease(str(tmp_path)), GPULease(str(tmp_path))
    assert first.acquire("llm-chat", timeout=0)
    assert not second.acquire("image-gen", timeout=0)
    assert first.holder().startswith("llm-chat:")
    first.release()


def test_same_purpose_is_also_refused_while_held(tmp_path):
    first, second = GPULease(str(tmp_path)), GPULease(str(tmp_path))
    assert first.acquire("llm-chat", timeout=0)
    assert not second.acquire("llm-chat", timeout=0)
    first.release()


def test_lease_is_free_again_after_release(tmp_path):
    first, second = GPULease(str(tmp_path)), GPULease(str(tmp_path))
    assert first.acquire("a", timeout=0)
    first.release()
    assert second.acquire("b", timeout=0)
    assert second.holder().startswith("b:")
    second.release()


def test_context_manager_releases(tmp_path):
    with GPULease(str(tmp_path)) as lease:
        assert lease.acquire("a", timeout=0)
    assert GPULease(str(tmp_path)).acquire("b", timeout=0)


def test_a_second_process_is_refused_while_this_one_holds_the_lease(tmp_path):
    lease = GPULease(str(tmp_path))
    assert lease.acquire("llm-chat", timeout=0)
    code = ("import sys; from wattson.inference.gpu_lease import GPULease; "
            "sys.exit(0 if GPULease(sys.argv[1]).acquire('stt', timeout=0) else 3)")
    refused = subprocess.run([sys.executable, "-c", code, str(tmp_path)])
    assert refused.returncode == 3
    lease.release()
    admitted = subprocess.run([sys.executable, "-c", code, str(tmp_path)])
    assert admitted.returncode == 0


def test_lock_file_is_not_unlinked_on_release(tmp_path):
    lease = GPULease(str(tmp_path))
    assert lease.acquire("a", timeout=0)
    lease.release()
    assert (tmp_path / "gpu.lock").exists()
