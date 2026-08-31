"""Classification ceilings + parent-revocation chain invalidation (§3.3).

Behavior only — the encoding/wire format is MELD-gated and intentionally
undocumented; these tests pin the SEMANTICS.
"""
import pytest

from scr.capability import CapabilityDenied, CapabilityManifest, attenuate
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.orchestration import AgentNode, DelegationError, Team
from scr.state import Store


# ------------------------------------------------- classification ceilings
def test_ceiling_denies_higher_classification():
    m = CapabilityManifest(tools=frozenset({"t"}), classification_ceiling="internal")
    m.check_classification("public")
    m.check_classification("internal")
    with pytest.raises(CapabilityDenied):
        m.check_classification("confidential")
    with pytest.raises(CapabilityDenied):
        m.check_classification("secret")


def test_unknown_classification_denied_by_default():
    m = CapabilityManifest(classification_ceiling="secret")
    with pytest.raises(CapabilityDenied):
        m.check_classification("cosmic-top-secret")


def test_attenuation_lowers_ceiling_to_more_restrictive():
    parent = CapabilityManifest(classification_ceiling="confidential")
    child = CapabilityManifest(classification_ceiling="secret")   # asks higher
    assert attenuate(parent, child).classification_ceiling == "confidential"
    parent2 = CapabilityManifest(classification_ceiling="secret")
    child2 = CapabilityManifest(classification_ceiling="public")
    assert attenuate(parent2, child2).classification_ceiling == "public"


def test_kernel_folds_classification_denial():
    store = Store(":memory:")
    sid = store.create_session()
    ran = []
    tool = ToolSpec("secret_tool", lambda a: ran.append(1) or "did it",
                    idempotent=True, classification="secret")
    m = CapabilityManifest(tools=frozenset({"secret_tool"}),
                           classification_ceiling="internal")
    kernel = Kernel(store, MockAdapter([
        ModelResponse("", (ToolCall("c1", "secret_tool", {}),)),
        ModelResponse("done"),
    ]), {"secret_tool": tool}, m)
    kernel.run(sid, "go")
    tool_msg = [x for x in store.get_messages(sid) if x["role"] == "tool"][0]
    assert "DENIED by capability kernel" in tool_msg["content"]
    assert ran == []                          # over-ceiling tool never executed


def test_kernel_allows_within_ceiling():
    store = Store(":memory:")
    sid = store.create_session()
    tool = ToolSpec("internal_tool", lambda a: "ok", idempotent=True,
                    classification="internal")
    m = CapabilityManifest(tools=frozenset({"internal_tool"}),
                           classification_ceiling="confidential")
    kernel = Kernel(store, MockAdapter([
        ModelResponse("", (ToolCall("c1", "internal_tool", {}),)),
        ModelResponse("done"),
    ]), {"internal_tool": tool}, m)
    kernel.run(sid, "go")
    tool_msg = [x for x in store.get_messages(sid) if x["role"] == "tool"][0]
    assert '"result": "ok"' in tool_msg["content"]


# ------------------------------------------ parent-revocation chain sever
def _m(tools):
    return CapabilityManifest(tools=frozenset(tools))


def _team():
    t = Team(root=AgentNode("lead", _m({"a", "b"})))
    t.add("lead", AgentNode("mid", _m({"a", "b"})))
    t.add("mid", AgentNode("leaf", _m({"a"})))
    t.add("lead", AgentNode("sibling", _m({"a"})))
    return t


def test_revoking_parent_severs_descendants():
    t = _team()
    assert t.effective_manifest("leaf").tools == frozenset({"a"})   # works before
    t.revoke("mid")                                                 # revoke the parent
    assert t.is_severed("leaf")
    with pytest.raises(DelegationError):
        t.effective_manifest("leaf")       # child invalidated by revoked ancestor


def test_revoking_node_severs_itself_not_siblings():
    t = _team()
    t.revoke("leaf")
    with pytest.raises(DelegationError):
        t.effective_manifest("leaf")
    # sibling under a different path is unaffected
    assert t.effective_manifest("sibling").tools == frozenset({"a"})


def test_revoking_root_severs_everything():
    t = _team()
    t.revoke("lead")
    for name in ("mid", "leaf", "sibling"):
        assert t.is_severed(name)
        with pytest.raises(DelegationError):
            t.effective_manifest(name)
