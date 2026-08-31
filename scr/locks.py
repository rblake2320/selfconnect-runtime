"""Cross-process single-writer workspace lock with stale-lock detection.

Two layers:
  1. An OS advisory lock (msvcrt / flock) on the `.lock` file — the authority
     for live single-writer exclusion; the OS releases it when the holder dies.
  2. A metadata sidecar (`.lock.meta`, never OS-locked) recording PID, boot-id,
     host and a heartbeat timestamp. This is what makes a legitimate restart
     after a crash safe on paths where the OS lock alone is ambiguous:
       * a lock left by a PREVIOUS boot (boot-id mismatch, PID possibly reused)
         is classified stale and reclaimable;
       * a HUNG-but-alive holder (OS lock held, heartbeat gone stale) is
         detected as stale rather than mistaken for a healthy holder.

The holder calls `heartbeat()` periodically; a supervisor uses `probe()` to
classify the lock and `break_stale()` to reclaim a genuinely dead/hung one.
"""
from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Optional


class LockHeld(Exception):
    """Another process (or handle) holds the workspace lock."""


def boot_id() -> str:
    """A per-boot identifier, stable across calls within one boot."""
    if os.name != "nt":
        try:
            with open("/proc/sys/kernel/random/boot_id", "r") as f:
                return f.read().strip()
        except OSError:
            return "posix-unknown"
    import ctypes
    # GetTickCount64 = ms since boot; now - uptime = boot epoch (constant).
    # NB: restype MUST be c_uint64 — ctypes defaults to c_int (32-bit), which
    # truncates the 64-bit tick count (wrong past ~49 days uptime).
    fn = ctypes.windll.kernel32.GetTickCount64
    fn.restype = ctypes.c_uint64
    tick_ms = fn()
    return f"win-{int(time.time()) - int(tick_ms // 1000)}"


def _os_lock(fh, blocking: bool = False) -> bool:
    """Try to take the OS advisory lock on an open file handle. Returns True on
    success. Non-blocking by default."""
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fh.fileno(), mode, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fh.fileno(), flags)
            return True
        except OSError:
            return False


def _os_unlock(fh) -> None:
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


@dataclass
class LockStatus:
    state: str                         # free | live | stale_other_boot | stale_heartbeat
    holder: Optional[dict] = None      # {pid, boot_id, host, heartbeat}

    @property
    def reclaimable(self) -> bool:
        return self.state in ("free", "stale_other_boot", "stale_heartbeat")


class WorkspaceLock:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.meta_path = lock_path + ".meta"
        self._fh = None

    # ------------------------------------------------------------- acquire
    def acquire(self) -> "WorkspaceLock":
        fh = open(self.lock_path, "a+b")
        if not _os_lock(fh, blocking=False):
            fh.close()
            raise LockHeld(f"workspace lock held: {self.lock_path}")
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        self._fh = fh
        self._write_meta()
        return self

    def _write_meta(self) -> None:
        from .atomic import atomic_write_text
        atomic_write_text(self.meta_path, json.dumps({
            "pid": os.getpid(), "boot_id": boot_id(),
            "host": socket.gethostname(), "heartbeat": time.time(),
        }))

    def heartbeat(self) -> None:
        """Refresh the liveness timestamp. Call periodically while holding."""
        if self._fh is not None:
            self._write_meta()

    def release(self) -> None:
        if self._fh is None:
            return
        _os_unlock(self._fh)
        self._fh.close()
        self._fh = None
        try:
            os.unlink(self.meta_path)
        except OSError:
            pass

    # --------------------------------------------------------------- probe
    @classmethod
    def probe(cls, lock_path: str, stale_after: float = 30.0) -> LockStatus:
        """Classify a lock WITHOUT acquiring it. Safe for a supervisor to call."""
        meta_path = lock_path + ".meta"
        meta = None
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            meta = None

        if not os.path.exists(lock_path):
            return LockStatus("free", meta)

        # Determine whether the OS lock is currently held by probing it.
        held = False
        try:
            fh = open(lock_path, "a+b")
            if _os_lock(fh, blocking=False):
                _os_unlock(fh)          # we only probed; don't keep it
                held = False
            else:
                held = True
            fh.close()
        except OSError:
            held = True                 # can't open ⇒ assume contended

        if not held:
            # OS lock is free → any holder is gone. A leftover from a previous
            # boot is explicitly stale; otherwise it's simply reclaimable.
            if meta and meta.get("boot_id") != boot_id():
                return LockStatus("stale_other_boot", meta)
            return LockStatus("free", meta)

        # OS lock held → a live process has it. Stale only if it stopped
        # heartbeating (hung).
        if meta and (time.time() - float(meta.get("heartbeat", 0)) > stale_after):
            return LockStatus("stale_heartbeat", meta)
        return LockStatus("live", meta)

    @classmethod
    def break_stale(cls, lock_path: str, stale_after: float = 30.0) -> bool:
        """Reclaim a genuinely stale lock so a restart can proceed. Refuses to
        break a live lock. Returns True if a stale lock was cleared."""
        status = cls.probe(lock_path, stale_after)
        if status.state == "live":
            return False
        for p in (lock_path + ".meta",):
            try:
                os.unlink(p)
            except OSError:
                pass
        # The .lock file itself: only remove if the OS lock is not held.
        if status.state != "live":
            try:
                fh = open(lock_path, "a+b")
                if _os_lock(fh, blocking=False):
                    _os_unlock(fh)
                    fh.close()
                    try:
                        os.unlink(lock_path)
                    except OSError:
                        pass
                else:
                    fh.close()
            except OSError:
                pass
        return True

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
