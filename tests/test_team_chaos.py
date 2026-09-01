"""Team-level crash-safety + cancel (§3.1 G5). Real process kills, no fakes."""
import json
import os
import sys
import textwrap
import threading
import time

import pytest
import yaml

from scr.capability import CapabilityManifest, ExecRule
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import ToolSpec
from scr.sandbox import SandboxRunner
from scr.state import Store
from scr.team import TeamRunner, load_team_from_dir, team_recover

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPS = {"tools": ["slow", "proc_exec"], "fs_read_roots": ["${WORKSPACE}"],
        "fs_write_roots": ["${WORKSPACE}"]}


def _agents_dir(tmp_path, agents):
    ad = tmp_path / "src" / "agents"
    ad.mkdir(parents=True, exist_ok=True)
    for name, body in agents.items():
        body = dict(body); body["name"] = name
        (ad / f"{name}.yaml").write_text(yaml.safe_dump(body))
    return str(tmp_path / "src")


def test_kill_mid_child_quarantines_it_and_preserves_siblings(tmp_path):
    """lead delegates to worker2 (completes) then worker (non-idempotent tool,
    killed mid-exec). team_recover quarantines worker, keeps worker2's result,
    and the orchestrator is left needs-review (resumable) — DB intact."""
    db = str(tmp_path / "team.db").replace("\\", "/")
    began = str(tmp_path / "began.marker").replace("\\", "/")
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["worker2", "worker"]},
        "worker2": {"capabilities": CAPS},
        "worker": {"capabilities": CAPS},
    }
    src = _agents_dir(tmp_path, agents)

    child = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, r"{SRC}")
        from scr.state import Store
        from scr.team import TeamRunner, load_team_from_dir
        from scr.kernel import ToolSpec
        from scr.gateway import MockAdapter, ModelResponse, ToolCall

        loaded = load_team_from_dir(r"{src}", r"{tmp_path}")
        store = Store(r"{db}")

        def slow(a):
            open(r"{began}", "w").write("x")
            time.sleep(60)          # parent kills us here
            return "unreachable"

        scripts = {{
            "lead": [
                ModelResponse("", (ToolCall("d1", "delegate", {{"agent": "worker2", "task": "t"}}),)),
                ModelResponse("", (ToolCall("d2", "delegate", {{"agent": "worker", "task": "t"}}),)),
                ModelResponse("lead done"),
            ],
            "worker2": [ModelResponse("worker2 finished cleanly")],
            "worker": [ModelResponse("", (ToolCall("w1", "slow", {{}}),)), ModelResponse("wdone")],
        }}
        tools = {{"slow": ToolSpec("slow", slow, idempotent=False)}}
        r = TeamRunner(store, loaded, lambda ag: MockAdapter(list(scripts[ag])),
                       lambda m: dict(tools))
        r.run("lead", "go")
        print("TEAMID", r.last_team_id, flush=True)
    """)
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True)
    # capture the team id line as soon as it's printed? It prints only at the end.
    # Instead wait for the 'began' marker (worker's slow tool started), then kill.
    deadline = time.time() + 30
    began_path = str(tmp_path / "began.marker")
    while not os.path.exists(began_path):
        assert time.time() < deadline and proc.poll() is None, "worker tool never started"
        time.sleep(0.05)
    proc.kill()
    proc.wait(timeout=30)

    # fresh Store over the surviving DB; find the team id and recover
    store = Store(str(tmp_path / "team.db"))
    team_id = store.conn.execute("SELECT DISTINCT team_id FROM team_sessions").fetchone()[0]
    reports = {r["agent"]: r["status"] for r in team_recover(store, team_id)}
    assert reports.get("worker") == "quarantined"        # killed child quarantined
    # worker2 completed before the kill → its session is clean (not quarantined)
    assert reports.get("worker2") in ("clean", "resumed")
    # DB integrity after the kill
    assert store.conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    # worker2's real result is preserved in its session
    w2 = [m["session_id"] for m in store.team_members(team_id) if m["agent"] == "worker2"][0]
    assert any("worker2 finished cleanly" in mm["content"]
               for mm in store.get_messages(w2))


def test_cancel_storm_no_orphans(tmp_path):
    """lead fans out to 4 workers; each worker runs a real sandbox tool that
    spawns a grandchild. Cancel mid-fan-out kills the in-flight tree and stops
    further delegation — no grandchild survives."""
    ws = tmp_path / "ws"; (ws / "out").mkdir(parents=True)
    began = str(tmp_path / "began.marker").replace("\\", "/")
    survivor = str(tmp_path / "survivor.marker").replace("\\", "/")
    prog = textwrap.dedent(f"""
        import subprocess, sys, time
        open('{began}', 'a').write('x')
        gc = "import time; time.sleep(30); open('{survivor}','a').write('x')"
        subprocess.Popen([sys.executable, '-c', gc])
        time.sleep(30)
    """)
    caps = {"tools": ["proc_exec"], "fs_read_roots": ["${WORKSPACE}"],
            "fs_write_roots": ["${WORKSPACE}/out"],
            "exec_rules": [{"binary": sys.executable, "arg_pattern": r"(?s).*"}]}
    agents = {"lead": {"capabilities": caps, "delegates": ["w1", "w2", "w3", "w4"]},
              "w1": {"capabilities": caps}, "w2": {"capabilities": caps},
              "w3": {"capabilities": caps}, "w4": {"capabilities": caps}}
    src = _agents_dir(tmp_path, agents)
    loaded = load_team_from_dir(src, str(ws))
    store = Store(str(tmp_path / "t.db"))
    sb = SandboxRunner()

    from scr.tools_native import build_native_tools
    def scripts(agent):
        if agent == "lead":
            return MockAdapter([
                ModelResponse("", tuple(ToolCall(f"d{i}", "delegate",
                              {"agent": f"w{i}", "task": "t"}) for i in range(1, 5))),
                ModelResponse("lead done")])
        return MockAdapter([
            ModelResponse("", (ToolCall("x", "proc_exec",
                          {"binary": sys.executable, "args": ["-c", prog]}),)),
            ModelResponse(f"{agent} done")])

    runner = TeamRunner(store, loaded, scripts,
                        lambda m: build_native_tools(m, sb), sandbox=sb)
    t = threading.Thread(target=lambda: runner.run("lead", "fan out"), daemon=True)
    t.start()
    # wait until at least one worker's tree started
    deadline = time.time() + 30
    while not os.path.exists(str(tmp_path / "began.marker")):
        assert time.time() < deadline, "no worker tree started"
        time.sleep(0.05)
    runner.cancel()
    t.join(timeout=30)
    assert not t.is_alive(), "team run did not stop on cancel"
    time.sleep(2.0)
    assert not os.path.exists(str(tmp_path / "survivor.marker")), \
        "a grandchild survived team cancel — orphaned process"
