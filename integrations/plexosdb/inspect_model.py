"""Read PLEXOS XML with upstream plexosdb; never solve or edit the input model."""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict

from plexosdb import PlexosDB

# Private parser snapshot: owner may read, and no identity receives write mode bits.
SNAPSHOT_MODE: int = 0o400


class Summary(TypedDict):
    """Deterministic inventory from an actual upstream XML import."""

    plexosdb_version: str
    input_sha256: str
    objects: int
    classes: dict[str, int]


def inspect_model(source: Path) -> Summary:
    """Import XML into in-memory SQLite and count objects by their source class.

    Args:
        source: Readable XML file; related CSV data are not opened or evaluated.
    Returns:
        Counts and hash from the same private snapshot. Host edits after the
        snapshot is read cannot pair old database results with a new file hash.
    Raises:
        OSError: The input cannot be read.
        Exception: Upstream rejects the XML or its database schema.
    """
    # Immutable bytes bind the digest and parser input, even if the host edits
    # the source behind its read-only container mount during import.
    data = source.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    with TemporaryDirectory(prefix="plexosdb-") as temporary:
        snapshot = Path(temporary) / "model.xml"
        snapshot.write_bytes(data)
        snapshot.chmod(SNAPSHOT_MODE)
        database = PlexosDB.from_xml(snapshot)
    counts = database.query(
        "SELECT c.name, COUNT(*) FROM t_object o "
        "JOIN t_class c ON c.class_id = o.class_id GROUP BY c.name ORDER BY c.name"
    )
    return {
        "plexosdb_version": version("plexosdb"),
        "input_sha256": checksum,
        "objects": database.query("SELECT COUNT(*) FROM t_object")[0][0],
        "classes": dict(counts),
    }


def main() -> None:
    """Print a model inventory to stdout; callers choose whether to save it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=Path("/input/model.xml"))
    args = parser.parse_args()
    print(json.dumps(inspect_model(args.source), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
