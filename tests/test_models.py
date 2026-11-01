"""Model verification: a published repo's files must match its hash_manifest.json, and the check names what is wrong."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wattson import models


def _repo(root: Path, files: dict[str, bytes], prefix: str = "") -> None:
    manifest = {}
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
        manifest[prefix + name] = hashlib.sha256(data).hexdigest()
    (root / models.MANIFEST).write_text(json.dumps(manifest))


def test_a_matching_repo_verifies(tmp_path: Path) -> None:
    _repo(tmp_path, {"model.safetensors": b"weights", "config.json": b"{}"}, prefix=models.STRIPPED_PREFIX)
    assert models.verify(tmp_path) == (2, [])


def test_a_changed_and_a_missing_file_are_named(tmp_path: Path) -> None:
    _repo(tmp_path, {"model.safetensors": b"weights", "tokenizer.json": b"{}"})
    (tmp_path / "model.safetensors").write_bytes(b"tampered")
    (tmp_path / "tokenizer.json").unlink()
    checked, problems = models.verify(tmp_path)
    assert sorted(problems) == ["changed: model.safetensors", "missing: tokenizer.json"] and checked == 1


def test_the_model_card_is_not_hashed(tmp_path: Path) -> None:
    _repo(tmp_path, {"README.md": b"original card", "model.safetensors": b"w"})
    (tmp_path / "README.md").write_bytes(b"the published card replaced it")
    assert models.verify(tmp_path)[1] == []


def test_a_directory_without_a_manifest_is_a_problem(tmp_path: Path) -> None:
    assert models.verify(tmp_path)[1] == [f"no {models.MANIFEST} in {tmp_path}"]


def test_an_already_pulled_model_is_not_downloaded_again(tmp_path: Path, monkeypatch) -> None:
    _repo(tmp_path, {"model.safetensors": b"w"})
    (tmp_path / models.MARKER).write_text("{}")
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", None)  # an import here would raise: no download is attempted
    result = models.pull("org/name", tmp_path)
    assert result["downloaded"] is False and result["files_verified"] == 1
