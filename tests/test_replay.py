"""Deterministic replay (§3.1): same inputs → identical ledger chain;
divergence is detectable."""
from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.ledger import Ledger
from scr.replay import replay_matches, run_and_replay
from scr.state import Store


def _build(script_text):
    def build(store, sid):
        tool = ToolSpec("echo", lambda a: a.get("v", "x"), idempotent=True)
        adapter = MockAdapter([
            ModelResponse("", (ToolCall("c1", "echo", {"v": "hello"}),)),
            ModelResponse(script_text),
        ])
        return Kernel(store, adapter, {"echo": tool},
                      CapabilityManifest(tools=frozenset({"echo"})))
    return build


def test_replay_reproduces_identical_ledger():
    res = run_and_replay("fixedsession01", "do it", _build("final answer"))
    assert res.matches
    assert res.source_head == res.replay_head != "0" * 64
    assert res.source_count == res.replay_count >= 3


def test_divergent_input_produces_different_head():
    # same session id, but the model's final text differs → different ledger.
    a = run_and_replay("sid-A", "task", _build("answer ONE"))
    b = run_and_replay("sid-A", "task", _build("answer TWO"))
    assert a.matches and b.matches            # each is internally deterministic
    assert a.source_head != b.source_head     # different content → different chain


def test_replay_matches_against_a_recorded_session():
    # record a real session, then prove a replay reproduces its ledger head
    store = Store(":memory:")
    sid = store.create_session("record-1")
    _build("recorded final")(store, sid).run(sid, "go")
    assert replay_matches(store, sid, "go", _build("recorded final"))
    # a replay with a different script must NOT match the recording
    assert not replay_matches(store, sid, "go", _build("tampered final"))


def test_same_session_id_required_for_reproduction():
    """Different session ids give different idem keys → different ledger, even
    with identical script (confirms the id-binding rationale)."""
    a = run_and_replay("id-X", "t", _build("same"))
    b = run_and_replay("id-Y", "t", _build("same"))
    assert a.source_head != b.source_head
