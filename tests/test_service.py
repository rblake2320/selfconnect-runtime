"""FastAPI surface via in-process TestClient (no bound port). Auth, RBAC,
run/status/ledger, bind-guard, WS streaming, and the approval gate over REST."""
import pytest
from fastapi.testclient import TestClient

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.policy import Policy
from scr.service import BindRefused, check_bind, create_app
from scr.state import Store


def _plain_factory():
    def make(store, sid):
        return Kernel(store, MockAdapter([ModelResponse("hi there")]), {},
                      CapabilityManifest())
    return make


def _client(store, factory):
    app = create_app(store, factory)
    return TestClient(app)


def _seed_tokens(store):
    store.token_put("admintok", "admin@x", "admin")
    store.token_put("optok", "op@x", "operator")
    store.token_put("audtok", "aud@x", "auditor")
    store.token_put("viewtok", "view@x", "viewer")


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_auth_required():
    store = Store(":memory:")
    _seed_tokens(store)
    c = _client(store, _plain_factory())
    assert c.post("/runs", json={"user_text": "x", "idem_key": "k"}).status_code == 401
    assert c.post("/runs", json={"user_text": "x", "idem_key": "k"},
                  headers=_h("bogus")).status_code == 401


def test_operator_can_run_viewer_cannot():
    store = Store(":memory:")
    _seed_tokens(store)
    c = _client(store, _plain_factory())
    ok = c.post("/runs", json={"user_text": "x", "idem_key": "k1"}, headers=_h("optok"))
    assert ok.status_code == 200
    assert ok.json()["final_text"] == "hi there"
    denied = c.post("/runs", json={"user_text": "x", "idem_key": "k2"}, headers=_h("viewtok"))
    assert denied.status_code == 403


def test_status_and_ledger_roles():
    store = Store(":memory:")
    _seed_tokens(store)
    c = _client(store, _plain_factory())
    run = c.post("/runs", json={"user_text": "x", "idem_key": "k1"}, headers=_h("optok")).json()
    # viewer can read status
    assert c.get(f"/jobs/{run['job_id']}", headers=_h("viewtok")).status_code == 200
    # viewer cannot read ledger; auditor can
    assert c.get(f"/sessions/{run['session_id']}/ledger", headers=_h("viewtok")).status_code == 403
    led = c.get(f"/sessions/{run['session_id']}/ledger", headers=_h("audtok"))
    assert led.status_code == 200 and led.json()["ok"] is True


def test_idempotent_run_over_rest():
    store = Store(":memory:")
    _seed_tokens(store)
    c = _client(store, _plain_factory())
    a = c.post("/runs", json={"user_text": "x", "idem_key": "dup"}, headers=_h("optok")).json()
    b = c.post("/runs", json={"user_text": "y", "idem_key": "dup"}, headers=_h("optok")).json()
    assert b["deduped"] is True and a["job_id"] == b["job_id"]


def test_bind_guard():
    check_bind("127.0.0.1", tls=False, auth=False)   # loopback ok
    check_bind("localhost", tls=False, auth=False)
    with pytest.raises(BindRefused):
        check_bind("0.0.0.0", tls=False, auth=False)  # non-loopback, no TLS/auth
    check_bind("10.0.0.5", tls=True, auth=True)       # allowed with TLS+auth


def test_ws_streams_events():
    store = Store(":memory:")
    _seed_tokens(store)
    c = _client(store, _plain_factory())
    run = c.post("/runs", json={"user_text": "x", "idem_key": "k1"}, headers=_h("optok")).json()
    with c.websocket_connect(f"/ws/jobs/{run['job_id']}", headers=_h("viewtok")) as ws:
        first = ws.receive_json()
        assert "seq" in first and "state" in first


class _ConvAdapter:
    """Conversation-aware mock: emits the deploy call until a tool result for
    it appears, then finalizes — models a real model across resume (a fresh
    scripted adapter would wrongly replay its script from the start)."""

    def complete(self, messages, tools):
        already_ran = any(m["role"] == "tool" and "deploy" in m["content"]
                          for m in messages)
        if already_ran:
            return ModelResponse("done")
        return ModelResponse("", (ToolCall("c1", "deploy", {"env": "prod"}),))


def test_approval_gate_over_rest():
    store = Store(":memory:")
    _seed_tokens(store)
    calls = []

    def factory(store, sid):
        tool = ToolSpec("deploy", lambda a: calls.append(a) or "deployed", idempotent=False)
        return Kernel(store, _ConvAdapter(), {"deploy": tool},
                      CapabilityManifest(tools=frozenset({"deploy"})),
                      policy=Policy.from_yaml("require_approval:\n  - deploy\n"))

    c = _client(store, factory)
    run = c.post("/runs", json={"user_text": "deploy", "idem_key": "k1"},
                 headers=_h("optok")).json()
    assert run["stopped_reason"] == "awaiting_approval"
    assert run["pending_approval"] and calls == []
    # approve → resume → completed, tool runs exactly once
    res = c.post(f"/jobs/{run['job_id']}/approve",
                 json={"approval_id": run["pending_approval"], "approver": "ron"},
                 headers=_h("optok")).json()
    assert res["stopped_reason"] == "completed"
    assert calls == [{"env": "prod"}]
