"""The trainer reads the gzipped trajectories that data/ ships and verifies them against the manifest recorded for the plain file."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from wattson.training import agent_sft

DATA: Path = Path(__file__).resolve().parents[2] / "data" / "trajectories"
SETS: list[str] = sorted(p.name for p in DATA.iterdir() if p.is_dir())


def test_gzip_and_plain_forms_give_the_same_bytes(tmp_path: Path) -> None:
    payload = b'{"a": 1}\n{"b": 2}\n'
    plain = tmp_path / "t.jsonl"
    plain.write_bytes(payload)
    packed = tmp_path / "t.jsonl.gz"
    packed.write_bytes(gzip.compress(payload))
    assert agent_sft.read_trajectory_bytes(plain) == agent_sft.read_trajectory_bytes(packed) == payload


@pytest.mark.parametrize("name", SETS)
def test_every_shipped_trajectory_set_matches_its_manifest_hash(name: str) -> None:
    """The check the trainer makes before it trains: the decompressed bytes hash to the manifest's value, and no benchmark rows are in it."""
    manifest = json.loads((DATA / name / "manifest.json").read_text())
    raw = agent_sft.read_trajectory_bytes(DATA / name / "trajectories.jsonl.gz")
    assert hashlib.sha256(raw).hexdigest() == manifest["trajectories_sha256"]
    assert manifest["benchmark_access"] is False
    assert sum(1 for line in raw.decode().splitlines() if line.strip()) == manifest["kept"]
