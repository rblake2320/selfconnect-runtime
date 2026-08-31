"""Stale-lock detection: PID + boot-id + heartbeat (§3.5)."""
import json
import os
import time

from scr.locks import WorkspaceLock, boot_id


def test_probe_free_when_no_lock(tmp_path):
    assert WorkspaceLock.probe(str(tmp_path / "ws.lock")).state == "free"


def test_probe_live_while_held(tmp_path):
    path = str(tmp_path / "ws.lock")
    lk = WorkspaceLock(path).acquire()
    try:
        st = WorkspaceLock.probe(path)
        assert st.state == "live"
        assert st.holder["pid"] == os.getpid()
        assert st.holder["boot_id"] == boot_id()
    finally:
        lk.release()


def test_heartbeat_refreshes_timestamp(tmp_path):
    path = str(tmp_path / "ws.lock")
    lk = WorkspaceLock(path).acquire()
    try:
        t0 = json.load(open(path + ".meta"))["heartbeat"]
        time.sleep(0.05)
        lk.heartbeat()
        t1 = json.load(open(path + ".meta"))["heartbeat"]
        assert t1 > t0
    finally:
        lk.release()


def test_hung_holder_detected_as_stale_heartbeat(tmp_path):
    """OS lock held but heartbeat gone stale → classified stale, not live."""
    path = str(tmp_path / "ws.lock")
    lk = WorkspaceLock(path).acquire()
    try:
        # backdate the heartbeat to simulate a hung holder
        meta = json.load(open(path + ".meta"))
        meta["heartbeat"] = time.time() - 999
        with open(path + ".meta", "w") as f:
            json.dump(meta, f)
        st = WorkspaceLock.probe(path, stale_after=30.0)
        assert st.state == "stale_heartbeat"
        assert not (st.state == "live")
        assert st.reclaimable
    finally:
        lk.release()


def test_previous_boot_lock_is_stale_and_reclaimable(tmp_path):
    """A lock file left by a previous boot (OS lock gone) is stale_other_boot,
    and a fresh acquire succeeds — a legitimate restart is not blocked."""
    path = str(tmp_path / "ws.lock")
    # simulate a leftover: lock file + meta from another boot, no live OS lock
    with open(path, "wb") as f:
        f.write(b"99999")
    with open(path + ".meta", "w") as f:
        json.dump({"pid": 99999, "boot_id": "some-old-boot",
                   "host": "old", "heartbeat": time.time() - 5}, f)
    st = WorkspaceLock.probe(path)
    assert st.state == "stale_other_boot"
    assert st.reclaimable
    # a real restart can acquire despite the leftover
    with WorkspaceLock(path):
        pass


def test_break_stale_refuses_live_lock(tmp_path):
    path = str(tmp_path / "ws.lock")
    lk = WorkspaceLock(path).acquire()
    try:
        assert WorkspaceLock.break_stale(path) is False   # won't break a live lock
        assert WorkspaceLock.probe(path).state == "live"
    finally:
        lk.release()


def test_break_stale_clears_dead_lock(tmp_path):
    path = str(tmp_path / "ws.lock")
    with open(path, "wb") as f:
        f.write(b"99999")
    with open(path + ".meta", "w") as f:
        json.dump({"pid": 99999, "boot_id": "old", "host": "old",
                   "heartbeat": 0}, f)
    assert WorkspaceLock.break_stale(path) is True
    assert not os.path.exists(path + ".meta")
