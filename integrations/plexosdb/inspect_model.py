"""Read PLEXOS XML with upstream plexosdb; never solve or edit the input model."""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict

from plexosdb import PlexosDB


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
        Counts obtained from upstream PlexosDB, with a hash of the exact input.
    Raises:
        OSError: The input cannot be read.
        Exception: Upstream rejects the XML or its database schema.
    """
    database = PlexosDB.from_xml(source)
    counts = database.query(
        "SELECT c.name, COUNT(*) FROM t_object o "
        "JOIN t_class c ON c.class_id = o.class_id GROUP BY c.name ORDER BY c.name"
    )
    with source.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
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
