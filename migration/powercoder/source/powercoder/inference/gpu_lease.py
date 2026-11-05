"""GPU resource lease manager for the NEM PowerCoder mission.

Prevents overlapping memory-intensive training and teacher inference by
acquiring exclusive leases on GPU resources.
"""

import fcntl
import os
import time
from pathlib import Path
from typing import Optional


class GPULease:
    """Manages exclusive GPU leases using file locking."""

    def __init__(self, lock_dir: str = "/tmp/powercoder-gpu"):
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock_file: Optional[Path] = None
        self._lock_fd: Optional[int] = None

    def acquire(self, purpose: str, timeout: float = 60.0) -> bool:
        """Acquire an exclusive GPU lease.

        Args:
            purpose: Description of the operation (e.g., "teacher-inference", "training").
            timeout: Maximum seconds to wait for the lease.

        Returns:
            True if lease acquired, False if timeout.
        """
        self._lock_file = self.lock_dir / f"{purpose}.lock"
        start = time.time()

        while True:
            try:
                self._lock_fd = os.open(str(self._lock_file), os.O_CREAT | os.O_RDWR)
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Write lease info
                os.write(self._lock_fd, f"{purpose}:{time.time()}:{os.getpid()}".encode())
                return True
            except (OSError, IOError):
                if self._lock_fd is not None:
                    os.close(self._lock_fd)
                    self._lock_fd = None
                if time.time() - start > timeout:
                    return False
                time.sleep(0.5)

    def release(self):
        """Release the GPU lease."""
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None
        if self._lock_file is not None and self._lock_file.exists():
            self._lock_file.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()
