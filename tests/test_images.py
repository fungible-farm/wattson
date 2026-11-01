"""The single-purpose images ship a few source files; those files must be self-sufficient, and the Containerfiles must say what they ship."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO: Path = Path(__file__).resolve().parents[1]
SRC: Path = REPO / "src" / "wattson"
IMAGES: dict[str, tuple[Path, str]] = {
    "wattson-hf": (REPO / "containers" / "hf" / "Containerfile", "wattson.models"),
    "wattson-csiro": (REPO / "containers" / "csiro" / "Containerfile", "wattson.fetch"),
}


def copied_modules(containerfile: Path) -> list[str]:
    """The ``src/wattson/*.py`` files a Containerfile COPYs, as module names."""
    line = next(line for line in containerfile.read_text().splitlines() if line.startswith("COPY src/wattson/"))
    return [Path(token).stem for token in line.split()[1:-1]]


def wattson_imports(module: str) -> set[str]:
    """Every ``wattson.*`` module a source file imports, at any depth."""
    found: set[str] = set()
    for node in ast.walk(ast.parse((SRC / f"{module}.py").read_text())):
        if isinstance(node, ast.ImportFrom) and node.module == "wattson":
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("wattson."):
            found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            found |= {alias.name.split(".")[1] for alias in node.names if alias.name.startswith("wattson.")}
    return found


@pytest.mark.parametrize("image", IMAGES)
def test_every_wattson_module_an_image_needs_is_one_it_copies(image: str) -> None:
    containerfile, entry = IMAGES[image]
    shipped = set(copied_modules(containerfile))
    needed = {entry.split(".")[1]}
    for module in sorted(shipped):
        needed |= wattson_imports(module)
    assert needed <= shipped | {"__init__"}, f"{image} would fail at run time: needs {sorted(needed - shipped)} which it does not copy"


@pytest.mark.parametrize("image", IMAGES)
def test_the_shipped_files_run_alone_in_a_clean_interpreter(image: str, tmp_path: Path) -> None:
    """Copy only what the Containerfile copies, hide everything else, and run the image's entry point."""
    containerfile, entry = IMAGES[image]
    package = tmp_path / "wattson"
    package.mkdir()
    for module in copied_modules(containerfile):
        shutil.copy(SRC / f"{module}.py", package / f"{module}.py")
    result = subprocess.run([sys.executable, "-I", "-c", f"import {entry}; {entry}.main"], env={"PYTHONPATH": str(tmp_path)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_small_images_do_not_pull_in_the_solver_stack() -> None:
    """The point of the split: neither image needs numpy, pandapower, mcp or anything else the core image carries."""
    heavy = {"numpy", "pandapower", "scipy", "pandas", "mcp", "httpx", "pydantic", "networkx", "yaml"}
    for containerfile, entry in IMAGES.values():
        for module in copied_modules(containerfile):
            tree = ast.parse((SRC / f"{module}.py").read_text())
            top_level = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
            top_level |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
            assert not (top_level & heavy), f"{module}.py imports {top_level & heavy} at module level"


def test_each_small_image_has_one_entry_point_and_runs_as_the_unprivileged_user() -> None:
    for containerfile, entry in IMAGES.values():
        text = containerfile.read_text()
        assert re.search(rf'ENTRYPOINT \["python", "-m", "{re.escape(entry)}"\]', text)
        assert "USER wattson" in text and "useradd" in text
