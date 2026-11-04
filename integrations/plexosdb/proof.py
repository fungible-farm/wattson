"""Offline upstream fixture proof: actual import, name fidelity, export and RO mount."""

import errno
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plexosdb import ClassEnum, PlexosDB

import inspect_model as model_reader
from inspect_model import Summary, inspect_model

# Paths and expectations are the committed fixture's manifest, not invented models.
FIXTURE: Path = Path("/input/model.xml")
MANIFEST: Path = Path("/app/fixtures/manifest.json")


def check_source_edit_race(expected_summary: Summary) -> None:
    """Reproduce a host edit after import and require the original bytes' hash.

    The real upstream import runs before the source changes. Patching only the
    import boundary makes this deterministic; neither its database nor query
    results are mocked. Before snapshotting, this returned old counts/new hash.
    """
    actual_import = PlexosDB.from_xml
    with TemporaryDirectory() as temporary:
        source = Path(temporary) / "changing.xml"
        source.write_bytes(FIXTURE.read_bytes())
        changed = b'<MasterDataSet xmlns="http://tempuri.org/MasterDataSet.xsd"/>'

        def import_then_edit(snapshot: Path) -> PlexosDB:
            """Edit the caller's source after the real parser has consumed it."""
            database = actual_import(snapshot)
            source.write_bytes(changed)
            return database

        with patch.object(model_reader.PlexosDB, "from_xml", side_effect=import_then_edit):
            result = inspect_model(source)
        assert source.read_bytes() == changed, "source mutation did not run"
        assert result == expected_summary, result


def main() -> None:
    """Require known results and kernel-enforced read-only input; fail on mismatch."""
    expected = json.loads(MANIFEST.read_text())
    before = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert before == expected["sha256"], "fixture checksum mismatch"
    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    assert any(row[4] == "/input" and "ro" in row[5].split(",") for row in mounts), "input mount is not read-only"
    descriptor: int | None = None
    try:
        # No O_TRUNC and no write: even a wrongly mounted input is never changed.
        descriptor = os.open(FIXTURE, os.O_WRONLY)
    except OSError as error:
        assert error.errno in {errno.EROFS, errno.EACCES}, f"write descriptor unexpectedly failed: {error}"
    else:
        raise AssertionError("input mount allowed a write descriptor")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    result = inspect_model(FIXTURE)
    assert result == expected["summary"], result
    database = PlexosDB.from_xml(FIXTURE)
    names = sorted(database.list_objects_by_class(ClassEnum.Generator))
    assert names == expected["generator_names"], names
    # Export is explicit and isolated in tmpfs; no client XML is overwritten.
    with TemporaryDirectory() as temporary:
        exported = Path(temporary) / "roundtrip.xml"
        database.to_xml(exported)
        reloaded = PlexosDB.from_xml(exported)
        assert sorted(reloaded.list_objects_by_class(ClassEnum.Generator)) == names
        assert reloaded.query("SELECT COUNT(*) FROM t_object")[0][0] == result["objects"]
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == before
    check_source_edit_race(expected["summary"])
    print(json.dumps({"source_edit_race": "PASS", "proof": "PASS", "read_only_input": True, "roundtrip": True, **result}, sort_keys=True))


if __name__ == "__main__":
    main()
