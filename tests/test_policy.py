"""Policy load, approval matching, and admin-tightening (intersection only)."""
import pytest

from scr.capability import CapabilityManifest
from scr.gateway import ToolCall
from scr.policy import Policy, PolicyError


def test_load_and_require_approval_by_tool():
    p = Policy.from_yaml("""
    require_approval:
      - fs_write
      - tool: proc_exec
    """)
    assert p.requires_approval(ToolCall("1", "fs_write", {"path": "x"}))
    assert p.requires_approval(ToolCall("2", "proc_exec", {"binary": "git"}))
    assert not p.requires_approval(ToolCall("3", "fs_read", {"path": "x"}))


def test_require_approval_by_arg_regex():
    p = Policy.from_yaml("""
    require_approval:
      - tool: proc_exec
        arg_match:
          binary: ".*(rm|del).*"
    """)
    assert p.requires_approval(ToolCall("1", "proc_exec", {"binary": "/bin/rm"}))
    assert not p.requires_approval(ToolCall("2", "proc_exec", {"binary": "/bin/ls"}))


def test_tighten_intersects_tools_and_hosts():
    base = CapabilityManifest(
        tools=frozenset({"fs_read", "fs_write", "http_get"}),
        net_hosts=frozenset({"a.internal", "b.internal"}),
    )
    p = Policy.from_yaml("""
    tighten:
      tools: [fs_read, http_get]
      net_hosts: [a.internal]
    """)
    tightened = p.tighten(base)
    assert tightened.tools == frozenset({"fs_read", "http_get"})
    assert tightened.net_hosts == frozenset({"a.internal"})


def test_tighten_rejects_widening_tools():
    base = CapabilityManifest(tools=frozenset({"fs_read"}))
    p = Policy.from_yaml("tighten:\n  tools: [fs_read, proc_exec]\n")
    with pytest.raises(PolicyError):
        p.tighten(base)


def test_tighten_rejects_widening_hosts():
    base = CapabilityManifest(net_hosts=frozenset({"a.internal"}))
    p = Policy.from_yaml("tighten:\n  net_hosts: [a.internal, evil.example.com]\n")
    with pytest.raises(PolicyError):
        p.validate_tightening(base)


def test_empty_policy_requires_nothing():
    p = Policy.from_yaml("")
    assert not p.requires_approval(ToolCall("1", "anything", {}))
    base = CapabilityManifest(tools=frozenset({"x"}))
    assert p.tighten(base).tools == frozenset({"x"})  # no restriction
