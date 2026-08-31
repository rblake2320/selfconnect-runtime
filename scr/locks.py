"""Cross-process single-writer workspace lock.

Windows: msvcrt.locking on a lock file (exclusive byte-range lock).
POSIX:   fcntl.flock LOCK_EX | LOCK_NB.

The holder writes its PID into the file for diagnostics. Locks are
released by the OS on process death, so a crashed holder never leaves
a permanently stuck workspace (no PID-file staleness heuristics needed
for correctness; PID is informational).
"""
from __future__ import annotations

import os


class LockHeld(Exception):
    """Another process (or handle) holds the workspace lock."""


class WorkspaceLock:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._fh = None

    def acquire(self) -> "WorkspaceLock":
        fh = open(self.lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            fh.close()
            raise LockHeld(f"workspace lock held: {self.lock_path}") from e
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
