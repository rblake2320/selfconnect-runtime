"""Parallel-safe tool execution (§3.1) — REAL concurrency, no fakes.

Concurrency is proven with real work: multiple `http_get` calls run through real
sandbox worker subprocesses against a real ThreadingHTTPServer with a measured
per-request delay. If the batch ran sequentially the wall time would be N×delay;
we assert it is meaningfully less. Store integrity under concurrency is proven
by reading real files and verifying the hash-chained ledger afterward.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel
from scr.ledger import Ledger
from scr.sandbox import SandboxRunner
from scr.state import Store
from scr.tools_native import build_native_tools

REQUEST_DELAY = 0.5


class _SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(REQUEST_DELAY)          # real, measured latency
        body = f"ok {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def slow_server():
    # ThreadingHTTPServer so concurrent requests are actually served in parallel.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _kernel(tmp_path, script):
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True, exist_ok=True)
    manifest = CapabilityManifest(
        tools=frozenset({"http_get", "fs_read", "fs_write"}),
        fs_read_roots=(str(ws),), fs_write_roots=(str(ws / "out"),),
        net_hosts=frozenset({"127.0.0.1"}))
    tools = build_native_tools(manifest, SandboxRunner())
    return Kernel(Store(str(tmp_path / "p.db")), MockAdapter(script), tools, manifest), ws


def test_parallel_http_beats_sequential(tmp_path, slow_server):
    n = 4
    calls = tuple(ToolCall(f"c{i}", "http_get",
                           {"url": f"http://127.0.0.1:{slow_server}/r{i}"}) for i in range(n))
    kernel, _ = _kernel(tmp_path, [ModelResponse("", calls), ModelResponse("done")])
    sid = kernel.store.create_session()

    t0 = time.monotonic()
    result = kernel.run(sid, "fetch all")
    elapsed = time.monotonic() - t0

    assert result.stopped_reason == "completed"
    sequential = n * REQUEST_DELAY                 # 2.0s if run one-at-a-time
    assert elapsed < sequential * 0.7, f"no real parallelism: {elapsed:.2f}s vs seq {sequential:.2f}s"

    # every real response was folded, in order, and the ledger still verifies
    tool_msgs = [m for m in kernel.store.get_messages(sid) if m["role"] == "tool"]
    assert len(tool_msgs) == n
    for i, m in enumerate(tool_msgs):
        assert f"/r{i}" in m["content"] and "ok" in m["content"]
    assert Ledger(kernel.store).verify(sid).ok


def test_concurrent_reads_no_store_corruption(tmp_path):
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True)
    for i in range(6):
        (ws / f"f{i}.txt").write_text(f"content-{i}")
    calls = tuple(ToolCall(f"c{i}", "fs_read", {"path": str(ws / f"f{i}.txt")})
                  for i in range(6))
    kernel, _ = _kernel(tmp_path, [ModelResponse("", calls), ModelResponse("done")])
    sid = kernel.store.create_session()
    kernel.run(sid, "read all")

    tool_msgs = [m for m in kernel.store.get_messages(sid) if m["role"] == "tool"]
    assert len(tool_msgs) == 6
    for i, m in enumerate(tool_msgs):
        assert f"content-{i}" in m["content"]      # each real file read correctly, in order
    v = Ledger(kernel.store).verify(sid)
    assert v.ok                                     # hash chain intact under concurrency
    # single-dangling-intent recovery model preserved: every EXEC_INTENT has a DONE
    states = [e["state"] for e in kernel.store.journal_all(sid)]
    assert states.count("EXEC_INTENT") == states.count("EXEC_DONE") == 6


def test_non_parallel_safe_tool_breaks_the_batch(tmp_path):
    """A batch of two reads runs concurrently; a following fs_write (NOT
    parallel_safe) runs sequentially — all execute correctly, ledger intact."""
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True)
    (ws / "a.txt").write_text("aaa")
    (ws / "b.txt").write_text("bbb")
    calls = (
        ToolCall("c1", "fs_read", {"path": str(ws / "a.txt")}),
        ToolCall("c2", "fs_read", {"path": str(ws / "b.txt")}),
        ToolCall("c3", "fs_write", {"path": str(ws / "out" / "o.txt"), "content": "written"}),
    )
    kernel, _ = _kernel(tmp_path, [ModelResponse("", calls), ModelResponse("done")])
    sid = kernel.store.create_session()
    kernel.run(sid, "mixed batch")

    assert (ws / "out" / "o.txt").read_text() == "written"   # real write happened
    import json
    events = [json.loads(r["event"]) for r in kernel.store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,)).fetchall()]
    execs = [e for e in events if e.get("type") == "tool_exec"]
    parallel_flags = {e["tool"]: e.get("parallel", False) for e in execs}
    assert parallel_flags["fs_read"] is True        # reads went through the parallel path
    assert parallel_flags["fs_write"] is False      # write stayed sequential
    assert Ledger(kernel.store).verify(sid).ok


def test_single_parallel_safe_call_not_batched(tmp_path):
    """A lone parallel-safe call must still go through the normal sequential
    path (a batch needs >=2)."""
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True)
    (ws / "a.txt").write_text("solo")
    kernel, _ = _kernel(tmp_path, [
        ModelResponse("", (ToolCall("c1", "fs_read", {"path": str(ws / "a.txt")}),)),
        ModelResponse("done")])
    sid = kernel.store.create_session()
    kernel.run(sid, "one read")
    import json
    events = [json.loads(r["event"]) for r in kernel.store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,)).fetchall()]
    ex = [e for e in events if e.get("type") == "tool_exec"][0]
    assert ex.get("parallel", False) is False       # not batched
