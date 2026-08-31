"""Chaos: SIGKILL a real child process mid-run (not an in-process exception),
then recover against the surviving SQLite WAL DB from a fresh process.

This is the closest a test gets to pulling the power cord.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter
from scr.kernel import Kernel, ToolSpec
from scr.state import Store

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL semantics; Windows CI uses TerminateProcess variant")
def test_sigkill_during_nonidempotent_exec_then_recover(tmp_path):
    db = str(tmp_path / "chaos.db")
    marker = str(tmp_path / "tool-started.marker")

    child_src = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {SRC!r})
        from scr.state import Store
        from scr.kernel import Kernel, ToolSpec
        from scr.capability import CapabilityManifest
        from scr.gateway import MockAdapter, ModelResponse, ToolCall

        store = Store({db!r})
        sid = store.create_session()
        print(sid, flush=True)

        def slow_side_effect(args):
            open({marker!r}, "w").write("side effect began")
            time.sleep(60)   # parent SIGKILLs us here
            return "unreachable"

        tool = ToolSpec("deploy", slow_side_effect, idempotent=False)
        kernel = Kernel(store, MockAdapter([
            ModelResponse("", (ToolCall("c1", "deploy", {{"env": "prod"}}),))
        ]), {{"deploy": tool}}, CapabilityManifest(tools=frozenset({{"deploy"}})))
        kernel.run(sid, "deploy to prod")
    """)

    proc = subprocess.Popen([sys.executable, "-c", child_src],
                            stdout=subprocess.PIPE, text=True)
    sid = proc.stdout.readline().strip()
    assert sid, "child failed to start"

    deadline = time.time() + 30
    while not os.path.exists(marker):
        assert time.time() < deadline, "tool never started"
        assert proc.poll() is None, "child died prematurely"
        time.sleep(0.05)

    os.kill(proc.pid, signal.SIGKILL)   # the power cord
    proc.wait(timeout=30)

    # Fresh process semantics: brand-new Store over the WAL DB.
    store = Store(db)
    tail = store.journal_tail(sid)
    assert tail["state"] == "EXEC_INTENT", f"journal tail: {tail}"

    tool = ToolSpec("deploy", lambda a: "never", idempotent=False)
    kernel = Kernel(store, MockAdapter([]), {"deploy": tool},
                    CapabilityManifest(tools=frozenset({"deploy"})))
    report = kernel.recover(sid)
    assert report.status == "quarantined"
    assert store.session_status(sid) == "needs_review"
    # DB integrity after SIGKILL
    row = store.conn.execute("PRAGMA integrity_check;").fetchone()
    assert row[0] == "ok"
