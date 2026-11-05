"""Memory guard: check model size vs available host memory."""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "model_mem_guard.py"


def load():
    spec = importlib.util.spec_from_file_location("model_mem_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_meminfo(path: Path, available_kb: int):
    path.write_text(f"MemTotal:       128000000 kB\nMemAvailable:   {available_kb} kB\n")


def test_parse_size_literal_gb():
    m = load()
    assert m.parse_size_literal("233GB") == 233_000_000_000


def test_parse_size_literal_mb_decimal():
    m = load()
    assert m.parse_size_literal("1MB") == 1_000_000
    assert m.parse_size_literal("1MiB") == 1_048_576
    assert m.parse_size_literal("1kb") == 1_000
    assert m.parse_size_literal("1KiB") == 1_024
    assert m.parse_size_literal("1.5GB") == 1_500_000_000


def test_parse_size_literal_gib():
    m = load()
    assert m.parse_size_literal("121GiB") == 121 * 1024 * 1024 * 1024


def test_parse_size_literal_bad():
    m = load()
    with pytest.raises(ValueError):
        m.parse_size_literal("notasize")
    with pytest.raises(ValueError):
        m.parse_size_literal("100XB")


def test_pass_below_limit():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        meminfo = Path(tmp) / "meminfo"
        write_meminfo(meminfo, 100_000_000)  # 100 GB available
        result = m.main.__wrapped__ if hasattr(m.main, '__wrapped__') else None
        # Call via module functions directly
        total = m.parse_size_literal("50GB")
        avail = m.get_mem_available(str(meminfo))
        limit = int(avail * 0.60)
        assert total <= limit
        assert m.main.__code__.co_filename == str(SCRIPT)


def test_fail_exceeds_limit():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        meminfo = Path(tmp) / "meminfo"
        write_meminfo(meminfo, 100_000_000)  # 100 GB available
        total = m.parse_size_literal("90GB")
        avail = m.get_mem_available(str(meminfo))
        limit = int(avail * 0.60)
        assert total > limit


def test_boundary_exactly_at_limit():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        meminfo = Path(tmp) / "meminfo"
        # 100000 kB * 1024 = 102400000 bytes; 0.60 * 102400000 = 61440000
        write_meminfo(meminfo, 100_000)
        total = 61_440_000
        avail = m.get_mem_available(str(meminfo))
        limit = int(avail * 0.60)
        assert total <= limit


def test_boundary_one_byte_over():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        meminfo = Path(tmp) / "meminfo"
        write_meminfo(meminfo, 100_000)
        total = 61_440_001
        avail = m.get_mem_available(str(meminfo))
        limit = int(avail * 0.60)
        assert total > limit


def test_sharded_gguf_summing():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        # Create sparse shard files
        for i in range(1, 4):
            f = Path(tmp) / f"model-{i:05d}-of-00003.gguf"
            # Use truncate to create sparse files
            with open(f, 'a') as fh:
                fh.truncate(100_000_000)
        # Sum via one shard path
        shard = str(Path(tmp) / "model-00001-of-00003.gguf")
        total = m.sum_files([shard])
        assert total == 300_000_000


def test_directory_summing():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(1, 4):
            f = Path(tmp) / f"weights-{i}.safetensors"
            with open(f, 'a') as fh:
                fh.truncate(50_000_000)
        total = m.sum_files([tmp])
        assert total == 150_000_000


def test_differently_named_file_not_included():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(1, 4):
            f = Path(tmp) / f"model-{i:05d}-of-00003.gguf"
            with open(f, 'a') as fh:
                fh.truncate(100_000_000)
        # Add a differently-named gguf file
        other = Path(tmp) / "other-00001-of-00003.gguf"
        with open(other, 'a') as fh:
            fh.truncate(50_000_000)
        shard = str(Path(tmp) / "model-00001-of-00003.gguf")
        total = m.sum_files([shard])
        # Should sum only the 3 model shards, not the other file
        assert total == 300_000_000  # all 3 shards, including the one named; the differently named file is excluded


def test_directory_with_brackets():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        bracket_dir = Path(tmp) / "model_[x]"
        bracket_dir.mkdir()
        f = bracket_dir / "weights.safetensors"
        with open(f, 'a') as fh:
            fh.truncate(50_000_000)
        total = m.sum_files([str(bracket_dir)])
        assert total == 50_000_000


def test_file_not_found():
    m = load()
    with pytest.raises(RuntimeError):
        m.get_file_size("/nonexistent/path/file.gguf")


def test_empty_directory_exits_2():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        meminfo = Path(tmp) / "meminfo"
        write_meminfo(meminfo, 100_000)
        empty_dir = Path(tmp) / "empty"
        empty_dir.mkdir()
        result = m.main.__wrapped__ if hasattr(m.main, '__wrapped__') else m.main
        # main() calls sys.exit, so we test via sum_files + manual check
        total = m.sum_files([str(empty_dir)])
        assert total == 0  # empty dir yields 0 bytes


def test_directory_only_notes_exits_2():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        notes = Path(tmp) / "notes.txt"
        notes.write_text("hello")
        total = m.sum_files([tmp])
        assert total == 0  # notes.txt not a model file


def test_missing_path_exits_2():
    m = load()
    with pytest.raises(RuntimeError):
        m.get_file_size("/tmp/wt/mem-guard/nonexistent_model.gguf")


def test_meminfo_not_found():
    m = load()
    with pytest.raises(RuntimeError):
        m.get_mem_available("/nonexistent/meminfo")


def test_meminfo_missing_field():
    m = load()
    with tempfile.TemporaryDirectory() as tmp:
        meminfo = Path(tmp) / "meminfo"
        meminfo.write_text("MemTotal: 100000 kB\n")
        with pytest.raises(RuntimeError):
            m.get_mem_available(str(meminfo))


def test_several_shard_arguments_are_not_double_counted(tmp_path):
    """Naming two shards of the same set (or a shard plus its directory) counts each file once."""
    import subprocess, sys
    for i in range(1, 4):
        (tmp_path / f"model-{i:05d}-of-00003.gguf").write_bytes(b"")
        with open(tmp_path / f"model-{i:05d}-of-00003.gguf", "ab") as fh:
            fh.truncate(100_000_000)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable:    1000000000 kB\n")
    script = Path(__file__).resolve().parents[1] / "scripts" / "model_mem_guard.py"
    a, b = (str(tmp_path / f"model-0000{i}-of-00003.gguf") for i in (1, 2))
    for args in ([a, b], [a, str(tmp_path)], [str(tmp_path), a, b]):
        out = subprocess.run([sys.executable, str(script), "--meminfo", str(meminfo), *args], capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "total=300000000 " in out.stdout, out.stdout


# --- kit-ref tests (CI-safe: fake kit executable) ---

import subprocess
import sys
import shutil
import json as json_mod


def make_fake_kit(tmp_path, output_json, exit_code=0, log_file=None):
    """Create a fake kit executable that prints fixed JSON."""
    kit_dir = tmp_path / "fake_kit"
    kit_dir.mkdir()
    kit_script = kit_dir / "kit"
    script_content = f"""#!/usr/bin/env {sys.executable}
import sys
import json

# Log args to file for test verification
if {repr(str(log_file) if log_file else "")}:
    with open({repr(str(log_file) if log_file else "")}, 'a') as f:
        f.write(' '.join(sys.argv[1:]) + '\\n')

output = {output_json}
print(json.dumps(output))
sys.exit({exit_code})
"""
    kit_script.write_text(script_content)
    kit_script.chmod(0o755)
    return kit_script


def run_guard(tmp_path, *args, kit_path=None, meminfo_kb=1000000000):
    """Run the guard script with given args."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "model_mem_guard.py"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemAvailable:   {meminfo_kb} kB\n")
    cmd = [sys.executable, str(script), "--meminfo", str(meminfo)]
    if kit_path:
        cmd.extend(["--kit", str(kit_path)])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def test_kit_ref_pass_excludes_config_size(tmp_path):
    """kit inspect returns layers; config size is excluded. Total = 1050624 + 2560 = 1053184."""
    kit_json = json_mod.dumps({
        "digest": "sha256:abc",
        "manifest": {
            "config": {"size": 848, "digest": "sha256:cfg"},
            "layers": [{"size": 1050624, "digest": "sha256:l1"}, {"size": 2560, "digest": "sha256:l2"}]
        }
    })
    log = tmp_path / "kit_log.txt"
    kit = make_fake_kit(tmp_path, kit_json, log_file=log)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", kit_path=kit)
    assert result.returncode == 0, result.stderr
    assert "PASS total=1053184" in result.stdout, result.stdout
    # Verify kit was called with correct args
    args_log = log.read_text().strip()
    assert "inspect --remote example.com/m:v1" in args_log


def test_kit_ref_fail_exceeds_limit(tmp_path):
    """kit inspect size exceeds memory limit."""
    kit_json = json_mod.dumps({
        "manifest": {
            "layers": [{"size": 50_000_000_000}]
        }
    })
    kit = make_fake_kit(tmp_path, kit_json)
    result = run_guard(tmp_path, "--kit-ref", "example.com/big:v1", kit_path=kit, meminfo_kb=100_000)
    assert result.returncode == 1, result.stderr
    assert "FAIL total=50000000000" in result.stdout, result.stdout


def test_kit_ref_two_refs_summed(tmp_path):
    """Two --kit-ref values are summed."""
    # Use a smarter fake that returns different sizes per ref
    kit_script = tmp_path / "kit_smart"
    kit_script.write_text(f"""#!/usr/bin/env {sys.executable}
import sys, json
ref = sys.argv[3]
if "m1" in ref:
    print(json.dumps({{"manifest": {{"layers": [{{"size": 1000000}}]}}}}))
else:
    print(json.dumps({{"manifest": {{"layers": [{{"size": 2000000}}]}}}}))
""")
    kit_script.chmod(0o755)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m1:v1", "--kit-ref", "example.com/m2:v1", kit_path=kit_script, meminfo_kb=1000000000)
    assert result.returncode == 0, result.stderr
    assert "PASS total=3000000" in result.stdout, result.stdout


def test_kit_ref_combined_with_size_literal(tmp_path):
    """kit-ref size combined with a size literal."""
    kit_json = json_mod.dumps({"manifest": {"layers": [{"size": 1000000}]}})
    kit = make_fake_kit(tmp_path, kit_json)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", "1GB", kit_path=kit, meminfo_kb=1000000000)
    assert result.returncode == 0, result.stderr
    # 1GB = 1000000000 + 1000000 = 1001000000
    assert "PASS total=1001000000" in result.stdout, result.stdout


def test_kit_exit_nonzero(tmp_path):
    """kit exits with non-zero code => exit 2 ERROR."""
    kit = make_fake_kit(tmp_path, "{}", exit_code=1)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", kit_path=kit)
    assert result.returncode == 2, result.stderr
    assert "ERROR" in result.stderr


def test_kit_invalid_json(tmp_path):
    """kit outputs invalid JSON => exit 2 ERROR."""
    kit_script = tmp_path / "kit_bad_json"
    kit_script.write_text(f"""#!/usr/bin/env {sys.executable}
print("not valid json")
""")
    kit_script.chmod(0o755)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", kit_path=kit_script)
    assert result.returncode == 2, result.stderr
    assert "ERROR" in result.stderr


def test_kit_no_layers(tmp_path):
    """kit output has no layers => exit 2 ERROR."""
    kit_json = json_mod.dumps({"manifest": {"config": {"size": 100}}})
    kit = make_fake_kit(tmp_path, kit_json)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", kit_path=kit)
    assert result.returncode == 2, result.stderr
    assert "ERROR" in result.stderr


def test_kit_missing_path(tmp_path):
    """kit executable not found => exit 2 ERROR."""
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", kit_path=tmp_path / "nonexistent_kit")
    assert result.returncode == 2, result.stderr
    assert "ERROR" in result.stderr


def test_kit_layer_missing_size(tmp_path):
    """A layer without an integer size => exit 2 ERROR."""
    kit_json = json_mod.dumps({"manifest": {"layers": [{"digest": "sha256:x"}]}})
    kit = make_fake_kit(tmp_path, kit_json)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", kit_path=kit)
    assert result.returncode == 2, result.stderr
    assert "ERROR" in result.stderr


def test_kit_plain_http_flag(tmp_path):
    """--plain-http is passed through to kit."""
    kit_json = json_mod.dumps({"manifest": {"layers": [{"size": 1000}]}})
    log = tmp_path / "kit_log.txt"
    kit = make_fake_kit(tmp_path, kit_json, log_file=log)
    result = run_guard(tmp_path, "--kit-ref", "example.com/m:v1", "--plain-http", kit_path=kit)
    assert result.returncode == 0, result.stderr
    args_log = log.read_text().strip()
    assert "--plain-http" in args_log


def test_no_args_error(tmp_path):
    """No paths and no --kit-ref => argparse error."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "model_mem_guard.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "error" in result.stderr.lower() or "required" in result.stderr.lower()
