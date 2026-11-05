"""Exclusive GPU lease: one engine at a time across all purposes (Studio D, #29).

The lock is ONE file (``gpu.lock``) in the lock directory, so a second exclusive engine is refused whatever
its purpose is called. (An earlier version locked ``<purpose>.lock``, so two different purposes both
succeeded and nothing was excluded.) The holder's purpose, start time and pid are written into the file for
the refusal message. The file is never unlinked: unlinking a lock file lets a third process lock a new inode
while the old one is still held.
"""

import fcntl
import os
import time
from pathlib import Path
from typing import Optional

LOCK_NAME = "gpu.lock"


class GPULease:
    """Manages the exclusive GPU lease using flock on a single file."""

    def __init__(self, lock_dir: str = "/tmp/powercoder-gpu"):
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.lock_dir / LOCK_NAME
        self._lock_fd: Optional[int] = None

    def holder(self) -> str:
        """Return the recorded holder (``purpose:start:pid``) or an empty string."""
        try:
            return self.lock_file.read_text().strip()
        except OSError:
            return ""

    def acquire(self, purpose: str, timeout: float = 60.0) -> bool:
        """Acquire the exclusive lease. True if acquired, False if another engine still holds it after ``timeout``."""
        if self._lock_fd is not None:
            return True
        start = time.time()
        while True:
            fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                if time.time() - start >= timeout:
                    return False
                time.sleep(min(0.5, max(0.0, timeout - (time.time() - start))))
                continue
            os.ftruncate(fd, 0)
            os.write(fd, f"{purpose}:{time.time()}:{os.getpid()}".encode())
            self._lock_fd = fd
            return True

    def release(self):
        """Release the lease (the lock file stays; only the flock is dropped)."""
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()
