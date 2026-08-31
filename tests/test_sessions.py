"""Session manager + durable queue: happy path, idempotency, cancel, and
kill-mid-run recovery at the job level."""
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.sessions import SessionManager
from scr.state import Store

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _factory(script):
    def make(store, session_id):
        tool = ToolSpec("noop", lambda a: "ok", idempotent=True)
        return Kernel(store, MockAdapter(list(script)), {"noop": tool},
                      CapabilityManifest(tools=frozenset({"noop"})))
    return make


def test_enqueue_and_run():
    store = Store(":memory:")
    mgr = SessionManager(store, _factory([ModelResponse("done")]))
    job = mgr.enqueue("hello", idem_key="k1")
    assert not job.deduped
    result = mgr.run_job(job.job_id)
    assert result.stopped_reason == "completed"
    assert mgr.status(job.job_id)["status"] == "done"


def test_idempotent_enqueue_runs_once():
    store = Store(":memory:")
    mgr = SessionManager(store, _factory([ModelResponse("done")]))
    a = mgr.enqueue("hello", idem_key="same")
    b = mgr.enqueue("hello again", idem_key="same")
    assert b.deduped
    assert a.job_id == b.job_id  # same job, no duplicate scheduled


def test_cancel_marks_cancelled():
    store = Store(":memory:")
    mgr = SessionManager(store, _factory([ModelResponse("done")]))
    job = mgr.enqueue("work", idem_key="c1")
    mgr.cancel(job.job_id)
    assert mgr.status(job.job_id)["status"] == "cancelled"
    # a cancelled job is terminal — run_job does not execute it
    result = mgr.run_job(job.job_id)
    assert result.stopped_reason == "cancelled"


@pytest.mark.skipif(os.name == "nt",
                    reason="SIGKILL; Windows uses the TerminateProcess chaos variant in Phase 9")
def test_kill_mid_run_then_recover_quarantines(tmp_path):
    db = str(tmp_path / "jobs.db")
    marker = str(tmp_path / "began.marker")
    child = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {SRC!r})
        from scr.state import Store
        from scr.sessions import SessionManager
        from scr.kernel import Kernel, ToolSpec
        from scr.capability import CapabilityManifest
        from scr.gateway import MockAdapter, ModelResponse, ToolCall

        def factory(store, sid):
            def slow(a):
                open({marker!r}, "w").write("began")
                time.sleep(60)
                return "unreachable"
            tool = ToolSpec("deploy", slow, idempotent=False)
            return Kernel(store, MockAdapter([
                ModelResponse("", (ToolCall("c1", "deploy", {{"env": "prod"}}),))
            ]), {{"deploy": tool}}, CapabilityManifest(tools=frozenset({{"deploy"}})))

        store = Store({db!r})
        mgr = SessionManager(store, factory)
        job = mgr.enqueue("deploy prod", idem_key="deploy-1")
        print(job.job_id, flush=True)
        mgr.run_job(job.job_id)
    """)
    proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True)
    job_id = proc.stdout.readline().strip()
    deadline = time.time() + 30
    while not os.path.exists(marker):
        assert time.time() < deadline and proc.poll() is None
        time.sleep(0.05)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)

    # fresh manager over the surviving DB
    store = Store(db)
    def factory(store, sid):
        return Kernel(store, MockAdapter([]),
                      {"deploy": ToolSpec("deploy", lambda a: "x", idempotent=False)},
                      CapabilityManifest(tools=frozenset({"deploy"})))
    mgr = SessionManager(store, factory)
    assert mgr.status(job_id)["status"] == "running"  # crashed mid-run
    recovered = mgr.recover_all()
    assert recovered and recovered[0]["recovery"] == "quarantined"
    assert mgr.status(job_id)["status"] == "needs_review"
