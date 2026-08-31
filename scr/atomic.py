"""Crash-safe atomic file writes.

Protocol: write to a temp file in the SAME directory (same filesystem),
flush + fsync the file, then os.replace() (atomic on both NTFS and POSIX),
then fsync the directory on POSIX so the rename itself is durable.

A crash at any point leaves either the old complete file or the new
complete file — never a torn write.
"""
from __future__ import annotations

import os
import tempfile


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Atomically replace `path` with `data`. Durable on success."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".scr-tmp-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic rename, both Windows and POSIX
        if os.name != "nt":
            # Durably record the rename in the directory metadata (POSIX).
            dfd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    except BaseException:
        # Best-effort cleanup; original file untouched.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Text convenience wrapper. Content written as-is (no newline
    translation) so artifacts are CRLF-safe across platforms."""
    atomic_write_bytes(path, text.encode(encoding))
