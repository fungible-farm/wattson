"""Apply two reviewable fixes to pinned upstream just-mcp; save the exact diff.

The patches preserve the existing MCP implementation and parameter contract.
They redirect diagnostics to stderr and execute the exact discovered justfile.
"""

from __future__ import annotations

import difflib
from pathlib import Path
import sys


def replace_once(text: str, original: str, replacement: str) -> str:
    """Refuse source drift rather than applying a patch to an unknown upstream shape."""
    if text.count(original) != 1:
        raise ValueError(f"upstream source changed: {original!r}")
    return text.replace(original, replacement, 1)


def patch(root: Path) -> None:
    """Patch the pinned tree and retain a unified diff beside its source provenance."""
    diffs: list[str] = []
    for relative in ("src/main.rs", "src/executor/mod.rs"):
        path = root / relative
        original = path.read_text()
        updated = original
        if relative.endswith("main.rs"):
            if updated.count("fmt::layer()") != 2:
                raise ValueError("expected exactly two tracing formatters")
            updated = updated.replace("fmt::layer()", "fmt::layer().with_writer(std::io::stderr)")
        else:
            updated = replace_once(updated,
                "self.execute_just_command(&task_name, &request.parameters, &context)",
                "self.execute_just_command(&justfile_path, &task_name, &request.parameters, &context)")
            updated = replace_once(updated,
                "async fn execute_just_command(\n        &self,\n        task_name: &str,",
                "async fn execute_just_command(\n        &self,\n        justfile_path: &str,\n        task_name: &str,")
            updated = replace_once(updated,
                '            // The justfile is in the working directory\n            cmd.arg("--justfile");\n            cmd.arg("justfile");',
                '            // Execute the file discovered by the watcher, preserving case.\n            cmd.arg("--justfile");\n            cmd.arg(justfile_path);')
            updated = replace_once(updated,
                'self.parser.parse_file(&PathBuf::from(format!(\n            "{}/justfile",\n            context.working_directory.as_ref().unwrap()\n        )))?',
                'self.parser.parse_file(&PathBuf::from(justfile_path))?')
        path.write_text(updated)
        diffs.extend(difflib.unified_diff(original.splitlines(keepends=True), updated.splitlines(keepends=True),
                                         fromfile=f"a/{relative}", tofile=f"b/{relative}"))
    (root / "wattson-fixes.patch").write_text("".join(diffs))


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
