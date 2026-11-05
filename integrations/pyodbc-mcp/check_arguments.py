"""Regression: hostile recipe arguments must remain data before any container starts."""
import os
from pathlib import Path
import subprocess
import tempfile


def main() -> None:
    """Pass literal apostrophes/command substitutions through the real just recipe."""
    justfile = Path(__file__).with_name("justfile").resolve()
    environment = dict(os.environ)
    # Fail before Podman for the DSN case; no database prerequisite is acknowledged.
    environment.pop("WATTSON_DATABASE_READONLY_CONFIRMED", None)
    with tempfile.TemporaryDirectory(prefix="pyodbc-argument-check-") as scratch:
        directory = Path(scratch)
        config = directory / "private"
        config.mkdir()
        (config / "odbc.ini").write_text("[fixture]\nDriver=SQLite3\n")
        for target in ("config", "dsn"):
            marker = directory / f"{target}-executed"
            payload = f"missing' $(touch {marker}) '"
            arguments = [str(config), "fixture"]
            arguments[0 if target == "config" else 1] = payload
            result = subprocess.run(
                ["just", "--justfile", str(justfile), "run", *arguments],
                env=environment, capture_output=True, text=True, check=False,
            )
            assert result.returncode != 0, result
            assert not marker.exists(), f"{target} argument executed a host command"
    print("PASS: config and DSN apostrophes/command substitutions remain inert data")


if __name__ == "__main__":
    main()
