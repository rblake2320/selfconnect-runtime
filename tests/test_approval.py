"""HITL approval gate: pause, approve/deny, resume, replay-safety, crash
recovery during approval wait, and the token budget governor."""
import json

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Guards, Kernel, _approval_id
from scr.policy import Policy
from scr.state import Store


def _kernel(store, script, policy, guards=Guards()):
    calls_executed = []

    def deploy(args):
        calls_executed.append(args)
        return "deployed"

    from scr.kernel import ToolSpec
    tools = {"deploy": ToolSpec("deploy", deploy, idempotent=False)}
    m = CapabilityManifest(tools=frozenset({"deploy"}))
    return Kernel(store, MockAdapter(script), tools, m, guards=guards,
                  policy=policy), calls_executed


APPROVE_POLICY = Policy.from_yaml("require_approval:\n  - deploy\n")


def test_approval_required_pauses_without_executing():
    store = Store(":memory:")
    sid = store.create_session()
    k, executed = _kernel(store, [
        ModelResponse("", (ToolCall("c1", "deploy", {"env": "prod"}),)),
        ModelResponse("done"),
    ], APPROVE_POLICY)
    res = k.run(sid, "deploy to prod")
    assert res.stopped_reason == "awaiting_approval"
    assert res.pending_approval is not None
    assert executed == []  # nothing ran
    assert store.journal_tail(sid)["state"] == "AWAITING_APPROVAL"


def test_approve_then_resume_executes_once():
    store = Store(":memory:")
    sid = store.create_session()
    k, executed = _kernel(store, [
        ModelResponse("", (ToolCall("c1", "deploy", {"env": "prod"}),)),
        ModelResponse("all done"),
    ], APPROVE_POLICY)
    res = k.run(sid, "deploy")
    aid = res.pending_approval
    k.approve(sid, aid, approver="ron@example.com")
    res2 = k.resume(sid)
    assert res2.stopped_reason == "completed"
    assert res2.final_text == "all done"
    assert executed == [{"env": "prod"}]  # executed exactly once


def test_deny_then_resume_folds_denial_tool_never_runs():
    store = Store(":memory:")
    sid = store.create_session()
    k, executed = _kernel(store, [
        ModelResponse("", (ToolCall("c1", "deploy", {"env": "prod"}),)),
        ModelResponse("understood, not deploying"),
    ], APPROVE_POLICY)
    res = k.run(sid, "deploy")
    k.deny(sid, res.pending_approval, approver="ron@example.com")
    res2 = k.resume(sid)
    assert res2.stopped_reason == "completed"
    assert executed == []  # tool never ran
    tool_msgs = [m for m in store.get_messages(sid) if m["role"] == "tool"]
    assert "DENIED by approver ron@example.com" in tool_msgs[0]["content"]


def test_approval_bound_to_action_wrong_id_does_not_authorize():
    store = Store(":memory:")
    sid = store.create_session()
    k, executed = _kernel(store, [
        ModelResponse("", (ToolCall("c1", "deploy", {"env": "prod"}),)),
        ModelResponse("done"),
    ], APPROVE_POLICY)
    res = k.run(sid, "deploy")
    # Approve a DIFFERENT action id (forged / mismatched) — must not authorize.
    forged = _approval_id(sid, ToolCall("c1", "deploy", {"env": "staging"}))
    assert forged != res.pending_approval
    k.approve(sid, forged, approver="attacker")
    res2 = k.resume(sid)
    assert res2.stopped_reason == "awaiting_approval"  # still gated
    assert executed == []


def test_crash_during_approval_wait_recovers_to_same_gate(tmp_path):
    db = str(tmp_path / "appr.db")
    store = Store(db)
    sid = store.create_session()
    k, _ = _kernel(store, [
        ModelResponse("", (ToolCall("c1", "deploy", {"env": "prod"}),)),
        ModelResponse("done"),
    ], APPROVE_POLICY)
    res = k.run(sid, "deploy")
    aid = res.pending_approval
    store.close()  # simulate crash — approval never recorded

    # Fresh process semantics: new Store over the same DB.
    store2 = Store(db)
    k2, executed = _kernel(store2, [ModelResponse("done")], APPROVE_POLICY)
    # Resume before approving → still gated, nothing executed.
    assert k2.resume(sid).stopped_reason == "awaiting_approval"
    assert executed == []
    # Now approve and resume → proceeds.
    k2.approve(sid, aid, approver="ron")
    assert k2.resume(sid).stopped_reason == "completed"
    assert executed == [{"env": "prod"}]


def test_approval_and_denial_are_ledger_events_with_approver():
    store = Store(":memory:")
    sid = store.create_session()
    k, _ = _kernel(store, [
        ModelResponse("", (ToolCall("c1", "deploy", {"env": "prod"}),)),
        ModelResponse("done"),
    ], APPROVE_POLICY)
    res = k.run(sid, "deploy")
    k.approve(sid, res.pending_approval, approver="ron@example.com")
    events = [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,)).fetchall()]
    approval_events = [e for e in events if e.get("type") == "approval"]
    assert approval_events and approval_events[0]["approver"] == "ron@example.com"
    assert approval_events[0]["decision"] == "approved"
    # ledger still verifies (chain intact through approval events)
    assert k.ledger.verify(sid).ok


def test_token_budget_governor_stops_run():
    store = Store(":memory:")
    sid = store.create_session()
    from scr.kernel import ToolSpec
    # Model returns big token usage; budget cap is small → stop reason budget.
    adapter = MockAdapter([
        ModelResponse("thinking", input_tokens=600, output_tokens=600),
        ModelResponse("more", input_tokens=600, output_tokens=600),
        ModelResponse("final"),
    ])
    m = CapabilityManifest(tools=frozenset())
    k = Kernel(store, adapter, {}, m, guards=Guards(max_total_tokens=1000))
    res = k.run(sid, "go")
    assert res.stopped_reason == "budget"
