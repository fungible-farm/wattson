"""Bind workbook identity to private bytes for an entire upstream MCP session."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from excel_mcp.config import Limits

# Same compressed-file cap as the pinned upstream server, enforced before copying.
MAX_SOURCE_BYTES: int = Limits().max_file_bytes
# Temp directories are private (0700); workbook bytes and manifest need no writes after capture.
CAPTURE_MODE: int = 0o400
DEFAULT_MANIFEST: Path = Path('/tmp/excel-capture.json')


@dataclass(frozen=True)
class CapturedWorkbook:
    """One private parser input and the digest of exactly those bytes."""
    path: Path
    sha256: str
    size: int


@contextmanager
def capture_workbook(source: Path) -> Iterator[CapturedWorkbook]:
    """Read source once and retain a private immutable parser copy until session exit.

    Host edits can affect the initial read, but cannot change the captured bytes or pair a
    later source digest with earlier parsed data. No source file is opened for writing.
    """
    with source.open('rb') as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError(f'Workbook exceeds upstream compressed-file limit {MAX_SOURCE_BYTES}')
    with TemporaryDirectory(prefix='excel-capture-', dir='/tmp') as directory:
        target = Path(directory) / source.name
        target.write_bytes(data)
        target.chmod(CAPTURE_MODE)
        captured = CapturedWorkbook(target, hashlib.sha256(data).hexdigest(), len(data))
        yield captured


def write_manifest(captured: CapturedWorkbook) -> None:
    """Record capture identity on a private side channel; stdout stays exclusively MCP."""
    destination = Path(os.environ.get('EXCEL_MCP_CAPTURE_MANIFEST', str(DEFAULT_MANIFEST)))
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, CAPTURE_MODE)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump({'snapshot_sha256': captured.sha256, 'snapshot_bytes': captured.size,
                   'snapshot_path': str(captured.path), 'snapshot_mode': oct(CAPTURE_MODE)}, stream)
        stream.write('\n')
