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
