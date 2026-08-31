"""SCR service host (scr-service) console mode: real uvicorn serve + graceful
shutdown that releases the workspace lock. Non-elevated; the SCM path is
exercised by the elevated install (docs/CLEAN_BOX_TEST.md)."""
import os
import socket
import time

import pytest


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_console_serve_and_graceful_shutdown_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("SCR_HOME", str(tmp_path / "home"))
    # initialize the home (config + db) the way `scr init` would
    from scr.config import Config
    from scr.state import Store
    cfg = Config()
    Store(os.path.join(cfg.home, "scr.db")).close()

    from scr.locks import WorkspaceLock
    from scr.service_main import _Runner
    port = _free_port()
    runner = _Runner("127.0.0.1", port)
    t = runner.serve_in_thread()

    # wait for the real server to accept connections
    import httpx
    up = False
    for _ in range(100):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/jobs", timeout=1)
            up = True
            break
        except Exception:
            time.sleep(0.05)
    assert up, "service did not start"
    assert r.status_code == 401                # served, auth required (real route)

    # while running, the service lock is held → a second acquire fails
    with pytest.raises(Exception):
        WorkspaceLock(os.path.join(cfg.home, "service.lock")).acquire()

    runner.stop()                              # graceful shutdown
    t.join(timeout=15)
    assert not t.is_alive(), "service did not stop"

    # graceful shutdown released the workspace lock → re-acquirable
    lk = WorkspaceLock(os.path.join(cfg.home, "service.lock"))
    lk.acquire()
    lk.release()


def test_bind_guard_refuses_non_loopback_without_tls(tmp_path, monkeypatch):
    monkeypatch.setenv("SCR_HOME", str(tmp_path / "home"))
    from scr.service import BindRefused, check_bind
    with pytest.raises(BindRefused):
        check_bind("0.0.0.0", tls=False, auth=True)
    check_bind("127.0.0.1", tls=False, auth=True)   # loopback ok
