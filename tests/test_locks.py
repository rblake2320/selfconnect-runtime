import subprocess
import sys
import textwrap

import pytest

from scr.locks import LockHeld, WorkspaceLock


def test_acquire_and_release(tmp_path):
    lock = WorkspaceLock(str(tmp_path / "ws.lock"))
    lock.acquire()
    lock.release()
    # Re-acquirable after release
    with WorkspaceLock(str(tmp_path / "ws.lock")):
        pass


def test_second_holder_blocked_same_process(tmp_path):
    path = str(tmp_path / "ws.lock")
    first = WorkspaceLock(path).acquire()
    try:
        with pytest.raises(LockHeld):
            WorkspaceLock(path).acquire()
    finally:
        first.release()


def test_second_holder_blocked_across_processes(tmp_path):
    """True cross-process contention: a child process must fail to acquire
    while the parent holds the lock, and succeed after release."""
    path = str(tmp_path / "ws.lock")
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(tmp_path.parent)!r})
        sys.path.insert(0, {sys.path[0]!r})
        from scr.locks import WorkspaceLock, LockHeld
        try:
            WorkspaceLock({path!r}).acquire()
            print("ACQUIRED")
        except LockHeld:
            print("BLOCKED")
        """
    )
    holder = WorkspaceLock(path).acquire()
    try:
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            cwd=str(tmp_path), timeout=30,
        )
        assert "BLOCKED" in out.stdout, out.stdout + out.stderr
    finally:
        holder.release()

    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        cwd=str(tmp_path), timeout=30,
    )
    assert "ACQUIRED" in out.stdout, out.stdout + out.stderr


def test_lock_released_on_process_death(tmp_path):
    """OS releases the lock when the holder dies — no permanent stale lock."""
    path = str(tmp_path / "ws.lock")
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {sys.path[0]!r})
        from scr.locks import WorkspaceLock
        WorkspaceLock({path!r}).acquire()
        print("HELD", flush=True)
        import time; time.sleep(60)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "HELD"
        proc.kill()
        proc.wait(timeout=30)
        # After holder death, lock must be acquirable.
        with WorkspaceLock(path):
            pass
    finally:
        if proc.poll() is None:
            proc.kill()
