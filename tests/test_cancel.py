"""G5 — session cancel kills the in-flight process tree (no orphans) and
cooperatively stops the kernel. Cross-platform (Job Object / killpg)."""
import os
import sys
import textwrap
import threading
import time

from scr.capability import CapabilityManifest, ExecRule
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel
from scr.sandbox import SandboxLimits, SandboxRunner
from scr.sessions import SessionManager
from scr.state import Store
from scr.tools_native import build_native_tools


def test_cancel_kills_inflight_tree_no_orphan(tmp_path):
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True)
    # Forward-slash paths work with open() on both Windows and POSIX and avoid
    # backslash-escaping hazards when embedded in a program string.
    began = str(tmp_path / "began.marker").replace("\\", "/")
    survivor = str(tmp_path / "survivor.marker").replace("\\", "/")

    # A python program the sandboxed worker will exec: mark 'began', spawn a
    # grandchild that writes 'survivor' ONLY after a long sleep, then sleep.
    prog = textwrap.dedent(f"""
        import subprocess, sys, time
        open('{began}', 'w').write('x')
        gc = "import time; time.sleep(30); open('{survivor}','w').write('x')"
        subprocess.Popen([sys.executable, '-c', gc])
        time.sleep(30)
    """)

    manifest = CapabilityManifest(
        tools=frozenset({"proc_exec"}),
        fs_read_roots=(str(ws),), fs_write_roots=(str(ws / "out"),),
        # (?s) so the multi-line program argument matches (`.` excludes \n by
        # default — an intentional capability-kernel behavior, see below).
        exec_rules=(ExecRule(sys.executable, r"(?s).*"),))
    runner = SandboxRunner(SandboxLimits(timeout_seconds=60))

    def factory(store, sid):
        tools = build_native_tools(manifest, runner)
        k = Kernel(store, MockAdapter([
            ModelResponse("", (ToolCall("c1", "proc_exec",
                               {"binary": sys.executable, "args": ["-c", prog]}),)),
            ModelResponse("done"),
        ]), tools, manifest)
        k.sandbox_runner = runner          # let the manager reach the tree
        return k

    store = Store(str(tmp_path / "s.db"))
    mgr = SessionManager(store, factory)
    job = mgr.enqueue("run the tool", "k1")

    result_box = {}

    def run():
        result_box["r"] = mgr.run_job(job.job_id)

    t = threading.Thread(target=run)
    t.start()

    # wait until the sandboxed tool has actually started its tree
    deadline = time.time() + 30
    began_path = str(tmp_path / "began.marker")
    while not os.path.exists(began_path):
        assert time.time() < deadline, "tool tree never started"
        time.sleep(0.05)

    mgr.cancel(job.job_id)                  # G5: kill the tree + stop the loop
    t.join(timeout=30)
    assert not t.is_alive(), "run did not stop after cancel"

    # No orphan: the grandchild must never write its survivor marker.
    time.sleep(2.0)
    assert not os.path.exists(str(tmp_path / "survivor.marker")), \
        "grandchild survived session cancel — orphaned process"
    assert mgr.status(job.job_id)["status"] == "cancelled"


def test_kernel_cooperative_cancel_stops_before_tools():
    """Kernel-level: a set cancel_check stops the loop with reason 'cancelled'
    before any tool executes."""
    from scr.kernel import ToolSpec
    store = Store(":memory:")
    sid = store.create_session()
    ran = []
    k = Kernel(store, MockAdapter([
        ModelResponse("", (ToolCall("c1", "noop", {}),)),
        ModelResponse("done"),
    ]), {"noop": ToolSpec("noop", lambda a: ran.append(1) or "ok", idempotent=True)},
        CapabilityManifest(tools=frozenset({"noop"})))
    k.cancel_check = lambda: True           # already cancelled
    res = k.run(sid, "go")
    assert res.stopped_reason == "cancelled"
    assert ran == []


def test_kernel_cancel_after_first_iteration():
    """cancel_check flips True after the first model call → stops before the
    tool executes on the next check."""
    from scr.kernel import ToolSpec
    store = Store(":memory:")
    sid = store.create_session()
    ran = []
    state = {"calls": 0}

    def check():
        # allow the first iteration to begin, then cancel
        state["calls"] += 1
        return state["calls"] > 1

    k = Kernel(store, MockAdapter([
        ModelResponse("", (ToolCall("c1", "noop", {}),)),
        ModelResponse("", (ToolCall("c2", "noop", {}),)),
        ModelResponse("done"),
    ]), {"noop": ToolSpec("noop", lambda a: ran.append(1) or "ok", idempotent=True)},
        CapabilityManifest(tools=frozenset({"noop"})))
    k.cancel_check = check
    res = k.run(sid, "go")
    assert res.stopped_reason == "cancelled"
