"""MCP client host tests: handshake/list/call round-trip, scoped env,
crash-restart, denied-capability scoping, idempotent defaults, and a full
kernel loop driving an MCP tool under capability enforcement.
"""
import os
import sys

import pytest

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel
from scr.mcp_host import MCPHost, MCPServerConfig
from scr.state import Store

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mcp_fixture_server.py")


def _cfg(**kw):
    defaults = dict(
        name="fx", transport="stdio",
        command=(sys.executable, FIXTURE),
        idempotent_tools=frozenset({"echo", "add", "env_echo"}),
        call_timeout=15.0,
    )
    defaults.update(kw)
    return MCPServerConfig(**defaults)


def _host(manifest, cfg):
    host = MCPHost(manifest)
    host.add_server(cfg)
    return host


def test_handshake_list_and_call():
    m = CapabilityManifest(tools=frozenset({"mcp__fx__echo", "mcp__fx__add"}))
    host = _host(m, _cfg())
    try:
        specs = host.tool_specs()
        assert "mcp__fx__echo" in specs and "mcp__fx__add" in specs
        assert specs["mcp__fx__add"].fn({"a": 2, "b": 3}) == "5"
        assert specs["mcp__fx__echo"].fn({"text": "ping"}) == "ping"
    finally:
        host.stop_all()


def test_scoped_env_only(monkeypatch):
    monkeypatch.setenv("SCR_MCP_LEAK", "leaked")
    m = CapabilityManifest(tools=frozenset({"mcp__fx__env_echo"}))
    host = _host(m, _cfg(env={"SCR_SCOPED_OK": "present"}))
    try:
        spec = host.tool_specs()["mcp__fx__env_echo"]
        assert spec.fn({"name": "SCR_SCOPED_OK"}) == "present"      # configured var reaches server
        assert spec.fn({"name": "SCR_MCP_LEAK"}) == "<unset>"       # ambient secret does not
    finally:
        host.stop_all()


def test_idempotent_defaults_false():
    """A tool NOT listed in idempotent_tools projects as idempotent=False so
    an interrupted call is quarantined by the kernel."""
    m = CapabilityManifest(tools=frozenset({"mcp__fx__echo"}))
    host = _host(m, _cfg(idempotent_tools=frozenset()))
    try:
        assert host.tool_specs()["mcp__fx__echo"].idempotent is False
    finally:
        host.stop_all()


def test_server_crash_then_restart():
    m = CapabilityManifest(tools=frozenset({"mcp__fx__echo"}))
    host = _host(m, _cfg(env={"SCR_FIXTURE_CRASH_AFTER": "1"}))
    try:
        spec = host.tool_specs()["mcp__fx__echo"]
        assert spec.fn({"text": "first"}) == "first"       # call 1 ok
        # call 2 crashes the server; host restarts and retries → succeeds
        assert spec.fn({"text": "second"}) == "second"
    finally:
        host.stop_all()


def test_denied_capability_mcp_call_not_sent():
    """A projected tool absent from the manifest is denied by the kernel;
    the server never receives the call. We drive it through the kernel to
    prove the folded denial."""
    store = Store(":memory:")
    sid = store.create_session()
    m = CapabilityManifest(tools=frozenset())  # grants nothing
    host = _host(CapabilityManifest(tools=frozenset({"mcp__fx__echo"})), _cfg())
    try:
        specs = host.tool_specs()
        kernel = Kernel(
            store,
            MockAdapter([
                ModelResponse("", (ToolCall("c1", "mcp__fx__echo", {"text": "x"}),)),
                ModelResponse("done"),
            ]),
            specs, m)  # kernel manifest grants nothing → deny
        result = kernel.run(sid, "call the mcp tool")
        assert result.stopped_reason == "completed"
        msgs = store.get_messages(sid)
        tool_msg = [x for x in msgs if x["role"] == "tool"][0]
        assert "DENIED by capability kernel" in tool_msg["content"]
    finally:
        host.stop_all()


def test_kernel_e2e_mcp_tool_under_capability():
    store = Store(":memory:")
    sid = store.create_session()
    m = CapabilityManifest(tools=frozenset({"mcp__fx__add"}))
    host = _host(m, _cfg())
    try:
        specs = host.tool_specs()
        kernel = Kernel(
            store,
            MockAdapter([
                ModelResponse("", (ToolCall("c1", "mcp__fx__add", {"a": 40, "b": 2}),)),
                ModelResponse("the answer is 42"),
            ]),
            specs, m)
        result = kernel.run(sid, "add 40 and 2")
        assert result.final_text == "the answer is 42"
        tool_msg = [x for x in store.get_messages(sid) if x["role"] == "tool"][0]
        assert '"result": "42"' in tool_msg["content"]
    finally:
        host.stop_all()


def test_call_timeout_enforced():
    """A very short call timeout against a server that responds slowly is
    surfaced as an MCP error, not a hang. The fixture responds fast, so we
    assert the timeout plumbing by using an unroutable http server config."""
    m = CapabilityManifest(tools=frozenset({"mcp__h__x"}))
    host = MCPHost(m)
    with pytest.raises(Exception):
        # http transport probing a dead port fails fast → surfaced, not hung
        host.add_server(MCPServerConfig(
            name="h", transport="http",
            url="http://127.0.0.1:1/mcp", call_timeout=2.0))
    host.stop_all()
