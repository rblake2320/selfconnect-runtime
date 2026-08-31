"""Crash recovery: simulate process death at every dangerous point and
verify the kernel's classification. The invariant under test:

    A NON-IDEMPOTENT SIDE EFFECT IS NEVER SILENTLY RE-EXECUTED.
"""
import pytest

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Guards, Kernel, ToolSpec
from scr.state import Store


def fresh(tmp_path, tools=None):
    store = Store(str(tmp_path / "r.db"))
    tools = tools or {}
    kernel = Kernel(
        store, MockAdapter([]), tools,
        CapabilityManifest(tools=frozenset(tools.keys())),
    )
    sid = store.create_session()
    return store, kernel, sid


def test_clean_session_reports_clean(tmp_path):
    store, kernel, sid = fresh(tmp_path)
    assert kernel.recover(sid).status == "clean"


def test_crash_during_model_call_classified_reissue(tmp_path):
    """Adapter raises AFTER the intent record is journaled — exactly the
    window a real crash occupies. Recovery must say: reissue."""
    tool = ToolSpec("echo", lambda a: "x", idempotent=True)
    kernel_store = Store(str(tmp_path / "r.db"))
    kernel = Kernel(
        kernel_store,
        MockAdapter([ConnectionError("power pulled mid-call")]),
        {"echo": tool},
        CapabilityManifest(tools=frozenset({"echo"})),
    )
    sid = kernel_store.create_session()
    with pytest.raises(ConnectionError):
        kernel.run(sid, "go")
    assert kernel_store.journal_tail(sid)["state"] == "MODEL_CALL_INTENT"

    # "restart": new Store + Kernel over the same DB file
    store2 = Store(str(tmp_path / "r.db"))
    kernel2 = Kernel(store2, MockAdapter([]), {"echo": tool},
                     CapabilityManifest(tools=frozenset({"echo"})))
    report = kernel2.recover(sid)
    assert report.status == "reissue_model_call"
    assert store2.journal_tail(sid)["state"] == "RECOVERED"


def test_crash_during_idempotent_tool_classified_safe_reissue(tmp_path):
    def dies(args):
        raise KeyboardInterrupt("kill -9 during tool exec")

    tool = ToolSpec("reader", dies, idempotent=True)
    store = Store(str(tmp_path / "r.db"))
    kernel = Kernel(
        store,
        MockAdapter([ModelResponse("", (ToolCall("c1", "reader", {"p": "a"}),))]),
        {"reader": tool},
        CapabilityManifest(tools=frozenset({"reader"})),
    )
    sid = store.create_session()
    with pytest.raises(KeyboardInterrupt):
        kernel.run(sid, "go")
    assert store.journal_tail(sid)["state"] == "EXEC_INTENT"

    store2 = Store(str(tmp_path / "r.db"))
    kernel2 = Kernel(store2, MockAdapter([]), {"reader": tool},
                     CapabilityManifest(tools=frozenset({"reader"})))
    report = kernel2.recover(sid)
    assert report.status == "safe_reissue"


def test_crash_during_nonidempotent_tool_quarantined(tmp_path):
    """THE test. A payment-like tool crashed mid-exec. Recovery must
    quarantine — never re-fire the side effect."""
    fired = []

    def payment(args):
        fired.append(1)
        raise KeyboardInterrupt("crash after the wire went out")

    tool = ToolSpec("send_payment", payment, idempotent=False)
    store = Store(str(tmp_path / "r.db"))
    kernel = Kernel(
        store,
        MockAdapter([ModelResponse("", (ToolCall("c1", "send_payment", {"usd": 500}),))]),
        {"send_payment": tool},
        CapabilityManifest(tools=frozenset({"send_payment"})),
    )
    sid = store.create_session()
    with pytest.raises(KeyboardInterrupt):
        kernel.run(sid, "go")
    assert fired == [1]

    store2 = Store(str(tmp_path / "r.db"))
    kernel2 = Kernel(store2, MockAdapter([]), {"send_payment": tool},
                     CapabilityManifest(tools=frozenset({"send_payment"})))
    report = kernel2.recover(sid)
    assert report.status == "quarantined"
    assert "will not re-execute" in report.detail
    assert store2.session_status(sid) == "needs_review"
    assert store2.journal_tail(sid)["state"] == "FAILED_NEEDS_REVIEW"
    assert fired == [1]  # recovery itself fired nothing


def test_crash_after_result_persisted_resumes_without_reexec(tmp_path):
    """Tool completed and persisted its result, crash landed BEFORE the
    EXEC_DONE record. Recovery folds the persisted result — zero re-exec."""
    fired = []

    def tool_fn(args):
        fired.append(1)
        return "receipt-771"

    tool = ToolSpec("send_payment", tool_fn, idempotent=False)
    store = Store(str(tmp_path / "r.db"))
    sid = store.create_session()

    # Reproduce the exact on-disk state: EXEC_INTENT journaled + result
    # persisted, no EXEC_DONE (crash in between).
    from scr.kernel import _idem_key
    idem = _idem_key(sid, 2, "send_payment", {"usd": 500})
    store.journal_append(sid, "MODEL_CALL_DONE", {"iteration": 1})
    store.journal_append(
        sid, "EXEC_INTENT",
        {"tool": "send_payment", "args": {"usd": 500},
         "idempotent": False, "idem_key": idem},
    )
    store.tool_result_put(idem, sid, "send_payment", "receipt-771")

    kernel = Kernel(store, MockAdapter([]), {"send_payment": tool},
                    CapabilityManifest(tools=frozenset({"send_payment"})))
    report = kernel.recover(sid)
    assert report.status == "resumed"
    assert fired == []  # never re-executed
    assert store.journal_tail(sid)["state"] == "EXEC_DONE"


def test_recovery_is_idempotent_itself(tmp_path):
    """Running recover() twice must not change the classification or
    duplicate side effects — recovery is safe to re-run after ITS crash."""
    store, kernel, sid = fresh(tmp_path)
    store.journal_append(sid, "MODEL_CALL_INTENT", {"iteration": 1})
    r1 = kernel.recover(sid)
    r2 = kernel.recover(sid)
    assert r1.status == "reissue_model_call"
    assert r2.status == "clean"  # tail is now RECOVERED — nothing dangling


def test_full_run_after_recovery_completes(tmp_path):
    """End-to-end: crash → restart → recover → finish the task."""
    tool = ToolSpec("echo", lambda a: f"echo:{a['v']}", idempotent=True)
    store = Store(str(tmp_path / "r.db"))
    kernel = Kernel(store, MockAdapter([ConnectionError("crash")]),
                    {"echo": tool}, CapabilityManifest(tools=frozenset({"echo"})))
    sid = store.create_session()
    with pytest.raises(ConnectionError):
        kernel.run(sid, "task")

    store2 = Store(str(tmp_path / "r.db"))
    kernel2 = Kernel(
        store2,
        MockAdapter([
            ModelResponse("", (ToolCall("c1", "echo", {"v": "recovered"}),)),
            ModelResponse("finished after crash"),
        ]),
        {"echo": tool},
        CapabilityManifest(tools=frozenset({"echo"})),
    )
    assert kernel2.recover(sid).status == "reissue_model_call"
    result = kernel2.run(sid, "continue")
    assert result.stopped_reason == "completed"
    assert result.final_text == "finished after crash"
