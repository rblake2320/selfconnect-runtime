"""Windows named-pipe transport (§3.7). Real pipe round-trips, no fakes."""
import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt",
                                reason="named-pipe transport is Windows-only")

from scr.pipe_transport import NamedPipeServer, pipe_client_request  # noqa: E402


def _uniq(n):
    return f"scr-test-{n}-{os.getpid()}-{time.time_ns()}"


def test_request_response_roundtrip():
    name = _uniq("echo")

    def handler(req):
        return {"ok": True, "echo": req.get("msg")}

    srv = NamedPipeServer(name, handler)
    t = threading.Thread(target=srv.serve_one, daemon=True)
    t.start()
    time.sleep(0.1)                              # let the server create the pipe
    resp = pipe_client_request(name, {"msg": "hello over pipe"})
    assert resp == {"ok": True, "echo": "hello over pipe"}
    t.join(timeout=5)


def test_handler_dispatch_returns_runtime_version():
    from scr import __version__
    name = _uniq("ver")

    def handler(req):
        if req.get("op") == "version":
            return {"version": __version__}
        return {"error": "unknown op"}

    srv = NamedPipeServer(name, handler)
    t = threading.Thread(target=srv.serve_one, daemon=True)
    t.start()
    time.sleep(0.1)
    resp = pipe_client_request(name, {"op": "version"})
    assert resp["version"] == __version__
    t.join(timeout=5)


def test_handler_exception_returns_structured_error():
    name = _uniq("boom")

    def handler(req):
        raise ValueError("bad request")

    srv = NamedPipeServer(name, handler)
    t = threading.Thread(target=srv.serve_one, daemon=True)
    t.start()
    time.sleep(0.1)
    resp = pipe_client_request(name, {"x": 1})
    assert resp["ok"] is False and resp["error"] == "ValueError"
    t.join(timeout=5)


def test_serve_forever_handles_multiple_requests():
    name = _uniq("multi")
    count = {"n": 0}

    def handler(req):
        count["n"] += 1
        return {"n": count["n"]}

    srv = NamedPipeServer(name, handler).start()
    try:
        time.sleep(0.1)
        r1 = pipe_client_request(name, {})
        r2 = pipe_client_request(name, {})
        assert r1["n"] == 1 and r2["n"] == 2
    finally:
        srv.stop()
