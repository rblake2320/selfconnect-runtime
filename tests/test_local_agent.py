"""Layer #2 (sc_local_agent_runtime + sc_qwen_core) ported to SCR: a local-model
mesh agent carrying SelfConnect's operating knowledge, running with observe/read
tools and NO Claude Code. Real end-to-end test with real native tools."""
import json
import os

from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.sandbox import SandboxRunner
from scr.state import Store
from scr.team import load_team_from_dir
from scr.team import TeamRunner
from scr.tools_native import build_native_tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_SRC = os.path.join(ROOT, "packages", "selfconnect-enterprise")


def test_local_agent_carries_operating_knowledge():
    loaded = load_team_from_dir(PKG_SRC, "C:/ws", "C:/out")
    sp = loaded.specs["local-agent"].system_prompt.lower()
    # the ported sc_qwen_core disciplines are present
    assert "untrusted observed data" in sp
    assert "independent permission gates" in sp
    assert "no claude code" in sp
    # and the injection tools are NOT granted here (deferred to Tier-H)
    m = loaded.specs["local-agent"].manifest
    assert m.tools == frozenset({"fs_read", "fs_list", "fs_write"})
    assert "spawn_peer" not in m.tools and "send_role_message" not in m.tools


def test_local_agent_runs_end_to_end_observe_and_report(tmp_path):
    """The real package team: a stand-in local model lists its workspace, reads
    a real SelfConnect artifact, and writes a grounded observation — proving the
    CC-free agent operates on SCR with real tools. Observation goes to OUTPUT,
    never the workspace."""
    ws = tmp_path / "ws"; ws.mkdir()
    out = tmp_path / "out"; out.mkdir()
    (ws / "self_connect.py").write_text("# SelfConnect Win32 SDK\ndef send_string(): ...\n")
    (ws / "mesh_config.py").write_text("ROLES = ['a', 'b']\n")

    loaded = load_team_from_dir(PKG_SRC, str(ws), str(out))
    store = Store(":memory:")
    report = str(out / "observation.md")

    class _Stand:
        def __init__(self): self.n = 0
        def complete(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return ModelResponse("", (ToolCall("l", "fs_list", {"path": str(ws)}),))
            if self.n == 2:
                return ModelResponse("", (ToolCall("r", "fs_read",
                    {"path": str(ws / "self_connect.py")}),))
            if self.n == 3:
                return ModelResponse("", (ToolCall("w", "fs_write",
                    {"path": report,
                     "content": "local agent observation complete\n"
                                "read self_connect.py and mesh_config.py"}),))
            return ModelResponse("local agent observation complete: 2 files observed")

    sb = SandboxRunner(tmp_dir=str(tmp_path / "sbtmp"))
    runner = TeamRunner(store, loaded, lambda a: _Stand(),
                        lambda m: build_native_tools(m, sb), sandbox=sb)
    res = runner.run("sce.local-agent", "observe the workspace")
    assert res.stopped_reason == "completed"
    assert "local agent observation complete" in res.final_text
    assert os.path.exists(report)                      # written to OUTPUT
    assert not (ws / "observation.md").exists()        # NOT into the workspace
    ev = [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq",
        (res.session_id,)).fetchall()]
    reads = [e for e in ev if e.get("type") == "tool_exec" and e.get("tool") == "fs_read"]
    assert reads and reads[0]["path"].endswith("self_connect.py")   # grounded read in chain
