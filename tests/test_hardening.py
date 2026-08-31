"""Phase 9 hardening: Windows chaos twins, disk-full/clock-jump chaos,
lock contention storm, and the upgrade-path matrix."""
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from scr.atomic import atomic_write_bytes
from scr.capability import CapabilityDenied, CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.locks import LockHeld, WorkspaceLock
from scr.state import Store
from scr.updater import Updater

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------- Windows TerminateProcess chaos twin -----------
@pytest.mark.skipif(os.name != "nt", reason="Windows TerminateProcess twin of the POSIX SIGKILL chaos test")
def test_terminateprocess_chaos_then_recover(tmp_path):
    db = str(tmp_path / "chaos.db").replace("\\", "\\\\")
    marker = str(tmp_path / "began.marker").replace("\\", "\\\\")
    child = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, r"{SRC}")
        from scr.state import Store
        from scr.kernel import Kernel, ToolSpec
        from scr.capability import CapabilityManifest
        from scr.gateway import MockAdapter, ModelResponse, ToolCall
        store = Store(r"{db}")
        sid = store.create_session()
        print(sid, flush=True)
        def slow(a):
            open(r"{marker}", "w").write("began")
            time.sleep(60)
            return "unreachable"
        tool = ToolSpec("deploy", slow, idempotent=False)
        Kernel(store, MockAdapter([
            ModelResponse("", (ToolCall("c1", "deploy", {{"env": "prod"}}),))
        ]), {{"deploy": tool}}, CapabilityManifest(tools=frozenset({{"deploy"}}))).run(sid, "go")
    """)
    proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True)
    sid = proc.stdout.readline().strip()
    deadline = time.time() + 30
    marker_path = str(tmp_path / "began.marker")
    while not os.path.exists(marker_path):
        assert time.time() < deadline and proc.poll() is None
        time.sleep(0.05)
    proc.kill()               # TerminateProcess on Windows
    proc.wait(timeout=30)

    store = Store(str(tmp_path / "chaos.db"))
    assert store.journal_tail(sid)["state"] == "EXEC_INTENT"
    kernel = Kernel(store, MockAdapter([]),
                    {"deploy": ToolSpec("deploy", lambda a: "x", idempotent=False)},
                    CapabilityManifest(tools=frozenset({"deploy"})))
    report = kernel.recover(sid)
    assert report.status == "quarantined"
    assert store.conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"


# --------------------------- Windows junction escape twin ------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows junction twin of the POSIX symlink escape test")
def test_windows_junction_escape_denied(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET")
    link = jail / "link"
    # Directory junction (no admin needed).
    rc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                        capture_output=True)
    if rc.returncode != 0:
        pytest.skip("could not create junction: " + rc.stderr.decode(errors="replace"))
    m = CapabilityManifest(tools=frozenset({"fs_read"}), fs_read_roots=(str(jail),))
    with pytest.raises(CapabilityDenied):
        m.check_read(str(link / "secret.txt"))   # resolves outside the jail → denied


# --------------------------- disk-full chaos -------------------------------
def test_atomic_write_disk_full_leaves_original(tmp_path, monkeypatch):
    target = tmp_path / "important.txt"
    target.write_bytes(b"ORIGINAL")

    real_fsync = os.fsync

    def boom(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(str(target), b"NEW CONTENT")
    monkeypatch.setattr(os, "fsync", real_fsync)

    assert target.read_bytes() == b"ORIGINAL"          # original intact
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".scr-tmp-")]
    assert leftovers == []                              # temp cleaned up


# --------------------------- clock-jump chaos ------------------------------
def test_clock_jump_backward_does_not_break_ledger(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "clk.db"))
    sid = store.create_session()

    # time.time() jumps backwards on every call; ledger/hash chain use no time,
    # and the kernel's wall guard uses time.monotonic, so correctness holds.
    seq = iter([2_000_000_000.0, 1_000_000_000.0, 500_000_000.0, 100.0, 1.0])

    def jumpy():
        try:
            return next(seq)
        except StopIteration:
            return 1.0

    monkeypatch.setattr(time, "time", jumpy)
    tool = ToolSpec("noop", lambda a: "ok", idempotent=True)
    result = Kernel(store, MockAdapter([
        ModelResponse("", (ToolCall("c1", "noop", {}),)),
        ModelResponse("done"),
    ]), {"noop": tool}, CapabilityManifest(tools=frozenset({"noop"}))).run(sid, "go")
    assert result.stopped_reason == "completed"
    assert store.conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    from scr.ledger import Ledger
    assert Ledger(store).verify(sid).ok       # chain valid despite clock chaos


# --------------------------- dual-instance lock storm ----------------------
def test_lock_contention_storm_mutual_exclusion(tmp_path):
    lock_path = str(tmp_path / "ws.lock")
    holders = {"current": 0, "max": 0}
    lock = threading.Lock()
    errors = []

    def worker():
        try:
            wl = WorkspaceLock(lock_path)
            try:
                wl.acquire()
            except LockHeld:
                return                     # contention is expected; not an error
            with lock:
                holders["current"] += 1
                holders["max"] = max(holders["max"], holders["current"])
            time.sleep(0.01)
            with lock:
                holders["current"] -= 1
            wl.release()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    for _ in range(3):                     # repeated rounds widen the race window
        ts = [threading.Thread(target=worker) for _ in range(25)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    assert not errors
    assert holders["max"] <= 1             # never two holders at once


# --------------------------- upgrade-path matrix ---------------------------
def test_upgrade_path_matrix(tmp_path):
    u = Updater(str(tmp_path))
    u.install_initial("1.0.0")
    assert u.apply("2.0.0", lambda s: True).ok
    assert u.active() == "2.0.0"
    # a bad upgrade rolls back to 2.0.0
    res = u.apply("3.0.0", lambda s: False)
    assert not res.ok and res.rolled_back and u.active() == "2.0.0"
    # then a good upgrade proceeds
    assert u.apply("4.0.0", lambda s: True).ok
    assert u.active() == "4.0.0"
