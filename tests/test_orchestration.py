"""Delegation attenuation per edge, depth limits, and persisted mailbox."""
import pytest

from scr.capability import CapabilityManifest
from scr.orchestration import AgentNode, DelegationError, Mailbox, Team
from scr.state import Store


def _m(tools, hosts=()):
    return CapabilityManifest(tools=frozenset(tools), net_hosts=frozenset(hosts))


def test_child_cannot_exceed_parent():
    root = AgentNode("lead", _m({"fs_read", "fs_write", "http_get"}, {"a", "b"}))
    team = Team(root=root)
    team.add("lead", AgentNode("worker", _m({"fs_read", "http_get", "proc_exec"}, {"a", "c"})))
    eff = team.effective_manifest("worker")
    # proc_exec and host c were never granted to the parent → dropped
    assert eff.tools == frozenset({"fs_read", "http_get"})
    assert eff.net_hosts == frozenset({"a"})


def test_grandchild_is_subset_of_child_and_parent():
    root = AgentNode("lead", _m({"fs_read", "fs_write", "http_get"}))
    team = Team(root=root)
    team.add("lead", AgentNode("mid", _m({"fs_read", "http_get"})))
    team.add("mid", AgentNode("leaf", _m({"fs_read", "fs_write", "http_get"})))
    eff = team.effective_manifest("leaf")
    # leaf asked for fs_write, but mid didn't have it → dropped
    assert eff.tools == frozenset({"fs_read", "http_get"})


def test_depth_limit_enforced():
    root = AgentNode("a", _m({"fs_read"}))
    team = Team(root=root, max_depth=2)
    team.add("a", AgentNode("b", _m({"fs_read"})))
    team.add("b", AgentNode("c", _m({"fs_read"})))
    team.add("c", AgentNode("d", _m({"fs_read"})))  # depth 3 > max 2
    with pytest.raises(DelegationError):
        team.effective_manifest("d")


def test_unreachable_node_raises():
    root = AgentNode("a", _m({"fs_read"}))
    team = Team(root=root)
    team.nodes["orphan"] = AgentNode("orphan", _m({"fs_read"}))
    with pytest.raises(DelegationError):
        team.effective_manifest("orphan")


def test_mailbox_persists_in_order():
    store = Store(":memory:")
    sid = store.create_session()
    mb = Mailbox(store, sid)
    mb.send("lead", "worker", "first")
    mb.send("lead", "worker", "second")
    mb.send("lead", "other", "unrelated")
    inbox = mb.inbox("worker")
    assert [m["body"] for m in inbox] == ["first", "second"]
