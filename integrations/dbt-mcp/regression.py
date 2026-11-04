"""Ensure a failed actual MCP build cannot reuse an earlier successful smoke result."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

OUTPUT: Path = Path("/output")


def main() -> None:
    """Run a real positive baseline, then fail only build and require no success proof."""
    (OUTPUT / "mcp-regression.json").unlink(missing_ok=True)
    command = [sys.executable, "/opt/dbt/smoke.py"]
    baseline = subprocess.run(command, capture_output=True, text=True, check=False)
    (OUTPUT / "regression-baseline.log").write_text(baseline.stdout + baseline.stderr)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert (OUTPUT / "mcp-proof.json").exists()
    with TemporaryDirectory() as temporary:
        wrapper = Path(temporary) / "dbt"
        wrapper.write_text(
            '#!/bin/sh\n'
            'for argument in "$@"; do\n'
            '  if [ "$argument" = build ]; then\n'
            '    echo "Injected build failure" >&2\n'
            '    exit 42\n'
            '  fi\n'
            'done\n'
            'exec /opt/dbt/.venv/bin/dbt "$@"\n'
        )
        wrapper.chmod(0o755)
        environment = dict(os.environ, DBT_PATH=str(wrapper))
        failed = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
    (OUTPUT / "regression-fault.log").write_text(failed.stdout + failed.stderr)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert not (OUTPUT / "mcp-proof.json").exists(), "failed build left a success proof"
    # The deliberate CLI failure must have reached build after real parse/list/compile.
    assert "Tool compile called successfully" in failed.stderr, failed.stderr
    assert "Calling tool: build" in failed.stderr, failed.stderr
    report = {"regression": "PASS", "baseline_exit": baseline.returncode,
              "failed_build_exit": failed.returncode, "success_proof_after_failure": False}
    (OUTPUT / "mcp-regression.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
