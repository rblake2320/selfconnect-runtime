"""Adversarial native-tool tests: capability checks happen BEFORE any spawn;
sandbox re-validates; real read/write/list/exec inside the jail work.
"""
import json
import os
import sys

import pytest

from scr.capability import CapabilityManifest, ExecRule
from scr.sandbox import SandboxRunner
from scr.tools_native import build_native_tools


def _mk(tmp_path, **kw):
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True)
    defaults = dict(
        tools=frozenset({"fs_read", "fs_write", "fs_list", "http_get", "proc_exec"}),
        fs_read_roots=(str(ws),),
        fs_write_roots=(str(ws / "out"),),
        net_hosts=frozenset({"api.internal"}),
        exec_rules=(ExecRule(sys.executable, r".*"),),
        max_budget_usd=1.0,
    )
    defaults.update(kw)
    m = CapabilityManifest(**defaults)
    return m, build_native_tools(m, SandboxRunner()), ws


def test_read_inside_jail(tmp_path):
    m, tools, ws = _mk(tmp_path)
    (ws / "note.txt").write_text("hello sandbox")
    out = tools["fs_read"].fn({"path": str(ws / "note.txt")})
    assert json.loads(out)["content"] == "hello sandbox"


def test_traversal_denied_before_spawn(tmp_path):
    m, tools, ws = _mk(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    out = tools["fs_read"].fn({"path": str(ws / ".." / "secret.txt")})
    assert out.startswith("DENIED by capability kernel")
    assert "TOP SECRET" not in out


def test_write_outside_write_roots_denied(tmp_path):
    m, tools, ws = _mk(tmp_path)
    out = tools["fs_write"].fn({"path": str(ws / "escape.txt"), "content": "x"})
    assert out.startswith("DENIED by capability kernel")
    assert not (ws / "escape.txt").exists()


def test_write_inside_jail_crlf_safe(tmp_path):
    m, tools, ws = _mk(tmp_path)
    target = ws / "out" / "a.txt"
    out = tools["fs_write"].fn({"path": str(target), "content": "line1\r\nline2"})
    assert json.loads(out)["ok"] is True
    assert target.read_bytes() == b"line1\r\nline2"


def test_list_inside_jail(tmp_path):
    m, tools, ws = _mk(tmp_path)
    (ws / "a.txt").write_text("1")
    (ws / "sub").mkdir()
    out = json.loads(tools["fs_list"].fn({"path": str(ws)}))
    names = {e["name"] for e in out["entries"]}
    assert {"a.txt", "sub", "out"} <= names


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink; Windows junction variant below")
def test_symlink_escape_denied_posix(tmp_path):
    m, tools, ws = _mk(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET")
    link = ws / "link.txt"
    os.symlink(outside, link)
    out = tools["fs_read"].fn({"path": str(link)})
    assert out.startswith("DENIED by capability kernel")
    assert "SECRET" not in out


def test_http_non_allowlisted_host_denied(tmp_path):
    m, tools, ws = _mk(tmp_path)
    out = tools["http_get"].fn({"url": "http://evil.example.com/x"})
    assert out.startswith("DENIED by capability kernel")


def test_exec_non_allowlisted_binary_denied(tmp_path):
    m, tools, ws = _mk(tmp_path, exec_rules=())
    out = tools["proc_exec"].fn({"binary": sys.executable, "args": ["-c", "print(1)"]})
    assert out.startswith("DENIED by capability kernel")


def test_exec_allowlisted_captures_output(tmp_path):
    m, tools, ws = _mk(tmp_path)
    out = json.loads(tools["proc_exec"].fn(
        {"binary": sys.executable, "args": ["-c", "print('run-inside-jail')"]}))
    assert out["ok"] is True
    assert "run-inside-jail" in out["stdout"]


def test_idempotency_flags():
    """Recovery classification depends on these; assert they match the design."""
    from scr.capability import CapabilityManifest
    m = CapabilityManifest(tools=frozenset({"fs_read", "fs_write", "fs_list",
                                             "http_get", "proc_exec"}),
                           fs_read_roots=("x",), fs_write_roots=("x",))
    tools = build_native_tools(m, SandboxRunner())
    assert tools["fs_read"].idempotent is True
    assert tools["fs_list"].idempotent is True
    assert tools["http_get"].idempotent is True
    assert tools["fs_write"].idempotent is False
    assert tools["proc_exec"].idempotent is False
